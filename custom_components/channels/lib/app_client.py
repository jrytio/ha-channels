"""Client for the HTTP API a Channels app serves on port 57000.

The app only answers while it is in the foreground on its device.
"""

from __future__ import annotations

import math
from typing import Any
from urllib.parse import quote

import aiohttp

from .clock import Clock, SystemClock
from .models import AppStatus, ChannelsConnectionError, ChannelsError, url_host

DEFAULT_APP_PORT = 57000
_TIMEOUT = aiohttp.ClientTimeout(total=5)


def _as_object(body: Any) -> dict[str, Any]:
    """Return a JSON body that is an object, or an empty one for anything else."""
    return body if isinstance(body, dict) else {}


def _segment(value: object) -> str:
    """Encode a caller-supplied value as exactly one path segment."""
    return quote(str(value), safe="")


class AppClient:
    """Talks to one Channels app."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        port: int = DEFAULT_APP_PORT,
        clock: Clock | None = None,
    ) -> None:
        """Initialize the client."""
        self.host = host
        self.port = port
        self._session = session
        self._clock = clock or SystemClock()
        self._base = f"http://{url_host(host)}:{port}/api/"

    async def _request(
        self, method: str, path: str, json: dict[str, Any] | None = None
    ) -> tuple[Any, float]:
        """Return the decoded body and the midpoint of the request."""
        started = self._clock.time()
        try:
            async with self._session.request(
                method, self._base + path, json=json, timeout=_TIMEOUT
            ) as response:
                response.raise_for_status()
                body = await response.json(content_type=None)
        except aiohttp.ClientResponseError as err:
            raise ChannelsConnectionError(
                f"Channels at {self.host} answered with an error (HTTP {err.status})"
            ) from err
        except TimeoutError as err:
            raise ChannelsConnectionError(
                f"Channels at {self.host} did not answer in time"
            ) from err
        except aiohttp.ClientError as err:
            detail = f": {err}" if str(err) else ""
            raise ChannelsConnectionError(
                f"Channels at {self.host} did not answer{detail}"
            ) from err
        except ValueError as err:
            raise ChannelsConnectionError(
                f"Channels at {self.host} sent a reply that was not valid JSON"
            ) from err
        return body, (started + self._clock.time()) / 2

    async def _command(self, path: str) -> AppStatus:
        """Send a control command; the app replies with its new status."""
        body, sampled_at = await self._request("POST", path)
        return AppStatus.from_dict(_as_object(body), sampled_at)

    async def status(self) -> AppStatus:
        """Return what the app is doing right now."""
        body, sampled_at = await self._request("GET", "status")
        return AppStatus.from_dict(_as_object(body), sampled_at)

    async def favorite_channels(self) -> list[dict[str, Any]]:
        """Return the favourite channels configured in the app."""
        body, _ = await self._request("GET", "favorite_channels")
        return body if isinstance(body, list) else []

    async def play_channel(self, number: str) -> AppStatus:
        """Tune a live channel."""
        return await self._command(f"play/channel/{_segment(number)}")

    async def play_recording(self, recording_id: str) -> AppStatus:
        """Play a recording, from the DVR's saved position for it."""
        return await self._command(f"play/recording/{_segment(recording_id)}")

    async def pause(self) -> AppStatus:
        """Pause playback."""
        return await self._command("pause")

    async def resume(self) -> AppStatus:
        """Resume playback."""
        return await self._command("resume")

    async def stop(self) -> AppStatus:
        """Stop playback."""
        return await self._command("stop")

    async def toggle_pause(self) -> AppStatus:
        """Toggle between playing and paused."""
        return await self._command("toggle_pause")

    async def seek(self, seconds: float) -> AppStatus:
        """Seek relative to the current position; negative seeks backwards."""
        if not math.isfinite(seconds):
            raise ChannelsError("The seek amount must be a finite number")
        return await self._command(f"seek/{seconds:.3f}")

    async def seek_forward(self) -> AppStatus:
        """Seek ahead by the amount set in the app's settings."""
        return await self._command("seek_forward")

    async def seek_backward(self) -> AppStatus:
        """Seek back by the amount set in the app's settings."""
        return await self._command("seek_backward")

    async def skip_forward(self) -> AppStatus:
        """Skip to the next commercial marker."""
        return await self._command("skip_forward")

    async def skip_backward(self) -> AppStatus:
        """Skip to the previous commercial marker."""
        return await self._command("skip_backward")

    async def toggle_mute(self) -> AppStatus:
        """Toggle mute."""
        return await self._command("toggle_mute")

    async def toggle_cc(self) -> AppStatus:
        """Toggle closed captions."""
        return await self._command("toggle_cc")

    async def toggle_pip(self) -> AppStatus:
        """Toggle picture in picture."""
        return await self._command("toggle_pip")

    async def toggle_record(self) -> AppStatus:
        """Start, or stop, recording the programme on the current channel.

        This is a toggle: if the programme is already being recorded, calling
        it stops that recording. Check the DVR server first.
        """
        return await self._command("toggle_record")

    async def navigate(self, section: str) -> None:
        """Open a section of the app's sidebar by name."""
        await self._request("POST", f"navigate/{_segment(section)}")

    async def notify(self, title: str, message: str, icon: str | None = None) -> None:
        """Show an on-screen notification while video is playing."""
        payload = {"title": title, "message": message}
        if icon:
            payload["icon"] = icon
        await self._request("POST", "notify", json=payload)
