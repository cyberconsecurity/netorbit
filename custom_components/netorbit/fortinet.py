"""FortiSwitches managed by a FortiGate (FortiLink), read from the FortiGate's REST API.

FortiLink-managed switches don't answer SNMP, and the FortiGate doesn't pass their MAC tables through SNMP either,
so NetOrbit asks the FortiGate. Two read-only monitor endpoints are enough:

* ``/api/v2/monitor/switch-controller/managed-switch/status``: the switches, their ports (link state, speed) and
  how they connect: a port with ``fortilink_port`` leads to the FortiGate, a port with ``isl_peer_device_name``
  leads to another switch.
* ``/api/v2/monitor/switch-controller/detected-device``: every MAC address with the switch and port it was seen on.

Pure: no Home Assistant imports, so it is easy to test. The HTTP calls live in the coordinator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .logic import normalize_mac

STATUS_PATH = "/api/v2/monitor/switch-controller/managed-switch/status"
DEVICES_PATH = "/api/v2/monitor/switch-controller/detected-device"

_PORT_RE = re.compile(r"^port(\d+)$", re.IGNORECASE)


@dataclass
class FortiSwitch:
    serial: str
    name: str
    model: str | None = None
    ip: str | None = None
    os_version: str | None = None
    connected: bool = True
    parent: str | None = None  # serial of the switch above it; None when it hangs off the FortiGate itself
    uplink: int | None = None  # the port leading towards the FortiGate
    down: dict[int, str] = field(default_factory=dict)  # port -> serial of a switch below it on that port
    ports: dict[int, dict[str, Any]] = field(default_factory=dict)  # port -> {"name", "speed", "up"}
    table: dict[str, int] = field(default_factory=dict)  # mac -> edge port


def port_number(name: Any) -> int | None:
    """"port23" -> 23. Other interfaces (trunks, internal) have no number."""
    m = _PORT_RE.match(str(name or "").strip())
    return int(m.group(1)) if m else None


def _results(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("results")
    return [r for r in payload or [] if isinstance(r, dict)] if isinstance(payload, list) else []


def switch_name(serial: str, switch_id: str | None, model: str | None) -> str:
    """The name someone gave the switch on the FortiGate, or "FortiSwitch 124EF-2440" when it is still its serial."""
    if switch_id and switch_id != serial:
        return switch_id
    short = (model or "")[1:] if (model or "").upper().startswith("S") else (model or "")
    return f"FortiSwitch {short}-{serial[-4:]}" if short else f"FortiSwitch {serial[-4:]}"


def parse_fortiswitches(status: Any, devices: Any) -> dict[str, FortiSwitch]:
    """{serial: FortiSwitch} from the two API responses, with parents worked out along the FortiLink and ISL links."""
    switches: dict[str, FortiSwitch] = {}
    links: dict[str, dict[int, str]] = {}  # serial -> {port: peer serial} over ISL
    fortilink: dict[str, int] = {}  # serial -> its FortiLink port
    for sw in _results(status):
        serial = str(sw.get("serial") or sw.get("switch-id") or "").strip()
        if not serial:
            continue
        version = str(sw.get("os_version") or "")
        model = version.split("-", 1)[0] or None if version else None
        fs = FortiSwitch(
            serial=serial,
            name=switch_name(serial, sw.get("switch-id"), model),
            model=model,
            ip=sw.get("connecting_from") or None,
            os_version=version or None,
            connected=str(sw.get("status") or "").lower() == "connected",
        )
        for port in sw.get("ports") or []:
            if not isinstance(port, dict):
                continue
            n = port_number(port.get("interface"))
            if n is None:
                continue
            up = str(port.get("status") or "").lower() == "up"
            speed = port.get("speed")
            fs.ports[n] = {"name": port.get("interface"), "speed": speed if up and isinstance(speed, int) and speed > 0 else None, "up": up}
            if port.get("fortilink_port"):
                fortilink.setdefault(serial, n)
            peer = str(port.get("isl_peer_device_name") or "").strip()
            if peer:
                links.setdefault(serial, {})[n] = peer
        switches[serial] = fs

    # Parents: switches with a FortiLink port hang off the FortiGate; the rest off the ISL neighbour one step closer.
    level: dict[str, int] = {}
    queue = [s for s in switches if s in fortilink]
    for s in queue:
        level[s] = 0
        switches[s].uplink = fortilink[s]
    while queue:
        nxt = []
        for s in queue:
            for port, peer in (links.get(s) or {}).items():
                if peer not in switches or peer in level:
                    continue
                level[peer] = level[s] + 1
                switches[peer].parent = s
                switches[s].down[port] = peer
                back = [p for p, other in (links.get(peer) or {}).items() if other == s]
                switches[peer].uplink = back[0] if back else None
                nxt.append(peer)
        queue = nxt

    # MAC tables, edge ports only: what is learned over FortiLink or ISL belongs to the switch at the far end.
    for row in _results(devices):
        fs = switches.get(str(row.get("switch_id") or ""))
        mac = normalize_mac(row.get("mac"))
        if fs is None or not mac:
            continue
        port = row.get("port_id") if isinstance(row.get("port_id"), int) else port_number(row.get("port_name"))
        if not port or port == fs.uplink or port in fs.down or port == fortilink.get(fs.serial):
            continue
        fs.table[mac] = port
    return switches


INTERFACES_PATH = "/api/v2/monitor/system/interface"
LLDP_PATH = "/api/v2/monitor/network/lldp/neighbors"

_SHORT = (("internal", "int"), ("port", "p"), ("wan", "wan"), ("dmz", "dmz"))


def port_label(name: str) -> str:
    """A short label for a FortiGate port that fits a port button: "internal3" -> "int3", "wan1" stays."""
    low = name.lower()
    for long, short in _SHORT:
        if low.startswith(long) and low[len(long):].isdigit():
            return short + low[len(long):]
    return name[:5]


def parse_fortigate_ports(interfaces: Any, lldp: Any) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """The FortiGate's own physical ports and the LLDP neighbours heard on them.

    Returns ({n: {"name", "label", "alias", "speed", "up"}}, [{"port": n, "port_name", "system_name", "mac",
    "chassis", "ip"}]). Ports are numbered 1.. in the order the FortiGate lists them, and only physical ones are kept:
    VLANs, tunnels and the modem report an all-zero MAC.
    """
    rows = interfaces.get("results") if isinstance(interfaces, dict) else None
    ports: dict[int, dict[str, Any]] = {}
    by_name: dict[str, int] = {}
    if isinstance(rows, dict):
        for name, row in rows.items():
            if not isinstance(row, dict):
                continue
            mac = normalize_mac(row.get("mac"))
            if not mac or mac == "00:00:00:00:00:00":
                continue
            n = len(ports) + 1
            up = bool(row.get("link"))
            speed = row.get("speed")
            alias = str(row.get("alias") or "").strip().strip("()") or None
            ports[n] = {"name": str(name), "label": port_label(str(name)), "alias": alias,
                        "speed": int(speed) if up and isinstance(speed, (int, float)) and speed > 0 else None, "up": up}
            by_name[str(name)] = n
    neighbors: list[dict[str, Any]] = []
    for row in _results(lldp):
        n = by_name.get(str(row.get("port_name") or ""))
        if n is None:
            continue
        ip = next((a.get("address") for a in row.get("addresses") or [] if isinstance(a, dict) and a.get("type") == "ipv4"), None)
        neighbors.append({"port": n, "port_name": ports[n]["name"], "system_name": str(row.get("system_name") or "").strip() or None,
                          "mac": normalize_mac(row.get("mac")), "chassis": normalize_mac(row.get("chassis_id")), "ip": ip})
    return ports, neighbors


# FortiOS 7 has the ARP table under network/; some older releases had it under system/. Tried in this order.
ARP_PATH = "/api/v2/monitor/network/arp"
ARP_PATHS = (ARP_PATH, "/api/v2/monitor/system/arp")


def parse_fortigate_arp(payload: Any) -> dict[str, tuple[str, str | None]]:
    """{IP: (MAC, interface)} from the FortiGate's ARP table: every device it talks to, and the interface it heard it
    on. Fills in MACs for subnets Home Assistant only reaches through the FortiGate, and says which FortiGate port a
    device on a routed port is behind."""
    out: dict[str, tuple[str, str | None]] = {}
    for row in _results(payload):
        ip = str(row.get("ip") or "").strip()
        mac = normalize_mac(row.get("mac"))
        if not ip or not mac or mac in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"):
            continue
        out[ip] = (mac, str(row.get("interface") or row.get("intf") or "").strip() or None)
    return out
