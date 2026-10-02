"""Client for the Channels DVR server API on port 8089.

Deliberately has no method for GET /dvr/files: the unbounded listing is tens
of megabytes and has crashed servers.
"""

from __future__ import annotations

from typing import Any

import aiohttp

from .models import ChannelsConnectionError, FailedJob, Recording, url_host

DEFAULT_DVR_PORT = 8089
_TIMEOUT = aiohttp.ClientTimeout(total=10)
_RECENT = "sort=date_added&order=desc&limit=20"


def _on_channel(job: dict[str, Any], channel: str) -> bool:
    """Return whether a job is on a channel.

    Only a manual recording's job has `channel`; a scheduled one lists its
    channel only in `channels`.
    """
    channels = job.get("channels")
    return job.get("channel") == channel or (
        isinstance(channels, list) and channel in channels
    )


def _jobs(body: Any) -> list[dict[str, Any]]:
    return (
        [item for item in body if isinstance(item, dict)]
        if isinstance(body, list)
        else []
    )


class DvrClient:
    """Talks to one Channels DVR server."""

    def __init__(
        self, host: str, session: aiohttp.ClientSession, port: int = DEFAULT_DVR_PORT
    ) -> None:
        """Initialize the client."""
        self.host = host
        self.port = port
        self._session = session
        self._base = f"http://{url_host(host)}:{port}"

    async def _get(self, path: str) -> Any:
        try:
            async with self._session.get(
                self._base + path, timeout=_TIMEOUT
            ) as response:
                response.raise_for_status()
                return await response.json(content_type=None)
        except aiohttp.ClientResponseError as err:
            raise ChannelsConnectionError(
                f"Channels DVR at {self.host} answered with an error "
                f"(HTTP {err.status})"
            ) from err
        except TimeoutError as err:
            raise ChannelsConnectionError(
                f"Channels DVR at {self.host} did not answer in time"
            ) from err
        except aiohttp.ClientError as err:
            detail = f": {err}" if str(err) else ""
            raise ChannelsConnectionError(
                f"Channels DVR at {self.host} did not answer{detail}"
            ) from err
        except ValueError as err:
            raise ChannelsConnectionError(
                f"Channels DVR at {self.host} sent a reply that was not valid JSON"
            ) from err

    async def status(self) -> dict[str, Any]:
        """Return the server's version and platform details."""
        body = await self._get("/status")
        return body if isinstance(body, dict) else {}

    async def in_progress_recording(self, channel: str) -> Recording | None:
        """Return the newest recording still being written on a channel."""
        candidates: list[Recording] = []
        for kind in ("episodes", "movies"):
            body = await self._get(f"/api/v1/{kind}?{_RECENT}")
            for item in body if isinstance(body, list) else []:
                recording = Recording.from_dict(item)
                if not recording.completed and recording.channel == channel:
                    candidates.append(recording)
        return max(candidates, key=lambda r: r.created_at, default=None)

    async def has_active_job(self, channel: str, now: float) -> bool:
        """Return whether a job is recording on a channel at `now`.

        That is a job that has neither failed nor been skipped and whose
        programme window, `start_time` to `end_time` in seconds, covers `now`.
        """
        for job in _jobs(await self._get("/api/v1/jobs")):
            start, end = job.get("start_time"), job.get("end_time")
            if (
                not job.get("failed")
                and not job.get("skipped")
                and isinstance(start, int | float)
                and isinstance(end, int | float)
                and start <= now < end
                and _on_channel(job, channel)
            ):
                return True
        return False

    async def recent_failed_job(self, channel: str, since: float) -> FailedJob | None:
        """Return the latest job on a channel that failed at or after `since`."""
        body = await self._get("/api/v1/jobs")
        failed = [
            FailedJob.from_dict(item)
            for item in _jobs(body)
            if item.get("failed") and _on_channel(item, channel)
        ]
        recent = [job for job in failed if job.updated_at >= since]
        return max(recent, key=lambda j: j.updated_at, default=None)
