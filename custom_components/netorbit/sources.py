"""Read hub and placement hints that other Home Assistant integrations already have."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .detect import NETWORK_DOMAINS
from .logic import normalize_mac, ping_label, useful_name

_LOGGER = logging.getLogger(__name__)


@dataclass
class RegistryInfo:
    """What another integration says about a MAC."""

    name: str | None
    model: str | None
    manufacturer: str | None
    domain: str | None


def _entry_ids(dev: dr.DeviceEntry) -> list[str]:
    """The config entries a device belongs to, on old and new Home Assistant.

    Newer versions give each device one `config_entry_id` and deprecate the
    `config_entries` set; older ones only have the set.
    """
    if hasattr(dev, "config_entry_id"):
        entry_id = dev.config_entry_id
        return [entry_id] if entry_id else []
    return list(getattr(dev, "config_entries", ()) or ())


def _all_devices(reg: dr.DeviceRegistry) -> list[dr.DeviceEntry]:
    """Every device entry, on old and new Home Assistant.

    Newer versions want `registry.devices` iterated (it yields entries); older ones
    hand back a mapping whose iteration yields ids.
    """
    entries = list(reg.devices)
    if entries and isinstance(entries[0], str):
        return list(reg.devices.values())
    return entries


def registry_by_mac(hass: HomeAssistant) -> dict[str, RegistryInfo]:
    """Every device in Home Assistant that has a MAC connection, keyed by MAC."""
    reg = dr.async_get(hass)
    out: dict[str, RegistryInfo] = {}
    for dev in _all_devices(reg):
        macs = [normalize_mac(v) for k, v in dev.connections if k == dr.CONNECTION_NETWORK_MAC]
        macs = [m for m in macs if m]
        if not macs:
            continue
        domain = None
        for entry_id in _entry_ids(dev):
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry and entry.domain in NETWORK_DOMAINS:
                domain = entry.domain
                break
            if entry and domain is None and entry.domain != "netorbit":
                domain = entry.domain
        info = RegistryInfo(
            name=useful_name(dev.name_by_user) or useful_name(dev.name),
            model=dev.model, manufacturer=dev.manufacturer, domain=domain,
        )
        for mac in macs:
            old = out.get(mac)
            if old is None:
                out[mac] = info
                continue
            # The same MAC often shows up under several integrations (a speaker and its
            # media renderer, say). Prefer network integrations, then a real name.
            if domain in NETWORK_DOMAINS and old.domain not in NETWORK_DOMAINS:
                out[mac] = RegistryInfo(info.name or old.name, info.model or old.model,
                                        info.manufacturer or old.manufacturer, domain)
            elif old.name is None and info.name:
                out[mac] = RegistryInfo(info.name, old.model or info.model,
                                        old.manufacturer or info.manufacturer, old.domain)
    return out


def tracker_names_by_mac(hass: HomeAssistant) -> dict[str, str]:
    """{mac: name} from device trackers (nmap, router integrations) that report a MAC."""
    out: dict[str, str] = {}
    for state in hass.states.async_all("device_tracker"):
        mac = normalize_mac(state.attributes.get("mac"))
        if not mac:
            continue
        name = useful_name(state.attributes.get("friendly_name")) or useful_name(state.attributes.get("host_name"))
        if name:
            out.setdefault(mac, name)
    return out


def ping_names_by_ip(hass: HomeAssistant) -> dict[str, str]:
    """{ip: name} from Home Assistant's Ping integration, which many people already set up by hand."""
    out: dict[str, str] = {}
    ent_reg = er.async_get(hass)
    for entry in hass.config_entries.async_entries("ping"):
        host = (entry.data or {}).get("host") or (entry.options or {}).get("host")
        if not host:
            continue
        name = None
        for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            state = hass.states.get(ent.entity_id)
            name = ping_label(state.attributes.get("friendly_name") if state else None) or ping_label(ent.name)
            if name:
                break
        name = name or ping_label(entry.title)
        if name:
            out.setdefault(str(host), name)
    return out


def client_access_points(hass: HomeAssistant) -> dict[str, str]:
    """{client mac: AP mac} from integrations that know which AP each client uses.

    * Any device_tracker exposing ``mac`` and ``ap_mac`` attributes (UniFi does).
    * Netgear's per-device ``conn_ap_mac`` sensors (Orbi and other mesh routers).
    * The UniFi Network integration's own client list, for clients whose tracker entity is disabled. Home
      Assistant creates those disabled unless the client is already a known device, so on most networks the
      entities above cover only a few clients.
    """
    out: dict[str, str] = {}
    unifi_aps, _ = unifi_network(hass)
    for state in hass.states.async_all("device_tracker"):
        ap = normalize_mac(state.attributes.get("ap_mac"))
        mac = normalize_mac(state.attributes.get("mac"))
        if ap and mac and state.state == "home":
            out[mac] = ap

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    for entry in ent_reg.entities.values():
        if entry.domain != "sensor" or not (entry.unique_id or "").endswith("conn_ap_mac"):
            continue
        state = hass.states.get(entry.entity_id)
        ap = normalize_mac(state.state) if state else None
        if not ap or not entry.device_id:
            continue
        dev = dev_reg.async_get(entry.device_id)
        if not dev:
            continue
        for kind, value in dev.connections:
            mac = normalize_mac(value) if kind == dr.CONNECTION_NETWORK_MAC else None
            if mac:
                out[mac] = ap
    for mac, ap in unifi_aps.items():
        out.setdefault(mac, ap)
    return out


# A client the controller hasn't heard from in this long is treated as gone, as UniFi's own trackers do by default.
UNIFI_CLIENT_FRESH = 300


def unifi_network(hass: HomeAssistant, now: float | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """({wireless client mac: AP mac}, {network device mac: IP}) read straight from the UniFi Network integration.

    Read only, from what the integration already holds in memory; nothing is sent to the controller. Any change in
    the integration's internals just leaves both maps empty, so NetOrbit falls back to the switch tables.
    """
    aps, ips, _ = _unifi_read(hass, now)
    return aps, ips


def unifi_client_ips(hass: HomeAssistant, now: float | None = None) -> dict[str, str]:
    """{IP: MAC} of every client the UniFi controller has seen lately, wired or wireless. Fills in MACs that ARP can't
    give, for subnets Home Assistant reaches only through the router."""
    return _unifi_read(hass, now)[2]


def _unifi_read(hass: HomeAssistant, now: float | None) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    aps: dict[str, str] = {}
    ips: dict[str, str] = {}
    client_ips: dict[str, str] = {}
    now = time.time() if now is None else now
    try:
        entries = hass.config_entries.async_entries("unifi")
    except Exception:  # noqa: BLE001
        return aps, ips, client_ips
    for entry in entries:
        api = getattr(getattr(entry, "runtime_data", None), "api", None)
        if api is None:
            continue
        try:
            for raw in _unifi_raw(getattr(api, "clients", None)):
                seen = raw.get("last_seen")
                if isinstance(seen, (int, float)) and now - seen > UNIFI_CLIENT_FRESH:
                    continue
                mac = normalize_mac(raw.get("mac"))
                if not mac:
                    continue
                ip = raw.get("ip")
                if isinstance(ip, str) and ip:
                    client_ips[ip] = mac
                if raw.get("is_wired"):
                    continue
                ap = normalize_mac(raw.get("ap_mac"))
                if ap:
                    aps[mac] = ap
            for raw in _unifi_raw(getattr(api, "devices", None)):
                mac, ip = normalize_mac(raw.get("mac")), raw.get("ip")
                if mac and isinstance(ip, str) and ip:
                    ips[mac] = ip
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("NetOrbit couldn't read the UniFi client list: %s", err)
    return aps, ips, client_ips


def _unifi_raw(handler: Any) -> list[dict[str, Any]]:
    """The raw dicts of a UniFi API handler (clients or devices)."""
    if handler is None:
        return []
    out = []
    for key in list(handler):
        raw = getattr(handler[key], "raw", None)
        if isinstance(raw, dict):
            out.append(raw)
    return out


async def ssdp_types_by_ip(hass: HomeAssistant) -> dict[str, list[str]]:
    """{ip: [device types]} for devices that announced themselves over UPnP."""
    if "ssdp" not in hass.config.components:
        return {}
    try:
        from homeassistant.components import ssdp  # noqa: PLC0415
        from urllib.parse import urlparse  # noqa: PLC0415
    except ImportError:
        return {}
    out: dict[str, list[str]] = {}
    for st in (
        "urn:schemas-upnp-org:device:WLANAccessPointDevice:1",
        "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
        "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
    ):
        try:
            infos = await ssdp.async_get_discovery_info_by_st(hass, st)
        except Exception:  # noqa: BLE001
            continue
        for info in infos:
            host = urlparse(info.ssdp_location or "").hostname
            if host:
                out.setdefault(host, []).append(info.upnp.get("deviceType") or st)
    return out
