"""Media player for a Channels app."""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER
from .coordinator import ChannelsAppCoordinator
from .lib import AppStatus, ChannelsError

_STATES = {
    "playing": MediaPlayerState.PLAYING,
    "paused": MediaPlayerState.PAUSED,
    "stopped": MediaPlayerState.IDLE,
}
_CONTENT_TYPES = {
    "tv": MediaType.EPISODE,
    "movie": MediaType.MOVIE,
    "video": MediaType.VIDEO,
}
_RECORDING_TYPES = {
    MediaType.EPISODE,
    MediaType.MOVIE,
    MediaType.TVSHOW,
    MediaType.VIDEO,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the media player for one app."""
    async_add_entities([ChannelsMediaPlayer(entry.runtime_data, entry)])


class ChannelsMediaPlayer(CoordinatorEntity[ChannelsAppCoordinator], MediaPlayerEntity):
    """A Channels app on a TV."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.SEEK
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.PLAY_MEDIA
    )

    def __init__(self, coordinator: ChannelsAppCoordinator, entry: ConfigEntry) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = entry.unique_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model="Channels app",
        )

    @property
    def _status(self) -> AppStatus | None:
        return self.coordinator.data

    @property
    def available(self) -> bool:
        """Return True while the app's API answers."""
        return self._status is not None

    @property
    def state(self) -> MediaPlayerState | None:
        """Return the playback state."""
        return _STATES.get(self._status.state) if self._status else None

    @property
    def is_volume_muted(self) -> bool | None:
        """Return whether the app is muted."""
        return self._status.muted if self._status else None

    @property
    def media_content_id(self) -> str | None:
        """Return the recording ID, or the channel number on live TV."""
        if not self._status:
            return None
        return self._status.recording_id or self._status.channel_number

    @property
    def media_content_type(self) -> MediaType | None:
        """Return what kind of thing is playing."""
        if not self._status or not self._status.is_active:
            return None
        if self._status.recording_id is None:
            return MediaType.CHANNEL
        return _CONTENT_TYPES.get(self._status.content_type, MediaType.VIDEO)

    @property
    def media_title(self) -> str | None:
        """Return the episode title when there is one, else the title."""
        if not self._status:
            return None
        return self._status.episode_title or self._status.title

    @property
    def media_series_title(self) -> str | None:
        """Return the show's name for TV content."""
        if self._status and self._status.content_type == "tv":
            return self._status.title
        return None

    @property
    def media_season(self) -> str | None:
        """Return the season number."""
        if self._status and self._status.season_number is not None:
            return str(self._status.season_number)
        return None

    @property
    def media_episode(self) -> str | None:
        """Return the episode number."""
        if self._status and self._status.episode_number is not None:
            return str(self._status.episode_number)
        return None

    @property
    def media_image_url(self) -> str | None:
        """Return the artwork."""
        return self._status.image_url if self._status else None

    @property
    def media_position(self) -> float | None:
        """Return the position in seconds; recordings only."""
        return self._status.position if self._status else None

    @property
    def media_position_updated_at(self) -> datetime | None:
        """Return when the position was read."""
        if not self._status or self._status.position is None:
            return None
        return dt_util.utc_from_timestamp(self._status.sampled_at)

    @property
    def media_duration(self) -> float | None:
        """Return the duration; unknown while a recording is in progress."""
        return self._status.duration if self._status else None

    @property
    def source(self) -> str | None:
        """Return the live channel's name."""
        return self._status.channel_name if self._status else None

    @property
    def source_list(self) -> list[str]:
        """Return the favourite channels."""
        return [
            name
            for channel in self.coordinator.favorites
            if (name := channel.get("name"))
        ]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the identifiers automations need."""
        if not self._status:
            return {}
        return {
            "recording_id": self._status.recording_id,
            "channel_number": self._status.channel_number,
            "channel_name": self._status.channel_name,
        }

    async def _run(self, command: Awaitable[AppStatus]) -> None:
        """Send a command and take its reply as the new state."""
        try:
            status = await command
        except ChannelsError as err:
            raise HomeAssistantError(str(err)) from err
        self.coordinator.async_set_updated_data(status)

    async def async_media_play(self) -> None:
        """Resume playback."""
        await self._run(self.coordinator.client.resume())

    async def async_media_pause(self) -> None:
        """Pause playback."""
        await self._run(self.coordinator.client.pause())

    async def async_media_stop(self) -> None:
        """Stop playback."""
        await self._run(self.coordinator.client.stop())

    async def async_media_next_track(self) -> None:
        """Skip to the next commercial marker."""
        await self._run(self.coordinator.client.skip_forward())

    async def async_media_previous_track(self) -> None:
        """Skip to the previous commercial marker."""
        await self._run(self.coordinator.client.skip_backward())

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute. The app only has a toggle, so read it fresh first.

        The cached status can be seconds old, and the remote may have changed it.
        """
        try:
            status = await self.coordinator.client.status()
        except ChannelsError as err:
            raise HomeAssistantError(str(err)) from err
        if status.muted != mute:
            await self._run(self.coordinator.client.toggle_mute())

    async def async_media_seek(self, position: float) -> None:
        """Seek to an absolute position. The app only seeks relatively."""
        try:
            status = await self.coordinator.client.status()
        except ChannelsError as err:
            raise HomeAssistantError(str(err)) from err
        if status.position is None:
            raise ServiceValidationError("Live TV cannot be seeked to a position")
        await self._run(self.coordinator.client.seek(position - status.position))

    async def async_select_source(self, source: str) -> None:
        """Tune a favourite channel by name."""
        for channel in self.coordinator.favorites:
            if (name := channel.get("name")) and name == source:
                await self._run(self.coordinator.client.play_channel(channel["number"]))
                return
        raise ServiceValidationError(f"{source} is not a favourite channel")

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play a channel by number or a recording by ID."""
        if media_type == MediaType.CHANNEL:
            await self._run(self.coordinator.client.play_channel(media_id))
        elif media_type in _RECORDING_TYPES:
            await self._run(self.coordinator.client.play_recording(media_id))
        else:
            raise ServiceValidationError(
                f"Channels cannot play media type {media_type}"
            )

    async def async_seek_by(self, seconds: float) -> None:
        """Seek relative to the current position."""
        await self._run(self.coordinator.client.seek(seconds))

    async def async_seek_forward(self) -> None:
        """Seek ahead by the amount set in the app."""
        await self._run(self.coordinator.client.seek_forward())

    async def async_seek_backward(self) -> None:
        """Seek back by the amount set in the app."""
        await self._run(self.coordinator.client.seek_backward())
