"""Data models for the Channels app and DVR server APIs."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

_FILE_ID_RE = re.compile(r"/dvr/files/(\d+)/")

STATE_PLAYING = "playing"
STATE_PAUSED = "paused"
STATE_STOPPED = "stopped"


class ChannelsError(Exception):
    """Base error for everything in this library."""


class ChannelsConnectionError(ChannelsError):
    """The app or server did not answer."""


@dataclass(frozen=True, slots=True)
class AppStatus:
    """What a Channels app is doing, as reported by GET /api/status."""

    state: str
    muted: bool
    sampled_at: float
    position: float | None = None
    duration: float | None = None
    recording_id: str | None = None
    channel_number: str | None = None
    channel_name: str | None = None
    title: str | None = None
    episode_title: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    summary: str | None = None
    image_url: str | None = None
    content_type: str | None = None

    @property
    def is_active(self) -> bool:
        """Return True when something is playing or paused."""
        return self.state in (STATE_PLAYING, STATE_PAUSED)

    @classmethod
    def from_dict(cls, data: dict[str, Any], sampled_at: float) -> AppStatus:
        """Build a status from the API's JSON.

        The API omits keys whose value is null, so every lookup is optional.
        """
        channel = data.get("channel") or {}
        playing = data.get("now_playing") or {}
        match = _FILE_ID_RE.search(playing.get("thumb_url") or "")
        # An in-progress recording reports duration 0; treat that as unknown.
        duration = playing.get("duration") or None
        return cls(
            state=data.get("status", STATE_STOPPED),
            muted=bool(data.get("muted", False)),
            sampled_at=sampled_at,
            position=data.get("playback_time"),
            duration=duration,
            recording_id=match.group(1) if match else None,
            channel_number=channel.get("number"),
            channel_name=channel.get("name"),
            title=playing.get("title"),
            episode_title=playing.get("episode_title"),
            season_number=playing.get("season_number"),
            episode_number=playing.get("episode_number"),
            summary=playing.get("summary"),
            image_url=playing.get("image_url"),
            content_type=playing.get("type"),
        )


@dataclass(frozen=True, slots=True)
class Recording:
    """A file on the DVR server."""

    id: str
    channel: str | None
    completed: bool
    created_at: float
    title: str | None = None
    duration: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Recording:
        """Build a recording from an /api/v1 episode or movie."""
        return cls(
            id=str(data["id"]),
            channel=data.get("channel"),
            completed=bool(data.get("completed", False)),
            # The server reports milliseconds since the epoch.
            created_at=float(data.get("created_at", 0)) / 1000,
            title=data.get("title"),
            duration=data.get("duration") or None,
        )


@dataclass(frozen=True, slots=True)
class FailedJob:
    """A recording job the DVR server could not run."""

    name: str
    channel: str | None
    error: str
    updated_at: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FailedJob:
        """Build a failed job from an /api/v1/jobs entry."""
        return cls(
            name=data.get("name", ""),
            channel=data.get("channel"),
            error=data.get("error") or "unknown error",
            updated_at=float(data.get("updated_at", 0)) / 1000,
        )


def url_host(host: str) -> str:
    """Return a host as it goes in a URL: an IPv6 address in square brackets."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host
