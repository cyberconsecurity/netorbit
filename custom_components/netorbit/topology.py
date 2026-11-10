"""Work out the network layout from switch MAC tables. Pure Python, no Home Assistant imports.

A managed switch remembers which port it last saw each MAC address on. With the
tables from one or more switches we can tell:

* which switch (and port) every device hangs off,
* which hub sits on that same port (an access point, a mesh node, another switch),
* how the switches and hubs chain together back to the router.

The rule for "nearest" is simple: a switch S is the nearest switch to MAC m when no
other switch with a table sits between S and m on the same port.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Topology:
    """Result of the inference."""

    hub_parent: dict[str, str] = field(default_factory=dict)
    device_hub: dict[str, str] = field(default_factory=dict)


def macs_close(a: str | None, b: str | None, spread: int = 16) -> bool:
    """True when two MACs are the same device's base MAC and a radio (BSSID) MAC.

    Access points often report clients against a radio MAC that differs from the
    LAN MAC only in the last byte, or in a locally administered first byte.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    pa, pb = a.split(":"), b.split(":")
    if len(pa) != 6 or len(pb) != 6:
        return False
    if pa[1:5] == pb[1:5] and abs(int(pa[5], 16) - int(pb[5], 16)) <= spread:
        first_a, first_b = int(pa[0], 16), int(pb[0], 16)
        return first_a == first_b or (first_a | 0x02) == (first_b | 0x02)
    return False


def infer(
    tables: dict[str, dict[str, int]],
    hub_macs: dict[str, str],
    gateway_mac: str | None,
    device_macs: dict[str, str],
    root: str,
    leaf_hubs: set[str] | None = None,
    hub_parents: dict[str, str] | None = None,
) -> Topology:
    """Infer hub parents and device placement.

    tables: switch hub key -> {mac: port}
    hub_macs: hub key -> mac (switches and other hubs)
    gateway_mac: the router's MAC, used to find each switch's uplink port
    device_macs: device key -> mac
    root: the router's hub key
    leaf_hubs: hubs that devices can sit behind (access points, mesh nodes). Defaults
               to every hub without a table of its own.
    hub_parents: parents a person has locked, hub key -> parent hub key. When several
                 leaf hubs share one switch port (a mesh node bridging through an
                 access point), the port's devices go to the hub the others hang from.
    """
    result = Topology()
    if not tables:
        return result
    leaf_hubs = set(hub_macs) - set(tables) if leaf_hubs is None else leaf_hubs
    hub_parents = hub_parents or {}

    uplink: dict[str, int | None] = {}
    for sw, table in tables.items():
        uplink[sw] = table.get(gateway_mac) if gateway_mac and sw != root else None

    def downstream_port(sw: str, mac: str) -> int | None:
        port = tables[sw].get(mac)
        if port is None or port == 0 or port == uplink[sw]:
            return None
        return port

    def locate(mac: str, exclude: str | None = None) -> tuple[str, int] | None:
        candidates = [(sw, p) for sw in tables if sw != exclude and (p := downstream_port(sw, mac)) is not None]
        for sw, port in candidates:
            blocked = False
            for other, _ in candidates:
                if other == sw:
                    continue
                other_mac = hub_macs.get(other)
                if other_mac and tables[sw].get(other_mac) == port:
                    blocked = True
                    break
            if not blocked:
                return sw, port
        return candidates[0] if candidates else None

    hub_location: dict[str, tuple[str, int]] = {}
    for hub, mac in hub_macs.items():
        if hub == root or not mac:
            continue
        loc = locate(mac, exclude=hub)
        if loc:
            hub_location[hub] = loc
            result.hub_parent[hub] = loc[0]

    # Switches whose router MAC sits on a port facing another switch hang off that switch;
    # anything still unplaced hangs off the router.
    for sw in tables:
        if sw != root and sw not in result.hub_parent:
            result.hub_parent[sw] = root

    by_port: dict[tuple[str, int], list[str]] = {}
    for hub, loc in hub_location.items():
        if hub in leaf_hubs:
            by_port.setdefault(loc, []).append(hub)

    top = {sw for sw in tables if result.hub_parent.get(sw) == root}

    def behind_another(hub: str, here: set[str]) -> bool:
        """True when the chain of locked parents from hub reaches another hub on the same port."""
        seen = {hub}
        parent = hub_parents.get(hub)
        while parent and parent not in seen:
            if parent in here:
                return True
            seen.add(parent)
            parent = hub_parents.get(parent)
        return False

    # Several leaf hubs on one port usually means one bridges through another: a mesh
    # node whose uplink is an access point shows up on the access point's port, along
    # with every client of both. The table cannot tell them apart, but a locked parent
    # says which hangs from which: keep only the hubs the others hang from, and record
    # that parent so the inference agrees with the drawing.
    for loc, hubs in list(by_port.items()):
        if len(hubs) < 2:
            continue
        here = set(hubs)
        for hub in hubs:
            parent = hub_parents.get(hub)
            if parent and parent != hub and parent in here:
                result.hub_parent[hub] = parent
        by_port[loc] = [hub for hub in hubs if not behind_another(hub, here)]

    known_hub_macs = {m for m in hub_macs.values() if m}
    for key, mac in device_macs.items():
        if not mac or mac in known_hub_macs:
            continue
        loc = locate(mac)
        if not loc:
            # A switch plugged straight into the router sees it on its router-facing port, and
            # no switch sees it anywhere else: it's plugged into the router (or into something
            # unmanaged hanging off it), or on the router's own Wi-Fi. Only switches that hang
            # off the router count. A switch further down only knows the device is "somewhere
            # upstream", which is true of every device behind the first switch too.
            if any(sw in top and uplink[sw] is not None and tables[sw].get(mac) == uplink[sw] for sw in tables):
                result.device_hub[key] = root
            continue
        hubs_here = by_port.get(loc, [])
        result.device_hub[key] = hubs_here[0] if len(hubs_here) == 1 else loc[0]
    return result


def switch_ports(
    table: dict[str, int],
    own_mac: str | None,
    parent_mac: str | None,
    gateway_mac: str | None,
    children: dict[str, tuple[str, str]],
    info: dict[int, dict] | None = None,
    uplink: int | None = None,
    pinned: dict[int, list[str]] | None = None,
) -> list[dict]:
    """What each port of one switch leads to, for the card's port view. Pure.

    table: the switch's {mac: port}
    own_mac: the switch's own MAC (its management address can show up on a port of its own)
    parent_mac / gateway_mac: the hub above it and the router, used to find the uplink port
    children: node key -> (mac, kind) for every hub and device the map places right under this switch
    info: {port: {"name", "alias", "speed", "up"}} from the switch, when it publishes it
    uplink: the uplink port when something other than the table says which it is (a FortiGate for its FortiSwitches)
    pinned: {port: [node keys]} known to be on a port without a MAC in the table (a FortiSwitch below another)

    Returns one dict per port, in port order: port number, name, alias, speed (Mb/s), up, whether it is
    the uplink, the node keys plugged into it (hubs first) and how many MAC addresses it has learned.
    A port with many MACs and no child usually has something unmanaged behind it.
    """
    info = info or {}
    for mac in (parent_mac, gateway_mac):
        if uplink is None and mac and table.get(mac):
            uplink = table[mac]
    learned: dict[int, int] = {}
    for mac, port in table.items():
        if port and mac != own_mac:
            learned[port] = learned.get(port, 0) + 1
    on_port: dict[int, list[tuple[int, str]]] = {}
    for key, (mac, kind) in children.items():
        port = table.get(mac) if mac else None
        if not port or port == uplink:
            continue
        on_port.setdefault(port, []).append((0 if kind == "hub" else 1, key))
    for port, keys in (pinned or {}).items():
        for key in keys:
            if port != uplink and all(k != key for _r, k in on_port.get(port, [])):
                on_port.setdefault(port, []).append((0, key))
    numbers = sorted(set(info) | set(learned) | set(on_port) | ({uplink} if uplink else set()))
    out: list[dict] = []
    for port in numbers:
        meta = info.get(port) or {}
        out.append({
            "port": port,
            "name": meta.get("name"),
            "alias": meta.get("alias"),
            "speed": meta.get("speed"),
            "up": meta.get("up"),
            "uplink": port == uplink,
            "nodes": [key for _rank, key in sorted(on_port.get(port, []))],
            "macs": learned.get(port, 0),
        })
    return out
