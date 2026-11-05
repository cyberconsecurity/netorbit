"""Grandstream GWN access points: Wi-Fi clients from the AP's own SNMP client table land under that AP."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from homeassistant.core import HomeAssistant

from custom_components.netorbit.snmp import GRANDSTREAM_OID_PREFIX, OID_GWN_CLIENT_IP, OID_GWN_CLIENT_MAC, ProbeResult, SnmpClient, is_grandstream, parse_mac

from .conftest import GATEWAY
from .test_integration import _entry, _node_states, _setup

GW_MAC = "00:00:5e:00:53:01"
AP_MAC = "00:00:5e:00:53:06"
PHONE_MAC = "00:00:5e:00:53:40"
LAPTOP_MAC = "00:00:5e:00:53:41"
AP_IP = "192.0.2.6"
GWN_PROBE = ProbeResult(descr="Grandstream GWN access point", name="ap-1", bridge=False, object_id=f"{GRANDSTREAM_OID_PREFIX}.1.1")


def _tuple(oid: str) -> tuple[int, ...]:
    return tuple(int(x) for x in oid.split("."))


def _mac_tuple(mac: str) -> tuple[int, ...]:
    return tuple(int(x, 16) for x in mac.split(":"))


class Octets:
    """Stands in for pysnmp's OctetString: only the raw bytes are needed."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def asOctets(self) -> bytes:  # noqa: N802
        return self._data


def _client(rows_by_base: dict[str, object]) -> SnmpClient:
    """A SnmpClient whose walks return canned rows: {base oid: [(oid tuple, value)] or an Exception}."""

    async def bulk_walk_cmd(_engine, _comm, _target, _ctx, _nr, _mr, objtype, lexicographicMode=False):  # noqa: N803
        base = objtype.base
        rows = rows_by_base.get(base, [])
        if isinstance(rows, Exception):
            raise rows
        if rows == "error":
            yield "timeout", 0, 0, []
            return
        yield None, 0, 0, list(rows)

    async def create(_addr, timeout=1.0, retries=0):  # noqa: ARG001
        return object()

    mod = SimpleNamespace(
        bulk_walk_cmd=bulk_walk_cmd,
        ObjectType=lambda ident: SimpleNamespace(base=ident),
        ObjectIdentity=lambda oid: oid,
        CommunityData=lambda *a, **k: None,
        ContextData=lambda: None,
        UdpTransportTarget=SimpleNamespace(create=create),
    )
    client = SnmpClient(hass=None)
    client._mod, client._engine = mod, object()
    return client


def _row(mac: str, value) -> tuple[tuple[int, ...], object]:
    return _tuple(OID_GWN_CLIENT_MAC) + _mac_tuple(mac), value


def test_parse_mac_forms() -> None:
    raw = bytes.fromhex("00005e005306")
    for value in (raw, bytearray(raw), Octets(raw), "00:00:5E:00:53:06", "00-00-5e-00-53-06", "00005e005306", "0x00005e005306",
                  "00:00:5e:00:53:06 ", Octets(b"00:00:5e:00:53:06")):
        assert parse_mac(value) == AP_MAC, value
    for bad in (None, b"", b"\x00\x01", "not a mac", "00:00:5e", 12, Octets(b"\xff\xfe\xfd\xfc\xfb\xfa\xf9"),
                "abcdef", "192.01", "caf\u00e9:00:5e:00:53:06"):  # 6 characters of text are not 6 raw bytes
        assert parse_mac(bad) is None, bad


def test_is_grandstream() -> None:
    assert is_grandstream(f"{GRANDSTREAM_OID_PREFIX}.1.1")
    assert is_grandstream(f".{GRANDSTREAM_OID_PREFIX}")
    assert is_grandstream(None, "Grandstream GWN7660")
    # pysnmp prints a sysObjectID under a known prefix by name
    assert is_grandstream("SNMPv2-SMI::enterprises.42397.1.1", "Linux GWN7660 4.4")
    assert not is_grandstream("SNMPv2-SMI::enterprises.423970.1.1")
    assert not is_grandstream("1.3.6.1.4.1.423970.1")  # a different enterprise that merely starts with the digits
    assert not is_grandstream("1.3.6.1.4.1.41112.1.6", "UniFi AP")


async def test_walk_raw_bytes_and_text_macs() -> None:
    client = _client({OID_GWN_CLIENT_MAC: [
        _row(PHONE_MAC, Octets(bytes.fromhex("00005e005340"))),   # raw 6 bytes
        _row(LAPTOP_MAC, "00:00:5E:00:53:41"),                     # text
    ]})
    assert await client.wireless_clients(AP_IP, "public") == {PHONE_MAC, LAPTOP_MAC}


async def test_walk_takes_mac_from_the_row_index_when_the_value_is_unreadable() -> None:
    client = _client({OID_GWN_CLIENT_MAC: [_row(PHONE_MAC, Octets(b""))]})
    assert await client.wireless_clients(AP_IP, "public") == {PHONE_MAC}


async def test_walk_falls_back_to_the_ip_column() -> None:
    """If the MAC column is hidden (index only), the IP column's row index carries the MAC."""
    client = _client({
        OID_GWN_CLIENT_MAC: [],
        OID_GWN_CLIENT_IP: [(_tuple(OID_GWN_CLIENT_IP) + _mac_tuple(PHONE_MAC), "192.0.2.40")],
    })
    assert await client.wireless_clients(AP_IP, "public") == {PHONE_MAC}


async def test_empty_table_is_an_empty_set_not_an_error() -> None:
    assert await _client({}).wireless_clients(AP_IP, "public") == set()


async def test_ignores_rows_outside_the_table() -> None:
    client = _client({OID_GWN_CLIENT_MAC: [((1, 3, 6, 1, 2, 1, 1, 1, 0), "other"), _row(PHONE_MAC, "00:00:5e:00:53:40")]})
    assert await client.wireless_clients(AP_IP, "public") == {PHONE_MAC}


async def test_walk_error_and_timeout_do_not_raise(caplog: pytest.LogCaptureFixture) -> None:
    assert await _client({OID_GWN_CLIENT_MAC: "error"}).wireless_clients(AP_IP, "public") is None
    assert await _client({OID_GWN_CLIENT_MAC: TimeoutError("no answer")}).wireless_clients(AP_IP, "public") is None
    assert await _client({OID_GWN_CLIENT_MAC: OSError("unreachable")}).wireless_clients(AP_IP, "public") is None


async def test_unavailable_pysnmp_returns_none() -> None:
    client = SnmpClient(hass=None)

    async def no(): return False  # noqa: E704
    client._ensure = no  # type: ignore[method-assign]
    assert await client.wireless_clients(AP_IP, "public") is None


def _network(fake_net) -> None:
    """Router .1 and a Grandstream AP .6 (no switch tables). A phone and a laptop are on its Wi-Fi."""
    fake_net.alive = {GATEWAY, AP_IP, "192.0.2.40", "192.0.2.41"}
    fake_net.arp = {GATEWAY: GW_MAC, AP_IP: AP_MAC, "192.0.2.40": PHONE_MAC, "192.0.2.41": LAPTOP_MAC}
    fake_net.dns = {AP_IP: "ap-1.lan", "192.0.2.40": "phone.lan", "192.0.2.41": "laptop.lan"}
    fake_net.vendors = {"00:00:5E": "Grandstream Networks"}
    fake_net.snmp = {AP_IP: ("public", GWN_PROBE)}


async def test_wifi_clients_land_under_the_grandstream_ap(hass: HomeAssistant, fake_net) -> None:
    _network(fake_net)
    fake_net.wifi = {AP_IP: {PHONE_MAC}}
    entry = _entry("auto")
    await _setup(hass, entry)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    hubs = {h["name"]: h for h in entry.options["hubs"]}
    assert "ap-1" in hubs and hubs["ap-1"]["type"] == "access_point", hubs
    nodes = _node_states(hass)
    assert nodes["phone"].attributes["link"] == f"hub:{hubs['ap-1']['id']}"
    assert nodes["phone"].attributes["placement"] == "snmp"
    # The laptop is not in the AP's client list, so nothing places it.
    assert nodes["laptop"].attributes["link"] == "hub:gateway"
    assert nodes["laptop"].attributes["placement"] == "none"


async def test_manual_placement_beats_the_ap_client_list(hass: HomeAssistant, fake_net) -> None:
    _network(fake_net)
    fake_net.wifi = {AP_IP: {PHONE_MAC}}
    entry = _entry("auto")
    await _setup(hass, entry)
    await entry.runtime_data.async_refresh()
    await hass.services.async_call("netorbit", "assign_device", {"device": ["phone"], "hub": "Router"}, blocking=True, return_response=True)
    phone = _node_states(hass)["phone"]
    assert phone.attributes["link"] == "hub:gateway" and phone.attributes["placement"] == "manual"


async def test_unreadable_client_table_does_not_break_the_map(hass: HomeAssistant, fake_net) -> None:
    _network(fake_net)
    fake_net.wifi = {AP_IP: None}  # walk failed
    entry = _entry("auto")
    await _setup(hass, entry)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    nodes = _node_states(hass)
    assert nodes["phone"].attributes["link"] == "hub:gateway" and nodes["phone"].attributes["placement"] == "none"


async def test_other_vendors_are_not_asked_for_a_client_table(hass: HomeAssistant, fake_net) -> None:
    _network(fake_net)
    fake_net.snmp = {AP_IP: ("public", ProbeResult(descr="Some other AP", name="ap-1", bridge=False, object_id="1.3.6.1.4.1.41112.1.6"))}
    fake_net.wifi = {AP_IP: {PHONE_MAC}}
    entry = _entry("auto")
    await _setup(hass, entry)
    await entry.runtime_data.async_refresh()
    assert _node_states(hass)["phone"].attributes["placement"] == "none"


class Oid:
    """Stands in for the value pysnmp returns for sysObjectID: it prints by name, get_oid() has the numbers."""

    def __init__(self, numbers: str) -> None:
        self._numbers = tuple(int(x) for x in numbers.split("."))

    def get_oid(self) -> tuple[int, ...]:
        return self._numbers

    def __str__(self) -> str:
        return "SNMPv2-SMI::enterprises." + ".".join(str(x) for x in self._numbers[7:])


async def test_probe_reads_the_sys_object_id_as_numbers() -> None:
    """Against real pysnmp the sysObjectID prints as SNMPv2-SMI::enterprises.42397...; the probe keeps the numbers."""

    async def get_cmd(*_a):
        binds = [(None, "Linux"), (None, "ap-1"), (None, None), (None, Oid("1.3.6.1.4.1.42397.1.1"))]
        return None, 0, 0, binds

    mod = SimpleNamespace(
        get_cmd=get_cmd, ObjectType=lambda i: i, ObjectIdentity=lambda o: o, CommunityData=lambda *a, **k: None,
        ContextData=lambda: None, UdpTransportTarget=SimpleNamespace(create=lambda *a, **k: _done(object())),
    )
    client = SnmpClient(hass=None)
    client._mod, client._engine = mod, object()
    res = await client.probe(AP_IP, "public")
    assert res is not None and res.object_id == f"{GRANDSTREAM_OID_PREFIX}.1.1"
    assert is_grandstream(res.object_id, res.descr)


async def _done(value):
    return value


async def test_slow_walk_is_cut_off_by_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """An AP that answers one row at a time must not hold the scan: the whole walk has a deadline."""
    monkeypatch.setattr("custom_components.netorbit.snmp.WALK_DEADLINE", 0.05)
    client = _client({})

    async def trickle(_engine, _comm, _target, _ctx, _nr, _mr, objtype, lexicographicMode=False):  # noqa: N803
        while True:
            await asyncio.sleep(0.02)
            yield None, 0, 0, [_row(PHONE_MAC, Octets(bytes.fromhex("00005e005340")))]

    client._mod.bulk_walk_cmd = trickle
    assert await asyncio.wait_for(client.wireless_clients(AP_IP, "public"), 2) is None


async def test_row_index_with_a_length_prefix_still_gives_the_mac() -> None:
    """An OCTET STRING index without a fixed size is length prefixed (7 sub-ids): the MAC is its last 6."""
    row = (_tuple(OID_GWN_CLIENT_MAC) + (6,) + _mac_tuple(PHONE_MAC), Octets(b""))
    assert await _client({OID_GWN_CLIENT_MAC: [row]}).wireless_clients(AP_IP, "public") == {PHONE_MAC}
    assert await _client({OID_GWN_CLIENT_MAC: [(_tuple(OID_GWN_CLIENT_MAC) + (7, 8), Octets(b""))]}).wireless_clients(AP_IP, "public") == set()


async def test_table_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("custom_components.netorbit.snmp.MAX_TABLE", 3)
    rows = [_row(f"00:00:5e:00:54:{i:02x}", Octets(b"")) for i in range(10)]
    assert len(await _client({OID_GWN_CLIENT_MAC: rows}).wireless_clients(AP_IP, "public")) == 3


async def test_ap_listing_its_own_and_the_routers_mac_adds_no_devices(hass: HomeAssistant, fake_net) -> None:
    _network(fake_net)
    fake_net.wifi = {AP_IP: {PHONE_MAC, AP_MAC, GW_MAC}}
    entry = _entry("auto")
    await _setup(hass, entry)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    nodes = _node_states(hass)
    assert nodes["phone"].attributes["placement"] == "snmp"
    devices = [n for n in nodes.values() if n.attributes["kind"] == "device"]
    assert {n.attributes["mac"].lower() for n in devices} == {PHONE_MAC, LAPTOP_MAC}


async def test_device_plugged_into_the_switch_is_not_pulled_onto_the_ap(hass: HomeAssistant, fake_net) -> None:
    """A laptop the AP still lists, but that a managed switch sees on its own edge port, is wired."""
    _network(fake_net)
    fake_net.alive.add("192.0.2.2")
    fake_net.arp["192.0.2.2"] = "00:00:5e:00:53:02"
    fake_net.dns["192.0.2.2"] = "core-switch.lan"
    fake_net.snmp["192.0.2.2"] = ("public", ProbeResult(descr="8-port managed switch", name="core-switch", bridge=True))
    # Port 6 faces the AP, port 3 is an edge port. The AP lists both the phone and the docked laptop.
    fake_net.tables = {"192.0.2.2": {GW_MAC: 1, AP_MAC: 6, PHONE_MAC: 6, LAPTOP_MAC: 3}}
    fake_net.wifi = {AP_IP: {PHONE_MAC, LAPTOP_MAC}}
    entry = _entry("auto")
    await _setup(hass, entry)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    nodes = _node_states(hass)
    ap = next(h["id"] for h in entry.options["hubs"] if h["name"] == "ap-1")
    sw = next(h["id"] for h in entry.options["hubs"] if h["name"] == "core-switch")
    assert nodes["phone"].attributes["link"] == f"hub:{ap}"
    assert nodes["laptop"].attributes["link"] == f"hub:{sw}"
