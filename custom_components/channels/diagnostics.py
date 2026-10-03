"""Diagnostics for the Channels integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import CONF_KIND, KIND_APP
from .follow import follow_diagnostics
from .helpers import is_loaded
from .lib import ChannelsError

TO_REDACT_DATA = {CONF_HOST}
TO_REDACT_STATUS = {"title", "episode_title", "summary", "image_url", "channel_name"}
DVR_STATUS_KEYS = ("version", "os", "arch", "name")


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return what the device last reported, without addresses or titles."""
    info: dict[str, Any] = {
        "data": async_redact_data(dict(entry.data), TO_REDACT_DATA),
        "options": dict(entry.options),
    }
    if not is_loaded(entry):
        # A DVR server in setup retry has no client yet; that is when
        # diagnostics are most wanted, so still return the rest.
        info["status"] = "not loaded"
        return info
    if entry.data[CONF_KIND] == KIND_APP:
        status = entry.runtime_data.data
        info["status"] = (
            async_redact_data(asdict(status), TO_REDACT_STATUS) if status else None
        )
        info["follow_session"] = follow_diagnostics(hass, entry.entry_id) or None
        return info
    try:
        reported = await entry.runtime_data.status()
    except ChannelsError:
        info["status"] = "unreachable"
    else:
        info["status"] = {k: reported[k] for k in DVR_STATUS_KEYS if k in reported}
    return info
