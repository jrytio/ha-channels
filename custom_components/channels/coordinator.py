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

    async def _async_update_data(self) -> AppStatus | None:
        try:
            status = await self.client.status()
        except ChannelsError:
            self.update_interval = SCAN_UNREACHABLE
            return None
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
