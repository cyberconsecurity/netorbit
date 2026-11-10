"""Diagnostics with addresses and names redacted, safe to attach to a GitHub issue."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import NetOrbitConfigEntry
from .const import CONF_DEVICES, CONF_DISMISSED_HUBS, CONF_HUBS, CONF_IGNORED, CONF_SNMP_COMMUNITY


def _mask(i: int, prefix: str) -> str:
    return f"{prefix}{i:03d}"


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: NetOrbitConfigEntry) -> dict[str, Any]:
    """Shape of the setup without any real IPs, MACs or names."""
    coord = entry.runtime_data
    opts = coord.opts
    data = coord.data
    hub_alias = {h["id"]: ("root" if h.get("parent") is None else _mask(i, "hub_")) for i, h in enumerate(opts[CONF_HUBS])}
    nodes = []
    if data:
        for i, node in enumerate(data.nodes.values()):
            nodes.append(
                {
                    "id": _mask(i, "node_"),
                    "kind": node.kind,
                    "status": node.status,
                    "link": hub_alias.get(node.link or "", node.link and "hub_?"),
                    "has_ip": bool(node.ip),
                    "has_mac": bool(node.mac),
                    "has_hostname": bool(node.hostname),
                    "sleepy": node.sleepy,
                    "source": node.source,
                    "placement": node.placement,
                    "hub_type": node.hub_type,
                    "has_fqdn": bool(node.fqdn),
                    "has_managed_by": bool(node.managed_by),
                    "field_count": len(node.fields),
                    "port_count": len(node.ports or []),
                    "has_port": node.port is not None,
                    "has_port_speed": node.port_speed is not None,
                }
            )
    return {
        "settings": {
            **{k: v for k, v in opts.items() if k not in (CONF_HUBS, CONF_DEVICES, CONF_IGNORED, CONF_DISMISSED_HUBS, CONF_SNMP_COMMUNITY) and not _private(k)},
            "snmp_community_set": bool(opts.get(CONF_SNMP_COMMUNITY)),
            "dismissed_hubs": len(opts.get(CONF_DISMISSED_HUBS, [])),
        },
        "automatic_sources": data.sources if data else [],
        "suggested_hubs": len(data.suggested_hubs) if data else 0,
        "subnet_prefix": entry.data.get("subnet", "").split("/")[-1],
        # Whether a FortiGate is set up and how many FortiSwitches it gave; never its address or token.
        "fortigate": {"configured": bool(entry.options.get("fortigate_host") and entry.options.get("fortigate_token")),
                      "verify_ssl": bool(entry.options.get("fortigate_verify_ssl")),
                      "switches": len(getattr(entry.runtime_data, "_forti", {}) or {}) if getattr(entry, "runtime_data", None) else 0},
        # How many other subnets are swept and their sizes; never the addresses.
        "extra_subnet_prefixes": [str(n).split("/")[-1] for n in (entry.options.get("extra_subnets") or [])],
        "hubs": [
            {"id": hub_alias[h["id"]], "type": h.get("type"), "parent": hub_alias.get(h.get("parent") or "", None), "has_host": bool(h.get("host"))}
            for h in opts[CONF_HUBS]
        ],
        "manual_devices": len(opts[CONF_DEVICES]),
        "ignored": len(opts[CONF_IGNORED]),
        "seen_records": len(coord._seen),  # noqa: SLF001
        "new_devices": len(data.new_devices) if data else 0,
        "last_scan": data.last_scan if data else None,
        "scan": {**coord.scan, "first_scan_done": coord.first_scan_done},
        "nodes": nodes,
    }


# Settings that hold an address, a password or a token stay out of the settings section whatever they are called
# now or later; the sections above say only whether they are set.
_PRIVATE_HINTS = ("token", "password", "secret", "community", "host", "subnet", "address", "url")


def _private(key: str) -> bool:
    return any(hint in key.lower() for hint in _PRIVATE_HINTS)
