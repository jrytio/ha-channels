"""Tests for the two HTTP clients, against a local stand-in server."""

import asyncio

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from custom_components.channels.lib import app_client, dvr_client
from custom_components.channels.lib.app_client import AppClient
from custom_components.channels.lib.dvr_client import DvrClient
from custom_components.channels.lib.models import (
    ChannelsConnectionError,
    ChannelsError,
    url_host,
)

from ..fixtures import (
    DVR_STATUS,
    EPISODE_COMPLETED,
    EPISODE_IN_PROGRESS,
    EPISODE_IN_PROGRESS_OLDER,
    EPISODE_IN_PROGRESS_OTHER_CHANNEL,
    FAVORITE_CHANNELS,
    JOB_FAILED,
    JOB_MANUAL_RUNNING,
    JOB_OK,
    JOB_SCHEDULED,
    JOB_SCHEDULED_FAILED,
    STATUS_RECORDING,
)


class Stand:
    """A local HTTP server that records requests and returns canned JSON."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.raw_paths: list[str] = []
        self.bodies: list[str] = []
        self.replies: dict[str, object] = {}
        self.default: object = STATUS_RECORDING
        self.status = 200
        self.raw: str | None = None
        self.delay = 0.0
        self.server: TestServer | None = None

    async def _handle(self, request: web.Request) -> web.Response:
        self.requests.append((request.method, request.path_qs))
        self.raw_paths.append(request.raw_path)
        self.bodies.append(await request.text())
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raw is not None:
            return web.Response(text=self.raw, status=self.status)
        reply = self.replies.get(request.path, self.default)
        return web.json_response(reply, status=self.status)

    async def start(self) -> None:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self.server = TestServer(app)
        await self.server.start_server()

    @property
    def port(self) -> int:
        return self.server.port


@pytest.fixture
async def stand(socket_enabled):
    stand = Stand()
    await stand.start()
    yield stand
    await stand.server.close()


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as session:
        yield session


@pytest.fixture
def app(stand, session) -> AppClient:
    return AppClient("127.0.0.1", session, port=stand.port)


@pytest.fixture
def dvr(stand, session) -> DvrClient:
    return DvrClient("127.0.0.1", session, port=stand.port)


async def test_status_is_parsed_and_timestamped(app, stand):
    status = await app.status()

    assert stand.requests == [("GET", "/api/status")]
    assert status.recording_id == "15017"
    assert status.sampled_at > 0


@pytest.mark.parametrize(
    ("call", "path"),
    [
        (lambda c: c.play_channel("6.1"), "/api/play/channel/6.1"),
        (lambda c: c.play_recording("15017"), "/api/play/recording/15017"),
        (lambda c: c.pause(), "/api/pause"),
        (lambda c: c.resume(), "/api/resume"),
        (lambda c: c.stop(), "/api/stop"),
        (lambda c: c.toggle_pause(), "/api/toggle_pause"),
        (lambda c: c.seek(10.5), "/api/seek/10.500"),
        (lambda c: c.seek(-3), "/api/seek/-3.000"),
        (lambda c: c.seek_forward(), "/api/seek_forward"),
        (lambda c: c.seek_backward(), "/api/seek_backward"),
        (lambda c: c.skip_forward(), "/api/skip_forward"),
        (lambda c: c.skip_backward(), "/api/skip_backward"),
        (lambda c: c.toggle_mute(), "/api/toggle_mute"),
        (lambda c: c.toggle_cc(), "/api/toggle_cc"),
        (lambda c: c.toggle_pip(), "/api/toggle_pip"),
        (lambda c: c.toggle_record(), "/api/toggle_record"),
    ],
)
async def test_commands_post_and_return_the_new_status(app, stand, call, path):
    status = await call(app)

    assert stand.requests == [("POST", path)]
    assert status.recording_id == "15017"


@pytest.mark.parametrize(
    ("call", "raw_path"),
    [
        (
            lambda c: c.play_recording("../../toggle_record"),
            "/api/play/recording/..%2F..%2Ftoggle_record",
        ),
        # yarl leaves "=" bare: it is legal in a path and not a separator.
        (lambda c: c.play_channel("6.1?x=1#y"), "/api/play/channel/6.1%3Fx=1%23y"),
        (lambda c: c.navigate("a/../../stop"), "/api/navigate/a%2F..%2F..%2Fstop"),
    ],
)
async def test_ids_reach_the_app_as_one_path_segment(app, stand, call, raw_path):
    await call(app)

    assert stand.raw_paths == [raw_path]
    assert stand.requests[0][1] != "/api/toggle_record"


async def test_a_plain_channel_number_is_not_encoded(app, stand):
    await app.play_channel("6.1")

    assert stand.raw_paths == ["/api/play/channel/6.1"]


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), float("-inf")])
async def test_seek_rejects_a_non_finite_amount(app, stand, amount):
    with pytest.raises(ChannelsError, match="The seek amount must be a finite number"):
        await app.seek(amount)

    assert stand.requests == []


async def test_favorite_channels(app, stand):
    stand.replies["/api/favorite_channels"] = FAVORITE_CHANNELS

    assert [c["number"] for c in await app.favorite_channels()] == ["3.1", "6.1"]


async def test_favorite_channels_tolerates_a_non_list(app, stand):
    stand.replies["/api/favorite_channels"] = {"status": "error"}

    assert await app.favorite_channels() == []


async def test_notify_sends_title_message_and_icon(app, stand):
    await app.notify("Alert", "Dad is home", icon="fa:door")

    assert stand.requests == [("POST", "/api/notify")]
    assert '"title": "Alert"' in stand.bodies[0]
    assert '"icon": "fa:door"' in stand.bodies[0]


async def test_navigate(app, stand):
    await app.navigate("Library")

    assert stand.requests == [("POST", "/api/navigate/Library")]


async def test_app_that_does_not_answer_raises_a_connection_error(session, stand):
    await stand.server.close()

    with pytest.raises(ChannelsConnectionError, match="did not answer"):
        await AppClient("127.0.0.1", session, port=stand.port).status()


async def test_http_error_raises_a_connection_error(app, stand):
    stand.status = 500

    with pytest.raises(ChannelsConnectionError):
        await app.status()


async def test_dvr_status(dvr, stand):
    stand.replies["/status"] = DVR_STATUS

    assert (await dvr.status())["version"] == "2026.08.07.0346"


async def test_in_progress_recording_picks_the_newest_on_the_channel(dvr, stand):
    stand.replies["/api/v1/episodes"] = [
        EPISODE_IN_PROGRESS_OTHER_CHANNEL,
        EPISODE_IN_PROGRESS,
        EPISODE_IN_PROGRESS_OLDER,
        EPISODE_COMPLETED,
    ]
    stand.replies["/api/v1/movies"] = []

    recording = await dvr.in_progress_recording("6.1")

    assert recording.id == "15018"
    assert stand.requests == [
        ("GET", "/api/v1/episodes?sort=date_added&order=desc&limit=20"),
        ("GET", "/api/v1/movies?sort=date_added&order=desc&limit=20"),
    ]


async def test_in_progress_recording_can_be_a_movie(dvr, stand):
    stand.replies["/api/v1/episodes"] = [EPISODE_COMPLETED]
    stand.replies["/api/v1/movies"] = [EPISODE_IN_PROGRESS]

    assert (await dvr.in_progress_recording("6.1")).id == "15018"


async def test_no_in_progress_recording(dvr, stand):
    stand.replies["/api/v1/episodes"] = [EPISODE_COMPLETED]
    stand.replies["/api/v1/movies"] = []

    assert await dvr.in_progress_recording("6.1") is None


async def test_recent_failed_job_ignores_old_and_successful_jobs(dvr, stand):
    stand.replies["/api/v1/jobs"] = [JOB_OK, JOB_FAILED]

    found = await dvr.recent_failed_job("6.1", since=1790667000.0)
    too_late = await dvr.recent_failed_job("6.1", since=1790667100.0)
    other_channel = await dvr.recent_failed_job("3.1", since=0.0)

    assert "No Video Data" in found.error
    assert too_late is None
    assert other_channel is None


async def test_recent_failed_job_finds_a_job_listed_only_by_channels(dvr, stand):
    stand.replies["/api/v1/jobs"] = [JOB_MANUAL_RUNNING, JOB_SCHEDULED_FAILED]

    found = await dvr.recent_failed_job("3.1", since=0.0)

    assert "No Video Data" in found.error
    assert await dvr.recent_failed_job("6.1", since=0.0) is None


@pytest.mark.parametrize(
    ("job", "channel", "now", "expected"),
    [
        (JOB_MANUAL_RUNNING, "6.1", 1790945600.0, True),
        (JOB_SCHEDULED, "3.1", 1790950000.0, True),
        (JOB_SCHEDULED, "3.1", 1790949600.0, True),
        (JOB_SCHEDULED, "3.1", 1790949599.0, False),
        (JOB_SCHEDULED, "3.1", 1790951400.0, False),
        ({**JOB_MANUAL_RUNNING, "failed": True}, "6.1", 1790945600.0, False),
        ({**JOB_MANUAL_RUNNING, "skipped": True}, "6.1", 1790945600.0, False),
        (JOB_MANUAL_RUNNING, "3.1", 1790945600.0, False),
        (JOB_SCHEDULED, "6.1", 1790950000.0, False),
        (JOB_OK, "6.1", 1790879090.0, False),  # no window: not known to be active
    ],
    ids=[
        "running-by-channel",
        "running-by-channels-only",
        "at-start",
        "before-window",
        "at-end",
        "failed",
        "skipped",
        "other-channel",
        "other-channel-channels-only",
        "no-window",
    ],
)
async def test_has_active_job(dvr, stand, job, channel, now, expected):
    stand.replies["/api/v1/jobs"] = [job]

    assert await dvr.has_active_job(channel, now) is expected
    assert stand.requests == [("GET", "/api/v1/jobs")]


async def test_has_active_job_tolerates_a_non_list(dvr, stand):
    stand.replies["/api/v1/jobs"] = {"error": "nope"}

    assert await dvr.has_active_job("6.1", 1790945600.0) is False


async def test_dvr_that_does_not_answer_raises_a_connection_error(session, stand):
    await stand.server.close()

    with pytest.raises(ChannelsConnectionError, match="did not answer"):
        await DvrClient("127.0.0.1", session, port=stand.port).status()


async def test_app_reply_that_is_not_json_raises_a_connection_error(app, stand):
    stand.raw = "<html>Bad gateway</html>"

    with pytest.raises(ChannelsConnectionError, match="not valid JSON"):
        await app.status()


async def test_dvr_reply_that_is_not_json_raises_a_connection_error(dvr, stand):
    stand.raw = "<html>Bad gateway</html>"

    with pytest.raises(ChannelsConnectionError, match="not valid JSON"):
        await dvr.status()


@pytest.mark.parametrize("body", [["a", "b"], "not an object", 3])
async def test_app_status_that_is_not_an_object_is_read_as_empty(app, stand, body):
    stand.default = body

    status = await app.status()
    after_command = await app.pause()

    assert status.state == "stopped"
    assert status.recording_id is None
    assert after_command.state == "stopped"


async def test_app_that_does_not_answer_says_why_without_a_dangling_colon(
    session, stand
):
    await stand.server.close()

    with pytest.raises(ChannelsConnectionError) as caught:
        await AppClient("127.0.0.1", session, port=stand.port).status()

    message = str(caught.value)
    assert message.startswith("Channels at 127.0.0.1 did not answer: ")
    assert len(message) > len("Channels at 127.0.0.1 did not answer: ")


async def test_app_timeout_says_it_did_not_answer_in_time(app, stand, monkeypatch):
    monkeypatch.setattr(app_client, "_TIMEOUT", aiohttp.ClientTimeout(total=0.05))
    stand.delay = 0.5

    with pytest.raises(ChannelsConnectionError) as caught:
        await app.status()

    assert str(caught.value) == "Channels at 127.0.0.1 did not answer in time"


async def test_app_http_error_names_the_status(app, stand):
    stand.status = 503

    with pytest.raises(ChannelsConnectionError) as caught:
        await app.status()

    assert str(caught.value) == (
        "Channels at 127.0.0.1 answered with an error (HTTP 503)"
    )


async def test_dvr_timeout_says_it_did_not_answer_in_time(dvr, stand, monkeypatch):
    monkeypatch.setattr(dvr_client, "_TIMEOUT", aiohttp.ClientTimeout(total=0.05))
    stand.delay = 0.5

    with pytest.raises(ChannelsConnectionError) as caught:
        await dvr.status()

    assert str(caught.value) == "Channels DVR at 127.0.0.1 did not answer in time"


async def test_dvr_http_error_names_the_status(dvr, stand):
    stand.status = 404

    with pytest.raises(ChannelsConnectionError) as caught:
        await dvr.status()

    assert str(caught.value) == (
        "Channels DVR at 127.0.0.1 answered with an error (HTTP 404)"
    )


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("10.0.0.1", "10.0.0.1"),
        ("tv.local", "tv.local"),
        ("fe80::1", "[fe80::1]"),
        ("[fe80::1]", "[fe80::1]"),
    ],
)
def test_url_host_brackets_an_ipv6_address(host, expected):
    assert url_host(host) == expected


def test_clients_build_a_valid_url_for_an_ipv6_host():
    assert AppClient("fe80::1", None)._base == "http://[fe80::1]:57000/api/"
    assert DvrClient("fe80::1", None)._base == "http://[fe80::1]:8089"
