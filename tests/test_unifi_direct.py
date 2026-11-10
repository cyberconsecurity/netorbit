"""Wireless clients placed from the UniFi Network integration's own client list.

Home Assistant creates UniFi client trackers disabled unless the client is already a known device, so the
tracker entities usually cover only a few clients. NetOrbit also reads the integration's client list directly.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.netorbit.sources import client_access_points, unifi_network

from .test_auto_and_tools import AP_MAC, TV_MAC, _network
from .test_integration import _entry, _node_states, _setup

AP2_MAC = "00:00:5f:00:53:70"  # example-ok: synthetic Ubiquiti-prefixed test value (second AP, as ARP gives it)
AP2_RADIO = "00:00:5e:00:53:7f"  # the same AP as the controller reports it (not close to its ARP MAC)
PHONE_MAC = "00:00:5e:00:53:71"
WIRED_MAC = "00:00:5e:00:53:72"
OLD_MAC = "00:00:5e:00:53:73"


class _Handler(dict):
    """Stands in for an aiounifi API handler: iterate keys, look items up by key."""


def _unifi(hass: HomeAssistant, clients: list[dict], devices: list[dict]) -> None:
    entry = MockConfigEntry(domain="unifi", title="UniFi")
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(api=SimpleNamespace(
        clients=_Handler({c["mac"]: SimpleNamespace(raw=c) for c in clients}),
        devices=_Handler({d["mac"]: SimpleNamespace(raw=d) for d in devices}),
    ))


async def test_reads_the_unifi_client_list(hass: HomeAssistant) -> None:
    now = time.time()
    _unifi(hass, [
        {"mac": PHONE_MAC, "ap_mac": AP2_RADIO, "is_wired": False, "last_seen": now - 30},
        {"mac": WIRED_MAC, "ap_mac": AP2_RADIO, "is_wired": True, "last_seen": now - 30},
        {"mac": OLD_MAC, "ap_mac": AP2_RADIO, "is_wired": False, "last_seen": now - 3600},
    ], [{"mac": AP2_RADIO, "ip": "192.0.2.7"}])
    aps, ips = unifi_network(hass, now)
    assert aps == {PHONE_MAC: AP2_RADIO}  # wired and long-gone clients are left out
    assert ips == {AP2_RADIO: "192.0.2.7"}
    assert client_access_points(hass) == {PHONE_MAC: AP2_RADIO}


async def test_tracker_entities_win_over_the_client_list(hass: HomeAssistant) -> None:
    _unifi(hass, [{"mac": PHONE_MAC, "ap_mac": AP2_RADIO, "is_wired": False}], [])
    hass.states.async_set("device_tracker.phone", "home", {"mac": PHONE_MAC, "ap_mac": AP_MAC})
    assert client_access_points(hass) == {PHONE_MAC: AP_MAC}


async def test_nothing_breaks_without_unifi_or_with_an_unloaded_entry(hass: HomeAssistant) -> None:
    assert unifi_network(hass) == ({}, {})
    MockConfigEntry(domain="unifi").add_to_hass(hass)  # set up but not loaded: no runtime_data
    assert unifi_network(hass) == ({}, {})


async def test_two_aps_on_one_port_are_told_apart_by_unifi(hass: HomeAssistant, fake_net) -> None:
    """Two APs behind one switch port: the MAC table can't say which a client uses, the controller can. The
    controller's MAC for the second AP doesn't match the one NetOrbit learned, so it is matched by IP."""
    _network(fake_net)
    fake_net.alive.add("192.0.2.7")
    fake_net.arp["192.0.2.7"] = AP2_MAC
    fake_net.dns["192.0.2.7"] = "u6-lite-office.lan"
    fake_net.tables["192.0.2.2"][AP2_MAC] = 6  # same port as the first AP and the TV
    _unifi(hass, [{"mac": TV_MAC, "ap_mac": AP2_RADIO, "is_wired": False}], [{"mac": AP2_RADIO, "ip": "192.0.2.7"}])
    entry = _entry("auto")
    await _setup(hass, entry)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    tv = _node_states(hass)["living-room-tv"].attributes
    assert tv["link"] == "hub:u6_lite_office"
    assert tv["placement"] == "controller"


async def test_two_aps_on_one_port_without_unifi_stay_on_the_switch(hass: HomeAssistant, fake_net) -> None:
    """The same network with no controller: the device stays on the switch, as before."""
    _network(fake_net)
    fake_net.alive.add("192.0.2.7")
    fake_net.arp["192.0.2.7"] = AP2_MAC
    fake_net.dns["192.0.2.7"] = "u6-lite-office.lan"
    fake_net.tables["192.0.2.2"][AP2_MAC] = 6
    entry = _entry("auto")
    await _setup(hass, entry)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert _node_states(hass)["living-room-tv"].attributes["link"] == "hub:core_switch"


async def test_unifi_presence_marks_ping_shy_devices_online(hass: HomeAssistant, fake_net) -> None:
    """An Echo that ignores pings is online when the controller heard from it lately, and not when it didn't."""
    _network(fake_net)
    entry = _entry("auto")
    await _setup(hass, entry)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    fake_net.alive.discard("192.0.2.10")  # the TV stops answering pings
    fake_net.alive.discard("192.0.2.6")  # and so does the access point
    now = time.time()
    _unifi(hass, [{"mac": TV_MAC, "ap_mac": AP_MAC, "is_wired": False, "last_seen": now - 20}],
           [{"mac": AP_MAC, "ip": "192.0.2.6", "state": 1}])
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    nodes = _node_states(hass)
    assert nodes["living-room-tv"].state == "online"
    assert nodes["u6-lite-hall"].state == "online"


async def test_unifi_presence_needs_a_recent_last_seen_and_can_be_turned_off(hass: HomeAssistant, fake_net) -> None:
    _network(fake_net)
    entry = _entry("auto", controller_presence=False)
    await _setup(hass, entry)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    fake_net.alive.discard("192.0.2.10")
    _unifi(hass, [{"mac": TV_MAC, "ap_mac": AP_MAC, "is_wired": False, "last_seen": time.time() - 20}], [])
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert _node_states(hass)["living-room-tv"].state != "online"  # turned off: pings only
    # On, but the controller has no recent last-seen time for the NAS: no claim either way, so pings decide.
    hass.config_entries.async_update_entry(entry, options={**entry.options, "controller_presence": True})
    await hass.async_block_till_done()
    fake_net.alive.discard("192.0.2.12")
    _unifi(hass, [{"mac": "00:00:5e:00:53:12", "is_wired": True}], [])
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert _node_states(hass)["nas"].state != "online"
