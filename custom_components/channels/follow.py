"""Follow sessions: at most one per leader, each a background task."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_SYNC_OFFSET_MS,
    DOMAIN,
    EVENT_FOLLOW_PROBLEM,
    SIGNAL_FOLLOW_UPDATED,
)
from .helpers import async_get_dvr_client, is_loaded
from .lib import (
    AppStatus,
    ChannelsConnectionError,
    Follower,
    FollowSession,
    SystemClock,
)

_SESSIONS = "follow_sessions"
NOT_LOADED = "Its Channels entry is not loaded"


@dataclass(slots=True)
class RunningSession:
    """A follow session and the entities it is about."""

    leader_id: str
    follower_ids: list[str]
    session: FollowSession
    task: asyncio.Task[None]


class _WhileLoaded:
    """A follower's app, which answers only while its config entry is loaded.

    A session outlives a reload or a removal of a follower's entry. Reading
    the client through the entry each time keeps the session off a TV whose
    entry is gone and picks up the new client after a reload.
    """

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    @property
    def _client(self) -> Any:
        if not is_loaded(self._entry):
            raise ChannelsConnectionError(NOT_LOADED)
        return self._entry.runtime_data.client

    async def status(self) -> AppStatus:
        return await self._client.status()

    async def play_recording(self, recording_id: str) -> AppStatus:
        return await self._client.play_recording(recording_id)

    async def seek(self, seconds: float) -> AppStatus:
        return await self._client.seek(seconds)

    async def pause(self) -> AppStatus:
        return await self._client.pause()

    async def resume(self) -> AppStatus:
        return await self._client.resume()


def _sessions(hass: HomeAssistant) -> dict[str, RunningSession]:
    """Return the running sessions, keyed by the leader's config entry ID."""
    return hass.data.setdefault(DOMAIN, {}).setdefault(_SESSIONS, {})


@callback
def async_start_follow(
    hass: HomeAssistant,
    leader_id: str,
    leader_entry: ConfigEntry,
    followers: dict[str, ConfigEntry],
    *,
    tolerance: float,
    live_settle: float,
    behind_live: float,
    switch_lock: asyncio.Lock,
) -> None:
    """Start a session for a leader, replacing the one it already has."""
    async_stop_follow(hass, leader_entry.entry_id)

    @callback
    def problem(follower_id: str | None, message: str) -> None:
        hass.bus.async_fire(
            EVENT_FOLLOW_PROBLEM,
            {"leader": leader_id, "follower": follower_id, "message": message},
        )

    @callback
    def status_changed(follower_id: str, status: str) -> None:
        async_dispatcher_send(hass, SIGNAL_FOLLOW_UPDATED)

    session = FollowSession(
        leader_entry.runtime_data.client,
        {
            entity_id: Follower(
                _WhileLoaded(entry),
                # A positive offset delays that TV, so hold it behind the leader.
                target=-entry.options.get(CONF_SYNC_OFFSET_MS, 0) / 1000,
            )
            for entity_id, entry in followers.items()
        },
        dvr=async_get_dvr_client(hass),
        clock=SystemClock(),
        tolerance=tolerance,
        live_settle=live_settle,
        behind_live=behind_live,
        switch_lock=switch_lock,
        on_problem=problem,
        on_status=status_changed,
    )
    task = leader_entry.async_create_background_task(
        hass, session.run(), f"{DOMAIN} follow {leader_id}"
    )
    _sessions(hass)[leader_entry.entry_id] = RunningSession(
        leader_id, list(followers), session, task
    )
    async_dispatcher_send(hass, SIGNAL_FOLLOW_UPDATED)


@callback
def async_stop_follow(hass: HomeAssistant, leader_entry_id: str) -> bool:
    """End a leader's session. Return whether there was one."""
    running = _sessions(hass).pop(leader_entry_id, None)
    if running is None:
        return False
    running.task.cancel()
    async_dispatcher_send(hass, SIGNAL_FOLLOW_UPDATED)
    return True


@callback
def async_leader_of(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return the leader an entity is following, if it is in a session."""
    for running in _sessions(hass).values():
        if entity_id in running.follower_ids:
            return running.leader_id
    return None


@callback
def follow_attributes(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    """Return what an entity's part in any running session is."""
    attributes: dict[str, Any] = {}
    for running in _sessions(hass).values():
        if running.leader_id == entity_id:
            attributes["followed_by"] = list(running.follower_ids)
        elif entity_id in running.follower_ids:
            attributes["following"] = running.leader_id
            attributes["follow_status"] = running.session.statuses[entity_id]
    return attributes


@callback
def follow_diagnostics(hass: HomeAssistant, leader_entry_id: str) -> dict[str, str]:
    """Return each follower's status in a leader's session, if it has one."""
    running = _sessions(hass).get(leader_entry_id)
    return dict(running.session.statuses) if running else {}
