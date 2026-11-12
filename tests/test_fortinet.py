"""FortiSwitches behind a FortiGate (FortiLink): hubs, placement and ports from the FortiGate's REST API.

The payloads mirror FortiOS 7.4 responses, with made-up serials and documentation addresses.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.netorbit.diagnostics import async_get_config_entry_diagnostics
from custom_components.netorbit.fortinet import (
    DEVICES_PATH,
    INTERFACES_PATH,
    LLDP_PATH,
    STATUS_PATH,
    parse_fortigate_ports,
    parse_fortiswitches,
    port_label,
    port_number,
    switch_name,
)

from .test_auto_and_tools import SW_MAC, _network
from .test_integration import _entry, _node_states, _setup

CORE = "S124EF0000000001"
EDGE = "S108EP0000000002"
FGT = "FGT60F0000000000"
LAB_MAC = "00:00:5e:00:53:b0"  # a machine on the core FortiSwitch, port 7
CAM_MAC = "00:00:5e:00:53:b1"  # a camera on the edge FortiSwitch, port 3
TOKEN = "example-token-not-real"


def _port(n: int, up: bool, speed: int = 0, **extra) -> dict:
    return {"interface": f"port{n}", "fortilink_port": False, "isl_peer_device_name": "", "isl_peer_port_name": "",
            "status": "up" if up else "down", "speed": speed if up else 0, **extra}


STATUS = {"http_method": "GET", "status": "success", "results": [
    {"serial": CORE, "switch-id": CORE, "status": "Connected", "os_version": "S124EF-v7.6.8-build1164,260709 (GA)",
     "connecting_from": "192.0.2.240", "ports": [
         _port(1, True, 100), _port(2, False), _port(7, True, 1000),
         _port(23, True, 1000, isl_peer_device_name=EDGE, isl_peer_port_name="port8"),
         _port(24, True, 1000, fortilink_port=True, fgt_peer_device_name=FGT, fgt_peer_port_name="FortiLink"),
     ]},
    {"serial": EDGE, "switch-id": "Garage FSW", "status": "Connected", "os_version": "S108EP-v7.4.9-build946,260122 (GA)",
     "connecting_from": "192.0.2.241", "ports": [
         _port(3, True, 100), _port(4, False),
         _port(8, True, 1000, isl_peer_device_name=CORE, isl_peer_port_name="port23"),
     ]},
]}
DEVICES = {"http_method": "GET", "status": "success", "results": [
    {"mac": LAB_MAC, "port_name": "port7", "switch_id": CORE, "vlan_id": 1, "port_id": 7, "last_seen": 39},
    {"mac": CAM_MAC, "port_name": "port3", "switch_id": EDGE, "vlan_id": 1, "port_id": 3, "last_seen": 12},
    # Learned over the ISL and the FortiLink: belongs to the switch at the far end, so it is dropped here.
    {"mac": CAM_MAC, "port_name": "port23", "switch_id": CORE, "vlan_id": 1, "port_id": 23, "last_seen": 12},
    {"mac": LAB_MAC, "port_name": "port8", "switch_id": EDGE, "vlan_id": 1, "port_id": 8, "last_seen": 39},
]}


def test_parse_builds_the_chain_tables_and_ports() -> None:
    sw = parse_fortiswitches(STATUS, DEVICES)
    core, edge = sw[CORE], sw[EDGE]
    assert core.parent is None and core.uplink == 24 and core.down == {23: EDGE}
    assert edge.parent == CORE and edge.uplink == 8 and edge.down == {}
    assert core.table == {LAB_MAC: 7} and edge.table == {CAM_MAC: 3}
    assert core.ports[1] == {"name": "port1", "speed": 100, "up": True}
    assert core.ports[2]["up"] is False and core.ports[2]["speed"] is None
    assert core.name == "FortiSwitch 124EF-0001" and edge.name == "Garage FSW"
    assert core.ip == "192.0.2.240" and core.model == "S124EF"


def test_parse_helpers_and_bad_input() -> None:
    assert port_number("port23") == 23 and port_number("internal") is None and port_number(None) is None
    assert switch_name("S108EP0000000002", "S108EP0000000002", "S108EP") == "FortiSwitch 108EP-0002"
    assert parse_fortiswitches(None, None) == {}
    assert parse_fortiswitches({"results": "nonsense"}, {"results": [None, 3]}) == {}


def _phys(mac: str, up: bool = True, speed: float = 1000.0, alias: str = "") -> dict:
    return {"mac": mac, "link": up, "speed": speed if up else 0.0, "alias": alias}


INTERFACES = {"http_method": "GET", "status": "success", "results": {
    "wan1": _phys("00:00:5e:00:53:e1"),
    "wan2": _phys("00:00:5e:00:53:e2", up=False, alias="(Management_Use)"),
    "internal1": _phys("00:00:5e:00:53:e3"),
    "internal2": _phys("00:00:5e:00:53:e4", speed=100.0),
    "a": _phys("00:00:5e:00:53:e5", alias="(Backup WAN)"),
    "b": _phys("00:00:5e:00:53:e6"),
    "modem": _phys("00:00:00:00:00:00", up=False),
    "LabVLAN": _phys("00:00:00:00:00:00"),  # a VLAN: no MAC of its own, not a port
}}
LLDP = {"http_method": "GET", "status": "success", "results": [
    {"mac": "00:00:5e:00:53:c2", "chassis_id": "00:00:5E:00:53:C1", "port_name": "b", "port_id": "port24",
     "system_name": CORE, "addresses": [{"type": "ipv4", "address": "192.0.2.240"}]},
    # The UniFi-style core switch from the shared test network, heard on internal2 by its MAC.
    {"mac": SW_MAC, "chassis_id": SW_MAC, "port_name": "internal2", "port_id": "Port 1", "system_name": "core-switch"},
    {"mac": "00:00:5e:00:53:cf", "chassis_id": "00:00:5e:00:53:cf", "port_name": "nonexistent", "system_name": "x"},
]}


def test_parse_fortigate_ports_keeps_physical_ports_and_matches_lldp() -> None:
    ports, nbs = parse_fortigate_ports(INTERFACES, LLDP)
    assert [p["name"] for p in ports.values()] == ["wan1", "wan2", "internal1", "internal2", "a", "b"]
    by = {p["name"]: (n, p) for n, p in ports.items()}
    assert by["internal2"][1] == {"name": "internal2", "label": "int2", "alias": None, "speed": 100, "up": True}
    assert by["wan2"][1]["up"] is False and by["wan2"][1]["speed"] is None and by["wan2"][1]["alias"] == "Management_Use"
    assert [(nb["port_name"], nb["system_name"]) for nb in nbs] == [("b", CORE), ("internal2", "core-switch")]
    assert nbs[0]["ip"] == "192.0.2.240" and nbs[0]["port"] == by["b"][0]
    assert port_label("internal3") == "int3" and port_label("wan1") == "wan1" and port_label("b") == "b"
    assert parse_fortigate_ports(None, None) == ({}, [])


def _fortigate(fail: bool = False, ports: bool = True):
    async def get(_self, path):
        if fail:
            raise OSError("unreachable")
        if path in (INTERFACES_PATH, LLDP_PATH) and not ports:
            raise PermissionError("403: the token's profile has no Network read")
        return {STATUS_PATH: STATUS, DEVICES_PATH: DEVICES, INTERFACES_PATH: INTERFACES, LLDP_PATH: LLDP}[path]
    return patch("custom_components.netorbit.coordinator.NetOrbitCoordinator._fortigate_get", autospec=True, side_effect=get)


def _lab(fake_net) -> None:
    _network(fake_net)
    fake_net.alive |= {"192.0.2.30", "192.0.2.31"}
    fake_net.arp["192.0.2.30"] = LAB_MAC
    fake_net.arp["192.0.2.31"] = CAM_MAC
    fake_net.dns["192.0.2.30"] = "lab-host.lan"
    fake_net.dns["192.0.2.31"] = "garage-cam.lan"


async def _run(hass, entry) -> None:
    await _setup(hass, entry)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_fortiswitches_become_hubs_with_devices_and_ports(hass: HomeAssistant, fake_net) -> None:
    _lab(fake_net)
    entry = _entry("auto", fortigate_host="192.0.2.1", fortigate_token=TOKEN)
    with _fortigate():
        await _run(hass, entry)
    hubs = {h.get("forti_serial"): h for h in entry.options["hubs"] if h.get("forti_serial")}
    assert hubs[CORE]["parent"] == "gateway" and hubs[CORE]["parent_locked"] and hubs[CORE]["type"] == "switch"
    assert hubs[EDGE]["parent"] == hubs[CORE]["id"] and hubs[EDGE]["parent_locked"]
    assert hubs[CORE]["fields"]["Management IP"] == "192.0.2.240"

    nodes = _node_states(hass)
    lab, cam = nodes["lab-host"].attributes, nodes["garage-cam"].attributes
    assert lab["link"] == f"hub:{hubs[CORE]['id']}" and lab["placement"] == "fortigate" and lab["port"] == 7 and lab["port_speed"] == 1000
    assert cam["link"] == f"hub:{hubs[EDGE]['id']}" and cam["port"] == 3 and cam["port_speed"] == 100
    core = next(n.attributes for n in nodes.values() if n.attributes.get("node_key") == f"hub:{hubs[CORE]['id']}")
    ports = {p["port"]: p for p in core["ports"]}
    assert ports[24]["uplink"] is True
    assert ports[23]["nodes"] == [f"hub:{hubs[EDGE]['id']}"] and ports[23]["speed"] == 1000
    assert ports[2]["up"] is False
    # The rest of the network is untouched.
    assert nodes["nas"].attributes["link"] == "hub:core_switch"


async def test_a_failed_read_keeps_the_last_good_one(hass: HomeAssistant, fake_net) -> None:
    _lab(fake_net)
    entry = _entry("auto", fortigate_host="192.0.2.1", fortigate_token=TOKEN)
    with _fortigate():
        await _run(hass, entry)
    with _fortigate(fail=True):
        entry.runtime_data.request_sweep()
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
    assert _node_states(hass)["lab-host"].attributes["placement"] == "fortigate"


async def test_a_removed_fortiswitch_stays_removed(hass: HomeAssistant, fake_net) -> None:
    _lab(fake_net)
    entry = _entry("auto", fortigate_host="192.0.2.1", fortigate_token=TOKEN)
    with _fortigate():
        await _run(hass, entry)
        edge = next(h["id"] for h in entry.options["hubs"] if h.get("forti_serial") == EDGE)
        await hass.services.async_call("netorbit", "remove_hub", {"hub": edge}, blocking=True)
        await hass.async_block_till_done()
        entry.runtime_data.request_sweep()
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
    assert EDGE not in {h.get("forti_serial") for h in entry.options["hubs"]}
    assert f"fortiswitch:{EDGE}" in entry.options["dismissed_hubs"]


async def test_no_fortigate_no_requests_and_the_token_stays_out_of_diagnostics(hass: HomeAssistant, fake_net) -> None:
    _lab(fake_net)
    entry = _entry("auto")
    with _fortigate() as get:
        await _run(hass, entry)
    assert not get.called
    assert not any(h.get("forti_serial") for h in entry.options["hubs"])

    entry2 = _entry("auto", fortigate_host="192.0.2.1", fortigate_token=TOKEN)
    entry2.add_to_hass(hass)
    entry2.runtime_data = entry.runtime_data
    diag = await async_get_config_entry_diagnostics(hass, entry2)
    assert TOKEN not in str(diag)
    assert diag["fortigate"]["configured"] is True


async def test_settings_keep_the_token_until_the_address_is_cleared(hass: HomeAssistant, fake_net) -> None:
    _network(fake_net)
    entry = _entry("auto")
    await _setup(hass, entry)
    base = {"mode": "auto", "ping_interval": 60, "discovery_interval": 300, "stale_hours": 24, "ignore_random_mac": True,
            "auto_hubs": True, "group_by_type": True, "check_internet": True, "prefer_dns_names": False, "snmp_enabled": True}

    async def submit(**fields):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "settings"})
        await hass.config_entries.options.async_configure(flow["flow_id"], {**base, **fields})
        await hass.async_block_till_done()

    with _fortigate():
        await submit(fortigate_host="192.0.2.1", fortigate_token=TOKEN)
        assert entry.options["fortigate_token"] == TOKEN
        await submit(fortigate_host="192.0.2.1:8443")  # token box left empty: the saved one stays
        assert entry.options["fortigate_token"] == TOKEN and entry.options["fortigate_host"] == "192.0.2.1:8443"
        await submit(fortigate_host="")  # address cleared: FortiGate off, token forgotten
        assert entry.options["fortigate_token"] == "" and entry.options["fortigate_host"] == ""


async def test_diagnostics_leave_out_addresses_and_secrets(hass: HomeAssistant, fake_net) -> None:
    """The settings section of diagnostics never carries the FortiGate's address or token, nor other subnets."""
    _lab(fake_net)
    entry = _entry("auto", fortigate_host="192.0.2.1:8443", fortigate_token=TOKEN, extra_subnets=["198.51.100.0/24"])
    with _fortigate():
        await _run(hass, entry)
    text = str(await async_get_config_entry_diagnostics(hass, entry))
    for secret in (TOKEN, "192.0.2.1:8443", "198.51.100.0", "fortigate_token", "fortigate_host", "extra_subnets'"):
        assert secret not in text, secret


async def test_the_router_gets_the_fortigate_ports_and_switches_learn_theirs(hass: HomeAssistant, fake_net) -> None:
    _lab(fake_net)
    entry = _entry("auto", fortigate_host="192.0.2.1", fortigate_token=TOKEN)
    with _fortigate():
        await _run(hass, entry)
    nodes = _node_states(hass)
    core_id = next(h["id"] for h in entry.options["hubs"] if h.get("forti_serial") == CORE)
    router = next(n.attributes for n in nodes.values() if n.attributes.get("node_key") == "hub:gateway")
    ports = {p["name"]: p for p in router["ports"]}
    assert set(ports) == {"wan1", "wan2", "internal1", "internal2", "a", "b"}
    assert ports["b"]["nodes"] == [f"hub:{core_id}"] and ports["b"]["label"] == "b"
    assert ports["internal2"]["nodes"] == ["hub:core_switch"] and ports["internal2"]["speed"] == 100
    assert ports["wan1"]["uplink"] is True and ports["wan2"]["uplink"] is False
    fsw = next(n.attributes for n in nodes.values() if n.attributes.get("node_key") == f"hub:{core_id}")
    assert fsw["port_name"] == "b" and fsw["port_speed"] == 1000
    assert nodes["core-switch"].attributes["port_name"] == "internal2"


async def test_without_network_read_the_fortiswitches_still_work(hass: HomeAssistant, fake_net) -> None:
    _lab(fake_net)
    entry = _entry("auto", fortigate_host="192.0.2.1", fortigate_token=TOKEN)
    with _fortigate(ports=False):
        await _run(hass, entry)
    nodes = _node_states(hass)
    assert nodes["lab-host"].attributes["placement"] == "fortigate"
    router = next(n.attributes for n in nodes.values() if n.attributes.get("node_key") == "hub:gateway")
    assert router["ports"] is None
