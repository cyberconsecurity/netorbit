"""Switch ports: what each port leads to, its speed, and the attributes the card's port view reads."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.netorbit.snmp import PortInfo, build_port_info
from custom_components.netorbit.topology import switch_ports

from .test_auto_and_tools import AP_MAC, GW_MAC, NAS_MAC, SW_MAC, TV_MAC, _network
from .test_integration import _entry, _node_states, _setup

PC_MAC = "00:00:5e:00:53:40"
PHONE_MAC = "00:00:5e:00:53:41"


def test_build_port_info_maps_bridge_ports_to_interfaces() -> None:
    # Bridge ports 1-3 are interfaces 101-103; 103 is down; interface 200 is a VLAN that isn't a port.
    info = build_port_info(
        to_if={1: 101, 2: 102, 3: 103},
        names={101: "Gi1/0/1", 102: "Gi1/0/2", 103: "Gi1/0/3", 200: "Vlan1"},
        aliases={102: "Office desk"},
        high={101: 1000, 102: 2500, 103: 1000},
        low={},
        oper={101: 1, 102: 1, 103: 2, 200: 1},
    )
    assert set(info) == {1, 2, 3}
    assert info[1] == PortInfo(name="Gi1/0/1", alias=None, speed=1000, up=True)
    assert info[2].alias == "Office desk" and info[2].speed == 2500
    # A port that is down has no speed, whatever the interface's nominal speed is.
    assert info[3].up is False and info[3].speed is None


def test_build_port_info_without_a_bridge_map_falls_back_to_interfaces() -> None:
    info = build_port_info({}, {1: "port1", 2: "port2"}, {}, {}, {1: 100_000_000, 2: 4_294_967_295}, {1: 1, 2: 1})
    assert info[1].speed == 100
    # ifSpeed pinned at its maximum means "too fast to say here": unknown, not 4294 Mb/s.
    assert info[2].speed is None


def test_build_port_info_nothing_readable() -> None:
    assert build_port_info({}, {}, {}, {}, {}, {}) is None


def test_switch_ports_places_children_and_finds_the_uplink() -> None:
    table = {GW_MAC: 1, AP_MAC: 6, TV_MAC: 6, PHONE_MAC: 6, NAS_MAC: 3, PC_MAC: 3, SW_MAC: 1}
    children = {"hub:ap": (AP_MAC, "hub"), NAS_MAC: (NAS_MAC, "device"), PC_MAC: (PC_MAC, "device")}
    info = {1: {"name": "p1", "speed": 1000, "up": True}, 2: {"name": "p2", "up": False}, 3: {"speed": 100, "up": True}, 6: {"speed": 2500, "up": True}}
    ports = {p["port"]: p for p in switch_ports(table, SW_MAC, GW_MAC, GW_MAC, children, info)}
    assert sorted(ports) == [1, 2, 3, 6]
    assert ports[1]["uplink"] and ports[1]["nodes"] == []
    assert ports[2]["up"] is False and ports[2]["nodes"] == [] and ports[2]["macs"] == 0
    assert sorted(ports[3]["nodes"]) == sorted([NAS_MAC, PC_MAC]) and ports[3]["speed"] == 100
    # The access point is on port 6; its Wi-Fi clients are learned there but belong to the AP, not the port.
    assert ports[6]["nodes"] == ["hub:ap"] and ports[6]["macs"] == 3
    # The switch's own MAC on its uplink port isn't counted as something learned there.
    assert ports[1]["macs"] == 1


def test_switch_ports_lists_hubs_before_devices_on_a_shared_port() -> None:
    table = {AP_MAC: 4, NAS_MAC: 4}
    ports = switch_ports(table, None, None, None, {NAS_MAC: (NAS_MAC, "device"), "hub:ap": (AP_MAC, "hub")})
    assert ports[0]["nodes"] == ["hub:ap", NAS_MAC]
    assert ports[0]["uplink"] is False


async def test_ports_reach_the_sensors(hass: HomeAssistant, fake_net) -> None:
    _network(fake_net)
    fake_net.ports = {"192.0.2.2": {
        1: PortInfo(name="Port 1", speed=1000, up=True),
        3: PortInfo(name="Port 3", speed=100, up=True),
        6: PortInfo(name="Port 6", alias="Hall AP", speed=2500, up=True),
        8: PortInfo(name="Port 8", up=False),
    }}
    entry = _entry("auto")
    await _setup(hass, entry)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    nodes = _node_states(hass)
    sw = nodes["core-switch"].attributes
    by_port = {p["port"]: p for p in sw["ports"]}
    assert by_port[1]["uplink"] is True
    assert by_port[3]["nodes"] == [NAS_MAC]
    assert by_port[6]["nodes"] == ["hub:u6_lite_hall"] and by_port[6]["alias"] == "Hall AP"
    assert by_port[8]["up"] is False and by_port[8]["nodes"] == []
    # Each node right under the switch knows its port and speed; the TV is under the AP, so it doesn't.
    assert nodes["nas"].attributes["port"] == 3 and nodes["nas"].attributes["port_speed"] == 100
    assert nodes["u6-lite-hall"].attributes["port_speed"] == 2500
    assert nodes["living-room-tv"].attributes["port"] is None
    assert nodes["Router"].attributes["ports"] is None


async def test_switch_without_port_details_still_lists_ports(hass: HomeAssistant, fake_net) -> None:
    """A switch whose MAC table reads but whose interface tables don't: ports come from the MAC table alone."""
    _network(fake_net)
    entry = _entry("auto")
    await _setup(hass, entry)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    sw = _node_states(hass)["core-switch"].attributes
    by_port = {p["port"]: p for p in sw["ports"]}
    assert set(by_port) == {1, 3, 4, 6}
    assert by_port[3]["speed"] is None and by_port[3]["nodes"] == [NAS_MAC]
    assert _node_states(hass)["nas"].attributes["port_speed"] is None


async def test_a_newly_found_device_shows_on_its_port(hass: HomeAssistant, fake_net) -> None:
    """A device that turns up on a switch port is listed on that port once the map places it under the switch."""
    _network(fake_net)
    fake_net.alive.add("192.0.2.30")
    fake_net.arp["192.0.2.30"] = PC_MAC
    fake_net.dns["192.0.2.30"] = "office-pc.lan"
    fake_net.tables["192.0.2.2"][PC_MAC] = 5
    entry = _entry("auto")
    await _setup(hass, entry)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    by_port = {p["port"]: p for p in _node_states(hass)["core-switch"].attributes["ports"]}
    assert by_port[5]["nodes"] == [PC_MAC]
