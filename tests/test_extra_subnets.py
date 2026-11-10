"""More than one subnet on one map: an IoT VLAN swept with the main network, MACs filled in from UniFi."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.netorbit.logic import parse_subnets

from .test_auto_and_tools import AP_MAC, _network
from .test_integration import _entry, _node_states, _setup
from .test_unifi_direct import _unifi

IOT_SUBNET = "198.51.100.0/24"
IOT_IP = "198.51.100.20"
IOT_MAC = "00:00:5e:00:53:a0"
PLUG_IP = "198.51.100.21"
PLUG_MAC = "00:00:5e:00:53:a1"


def test_parse_subnets() -> None:
    assert parse_subnets("", 1024, 4) == []
    assert parse_subnets(None, 1024, 4) == []
    assert parse_subnets("198.51.100.7/24, 203.0.113.0/25\n198.51.100.0/24", 1024, 4) == ["198.51.100.0/24", "203.0.113.0/25"]
    assert parse_subnets(["203.0.113.0/24"], 1024, 4) == ["203.0.113.0/24"]
    with pytest.raises(ValueError, match="invalid"):
        parse_subnets("not-a-subnet", 1024, 4)
    with pytest.raises(ValueError, match="too_large"):
        parse_subnets("198.51.0.0/16", 1024, 4)  # example-ok: deliberately larger than any documentation range, to trip the size limit
    with pytest.raises(ValueError, match="too_many"):
        parse_subnets("192.0.2.0/30 192.0.2.4/30 192.0.2.8/30 192.0.2.12/30 192.0.2.16/30", 1024, 4)


async def test_routed_subnet_devices_join_the_map_under_their_ap(hass: HomeAssistant, fake_net) -> None:
    """Home Assistant isn't on the IoT subnet, so ARP has nothing for it. UniFi knows the devices' MACs and APs."""
    _network(fake_net)
    fake_net.alive |= {IOT_IP, PLUG_IP}
    _unifi(hass, [
        {"mac": IOT_MAC, "ip": IOT_IP, "ap_mac": AP_MAC, "is_wired": False},
        {"mac": PLUG_MAC, "ip": PLUG_IP, "is_wired": True},
    ], [])
    fake_net.tables["192.0.2.2"][PLUG_MAC] = 3  # the wired one is on the switch, like the NAS
    entry = _entry("auto", extra_subnets=[IOT_SUBNET])
    await _setup(hass, entry)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    by_ip = {n.attributes.get("ip"): n.attributes for n in _node_states(hass).values()}
    assert by_ip[IOT_IP]["link"] == "hub:u6_lite_hall" and by_ip[IOT_IP]["placement"] == "controller"
    assert by_ip[PLUG_IP]["link"] == "hub:core_switch"
    # The main network is drawn as before.
    assert by_ip["192.0.2.12"]["link"] == "hub:core_switch"


async def test_routed_subnet_without_unifi_adds_nothing(hass: HomeAssistant, fake_net) -> None:
    """Without a MAC NetOrbit can't track a device, so a routed subnet with no controller stays off the map."""
    _network(fake_net)
    fake_net.alive.add(IOT_IP)
    entry = _entry("auto", extra_subnets=[IOT_SUBNET])
    await _setup(hass, entry)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert IOT_IP not in {n.attributes.get("ip") for n in _node_states(hass).values()}


async def test_unifi_fills_only_what_arp_lacks(hass: HomeAssistant, fake_net) -> None:
    """On the main subnet ARP's answer stands, even if the controller reports something else for that IP."""
    _network(fake_net)
    _unifi(hass, [{"mac": IOT_MAC, "ip": "192.0.2.12", "is_wired": True}], [])
    entry = _entry("auto")
    await _setup(hass, entry)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    nas = _node_states(hass)["nas"].attributes
    assert nas["mac"].lower() == "00:00:5e:00:53:12"


async def test_settings_take_and_check_other_subnets(hass: HomeAssistant, fake_net) -> None:
    _network(fake_net)
    entry = _entry("auto")
    await _setup(hass, entry)
    base = {"mode": "auto", "ping_interval": 60, "discovery_interval": 300, "stale_hours": 24, "ignore_random_mac": True,
            "auto_hubs": True, "group_by_type": True, "check_internet": True, "prefer_dns_names": False, "snmp_enabled": True}

    async def submit(value: str):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "settings"})
        return await hass.config_entries.options.async_configure(flow["flow_id"], {**base, "extra_subnets": value})

    bad = await submit("198.51.0.0/16")  # example-ok: deliberately larger than any documentation range, to trip the size limit
    assert bad["type"] is FlowResultType.FORM and bad["errors"] == {"extra_subnets": "subnet_too_large"}
    bad = await submit("nonsense")
    assert bad["errors"] == {"extra_subnets": "invalid_subnet"}
    ok = await submit("198.51.100.9/24, 203.0.113.0/24")
    assert ok["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["extra_subnets"] == ["198.51.100.0/24", "203.0.113.0/24"]
    assert entry.runtime_data.subnets == ["192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"]
    cleared = await submit("")
    assert cleared["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["extra_subnets"] == []
