"""Tests for parsing the two APIs' JSON."""

from custom_components.channels.lib.models import AppStatus, FailedJob, Recording

from ..fixtures import (
    EPISODE_COMPLETED,
    EPISODE_IN_PROGRESS,
    JOB_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_LIVE,
    STATUS_RECORDING,
    STATUS_STOPPED,
)


def test_stopped_status():
    status = AppStatus.from_dict(STATUS_STOPPED, 100.0)

    assert status.state == "stopped"
    assert status.is_active is False
    assert status.position is None
    assert status.recording_id is None
    assert status.channel_number is None
    assert status.sampled_at == 100.0


def test_live_tv_has_a_channel_and_no_position_or_recording():
    status = AppStatus.from_dict(STATUS_LIVE, 100.0)

    assert status.is_active is True
    assert status.channel_number == "6.1"
    assert status.channel_name == "KAAA"
    assert status.title == "NBC News Daily"
    assert status.position is None
    assert status.recording_id is None


def test_recording_has_a_position_and_an_id_from_the_thumbnail():
    status = AppStatus.from_dict(STATUS_RECORDING, 100.0)

    assert status.recording_id == "15017"
    assert status.position == 311.9480165
    assert status.duration == 1800.384966
    assert status.channel_number is None
    assert status.title == "Jeopardy!"
    assert status.episode_title == "September 24, 2025"
    assert status.season_number == 42
    assert status.episode_number == 13
    assert status.content_type == "tv"


def test_in_progress_recording_has_no_duration():
    status = AppStatus.from_dict(STATUS_IN_PROGRESS, 100.0)

    assert status.recording_id == "15018"
    assert status.duration is None
    assert status.state == "paused"
    assert status.muted is True


def test_empty_body_is_treated_as_stopped():
    status = AppStatus.from_dict({}, 100.0)

    assert status.state == "stopped"
    assert status.muted is False


def test_recording_times_are_converted_from_milliseconds():
    done = Recording.from_dict(EPISODE_COMPLETED)
    live = Recording.from_dict(EPISODE_IN_PROGRESS)

    assert (done.id, done.completed, done.duration) == ("15017", True, 1800.384966)
    assert (live.id, live.completed, live.duration) == ("15018", False, None)
    assert live.channel == "6.1"
    assert live.created_at == 1790879087.0


def test_failed_job():
    job = FailedJob.from_dict(JOB_FAILED)

    assert job.channel == "6.1"
    assert "No Video Data" in job.error
    assert job.updated_at == 1790667016.656
