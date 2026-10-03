"""Actions for the Channels integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, service
import probatio as vol

from .const import (
    ATTR_BEHIND_LIVE,
    ATTR_FOLLOWER_TIMEOUT,
    ATTR_FOLLOWERS,
    ATTR_LEADER,
    ATTR_LIVE_SETTLE,
    ATTR_SECONDS,
    ATTR_TOLERANCE_MS,
    CONF_SYNC_OFFSET_MS,
    DOMAIN,
    SERVICE_SEEK_BACKWARD,
    SERVICE_SEEK_BY,
    SERVICE_SEEK_FORWARD,
    SERVICE_START_FOLLOW,
    SERVICE_STOP_FOLLOW,
    SERVICE_SWITCH_TO_RECORDING,
    SERVICE_SYNC_PLAYBACK,
)
from .follow import async_leader_of, async_start_follow, async_stop_follow
from .helpers import async_get_app_entry, async_get_dvr_client, is_loaded
from .lib import (
    ChannelsError,
    SystemClock,
    switch_to_recording,
    sync_follower,
    wait_until_reachable,
)
from .lib.recording import MIN_BEHIND_LIVE
from .lib.sync import SKIPPED, SyncResult, leader_status

SYNC_PLAYBACK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_LEADER): cv.entity_id,
        vol.Required(ATTR_FOLLOWERS): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional(ATTR_FOLLOWER_TIMEOUT, default=30): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=120)
        ),
        vol.Optional(ATTR_TOLERANCE_MS, default=50): vol.All(
            vol.Coerce(float), vol.Range(min=50, max=1000)
        ),
    }
)


SWITCH_TO_RECORDING_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional(ATTR_BEHIND_LIVE, default=5): vol.All(
            vol.Coerce(float), vol.Range(min=MIN_BEHIND_LIVE, max=60)
        ),
    }
)

START_FOLLOW_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_LEADER): cv.entity_id,
        vol.Required(ATTR_FOLLOWERS): vol.All(cv.ensure_list, [cv.entity_id]),
        # Above 900 ms a follower would be jumped before it was ever fine-tuned.
        vol.Optional(ATTR_TOLERANCE_MS, default=250): vol.All(
            vol.Coerce(float), vol.Range(min=100, max=900)
        ),
        vol.Optional(ATTR_LIVE_SETTLE, default=10): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=300)
        ),
        vol.Optional(ATTR_BEHIND_LIVE, default=5): vol.All(
            vol.Coerce(float), vol.Range(min=MIN_BEHIND_LIVE, max=60)
        ),
    }
)

STOP_FOLLOW_SCHEMA = vol.Schema({vol.Required(ATTR_LEADER): cv.entity_id})

_LOGGER = logging.getLogger(__name__)

NOT_SET_UP = "not_set_up"
NOT_LOADED = "Its Channels entry is not loaded"
UNEXPECTED_ERROR = "An unexpected error occurred; see the Home Assistant log"
_SWITCH_LOCK = "switch_lock"


def _switch_lock(hass: HomeAssistant) -> asyncio.Lock:
    """Return the lock that lets only one switch run at a time.

    Two switches on the same channel would both find no recording and both
    toggle, and the second toggle would stop the first one's recording.
    """
    return hass.data.setdefault(DOMAIN, {}).setdefault(_SWITCH_LOCK, asyncio.Lock())


def _skipped(reason: str) -> dict[str, object]:
    return SyncResult(SKIPPED, reason=reason).as_dict()


async def _async_sync_playback(call: ServiceCall) -> ServiceResponse:
    """Put the followers on the leader's recording at its position.

    The leader and followers are data fields, not a target: entity actions
    skip unavailable entities, and a follower is unavailable until its app
    has been launched. Each follower's API is polled here instead.
    """
    hass = call.hass
    leader_id: str = call.data[ATTR_LEADER]
    timeout: float = call.data[ATTR_FOLLOWER_TIMEOUT]
    tolerance: float = call.data[ATTR_TOLERANCE_MS] / 1000
    clock = SystemClock()

    leader_entry = async_get_app_entry(hass, leader_id)
    if leader_entry is None:
        raise ServiceValidationError(f"{leader_id} is not a Channels media player")
    if not is_loaded(leader_entry):
        return {"error": "The leader's Channels entry is not loaded", "followers": {}}
    leader = leader_entry.runtime_data.client

    try:
        await leader_status(leader)
    except ChannelsError as err:
        return {"error": str(err), "followers": {}}

    async def sync_one(entity_id: str) -> dict[str, object]:
        # Nothing done for one follower may escape: gather would drop every
        # other follower's result with it.
        try:
            if entity_id == leader_id:
                return _skipped("It is the leader")
            entry = async_get_app_entry(hass, entity_id)
            if entry is None:
                # Expected for a room listed ahead of its TV being added, so it
                # gets its own status that callers can ignore.
                return {"status": NOT_SET_UP}
            if not is_loaded(entry):
                # Disabled or mid-reload: not something a caller should ignore.
                return _skipped(NOT_LOADED)
            follower = entry.runtime_data.client
            if not await wait_until_reachable(follower, timeout, clock):
                return _skipped("Channels did not answer on that TV")
            result = await sync_follower(
                leader,
                follower,
                clock=clock,
                tolerance=tolerance,
                # A positive offset delays that TV, so aim it behind the leader.
                target=-entry.options.get(CONF_SYNC_OFFSET_MS, 0) / 1000,
            )
            await entry.runtime_data.async_request_refresh()
            return result.as_dict()
        except ChannelsError as err:
            return _skipped(str(err))
        except Exception:
            _LOGGER.exception("Unexpected error syncing %s", entity_id)
            return _skipped(UNEXPECTED_ERROR)

    followers: list[str] = list(dict.fromkeys(call.data[ATTR_FOLLOWERS]))
    results = await asyncio.gather(*(sync_one(entity_id) for entity_id in followers))
    return {"error": None, "followers": dict(zip(followers, results, strict=True))}


_SWITCH_FAILED: dict[str, object] = {
    "recording_id": None,
    "switched": False,
    "started_recording": False,
}


async def _async_switch_one(
    hass: HomeAssistant, entry: ConfigEntry, behind_live: float
) -> dict[str, object]:
    """Move one TV from live TV onto the recording of the same programme.

    Failures come back in the result rather than as an exception, so a
    script can read the reason.
    """
    failed = _SWITCH_FAILED
    coordinator = entry.runtime_data
    dvr = async_get_dvr_client(hass)
    if dvr is None:
        # A TV already on a recording needs no DVR; check before failing.
        try:
            status = await coordinator.client.status()
        except ChannelsError as err:
            return {**failed, "error": str(err)}
        except Exception:
            _LOGGER.exception("Unexpected error reading %s", entry.title)
            return {**failed, "error": UNEXPECTED_ERROR}
        if status.recording_id is not None:
            return {**failed, "recording_id": status.recording_id, "error": None}
        return {**failed, "error": "No Channels DVR server is set up"}
    try:
        result = await switch_to_recording(
            coordinator.client,
            dvr,
            clock=SystemClock(),
            behind_live=behind_live,
        )
    except ChannelsError as err:
        # A failure after the record command was sent leaves a recording
        # running; say so, or a retry could toggle it off.
        return {
            **failed,
            "started_recording": getattr(err, "started_recording", False),
            "error": str(err),
        }
    except Exception:
        _LOGGER.exception("Unexpected error switching %s", entry.title)
        return {**failed, "error": UNEXPECTED_ERROR}
    await coordinator.async_request_refresh()
    return {
        "recording_id": result.recording_id,
        "switched": result.switched,
        "started_recording": result.started_recording,
        "error": None,
    }


async def _async_switch_to_recording(call: ServiceCall) -> ServiceResponse:
    """Switch each targeted TV onto its recording.

    Registered as a domain action rather than an entity action: entity
    actions skip unavailable entities, and a TV is unavailable whenever its
    app is not answering, which this action reports instead of raising.
    """
    hass = call.hass
    behind_live: float = call.data[ATTR_BEHIND_LIVE]
    entity_ids: list[str] = list(dict.fromkeys(call.data[ATTR_ENTITY_ID]))
    entries: dict[str, ConfigEntry] = {}
    for entity_id in entity_ids:
        entry = async_get_app_entry(hass, entity_id)
        if entry is None:
            raise ServiceValidationError(f"{entity_id} is not a Channels media player")
        entries[entity_id] = entry
    # One at a time, across calls too, so a later TV's DVR check sees the
    # recording an earlier one started instead of toggling it off.
    lock = _switch_lock(hass)
    results: dict[str, dict[str, object]] = {}
    for entity_id, entry in entries.items():
        if not is_loaded(entry):
            results[entity_id] = {**_SWITCH_FAILED, "error": NOT_LOADED}
            continue
        async with lock:
            results[entity_id] = await _async_switch_one(hass, entry, behind_live)
    return results


def _leader_entry(hass: HomeAssistant, leader_id: str) -> ConfigEntry:
    entry = async_get_app_entry(hass, leader_id)
    if entry is None:
        raise ServiceValidationError(f"{leader_id} is not a Channels media player")
    return entry


async def _async_start_follow(call: ServiceCall) -> None:
    """Start keeping the followers on the leader's recording and position."""
    hass = call.hass
    leader_id: str = call.data[ATTR_LEADER]
    leader_entry = _leader_entry(hass, leader_id)
    if not is_loaded(leader_entry):
        raise HomeAssistantError("The leader's Channels entry is not loaded")
    # Two sessions pulling one TV two ways would never settle.
    if (other := async_leader_of(hass, leader_id)) is not None:
        raise ServiceValidationError(f"{leader_id} is itself following {other}")

    followers: dict[str, ConfigEntry] = {}
    for entity_id in dict.fromkeys(call.data[ATTR_FOLLOWERS]):
        entry = async_get_app_entry(hass, entity_id)
        # A room listed ahead of its TV being added is expected; skip it.
        if entity_id == leader_id or entry is None:
            continue
        if (other := async_leader_of(hass, entity_id)) not in (None, leader_id):
            raise ServiceValidationError(f"{entity_id} is already following {other}")
        followers[entity_id] = entry

    async_start_follow(
        hass,
        leader_id,
        leader_entry,
        followers,
        tolerance=call.data[ATTR_TOLERANCE_MS] / 1000,
        live_settle=call.data[ATTR_LIVE_SETTLE],
        behind_live=call.data[ATTR_BEHIND_LIVE],
        switch_lock=_switch_lock(hass),
    )


async def _async_stop_follow(call: ServiceCall) -> None:
    """End the leader's follow session, if it has one."""
    entry = _leader_entry(call.hass, call.data[ATTR_LEADER])
    async_stop_follow(call.hass, entry.entry_id)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_START_FOLLOW,
        _async_start_follow,
        schema=START_FOLLOW_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_STOP_FOLLOW,
        _async_stop_follow,
        schema=STOP_FOLLOW_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SYNC_PLAYBACK,
        _async_sync_playback,
        schema=SYNC_PLAYBACK_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SWITCH_TO_RECORDING,
        _async_switch_to_recording,
        schema=SWITCH_TO_RECORDING_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SEEK_BY,
        entity_domain=MEDIA_PLAYER_DOMAIN,
        schema={vol.Required(ATTR_SECONDS): vol.Coerce(float)},
        func="async_seek_by",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SEEK_FORWARD,
        entity_domain=MEDIA_PLAYER_DOMAIN,
        schema=None,
        func="async_seek_forward",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SEEK_BACKWARD,
        entity_domain=MEDIA_PLAYER_DOMAIN,
        schema=None,
        func="async_seek_backward",
    )
