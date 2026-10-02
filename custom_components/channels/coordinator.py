"""Polls one Channels app."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, SCAN_REACHABLE, SCAN_UNREACHABLE
from .lib import AppClient, AppStatus, ChannelsError

_LOGGER = logging.getLogger(__name__)

# A recording that has played to its end is reported at position 0, as
# playing or paused, for as long as the app is left alone. One that was just
# started from the beginning reports 0 too, for a second or two while it loads.
ENDED_AFTER = 3.0


class ChannelsAppCoordinator(DataUpdateCoordinator[AppStatus | None]):
    """Holds the latest status of one app, or None while it is unreachable.

    The app only answers while it is in the foreground on its device, so being
    unreachable is an ordinary state and not a failed update.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: AppClient
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=SCAN_REACHABLE,
        )
        self.client = client
        self.favorites: list[dict[str, Any]] = []
        self._favorites_stale = True
        self.ended = False
        self._at_zero: tuple[str, float] | None = None

    def _track_ended(self, status: AppStatus | None) -> None:
        """Work out whether the app is sitting at the end of a recording."""
        if (
            status is None
            or not status.is_active
            or status.recording_id is None
            or status.position != 0
        ):
            self._at_zero = None
            self.ended = False
            return
        if self._at_zero is None or self._at_zero[0] != status.recording_id:
            self._at_zero = (status.recording_id, status.sampled_at)
        self.ended = status.sampled_at - self._at_zero[1] >= ENDED_AFTER

    def async_set_updated_data(self, data: AppStatus | None) -> None:
        """Take a command's reply as the new status."""
        self._track_ended(data)
        super().async_set_updated_data(data)

    async def _async_update_data(self) -> AppStatus | None:
        try:
            status = await self.client.status()
        except ChannelsError:
            self.update_interval = SCAN_UNREACHABLE
            self._track_ended(None)
            return None
        self._track_ended(status)
        if self.data is None or self._favorites_stale:
            # Just became reachable, so the favourites may have changed; or
            # the last fetch failed. A failure here keeps the previous list
            # and does not make the TV unavailable.
            try:
                self.favorites = await self.client.favorite_channels()
            except ChannelsError:
                self._favorites_stale = True
            else:
                self._favorites_stale = False
        self.update_interval = SCAN_REACHABLE
        return status
