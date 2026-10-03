"""Channels app and DVR clients, and the sync engine. No Home Assistant imports."""

from .app_client import DEFAULT_APP_PORT, AppClient
from .clock import Clock, SystemClock
from .dvr_client import DEFAULT_DVR_PORT, DvrClient
from .follow import Follower, FollowSession
from .models import (
    AppStatus,
    ChannelsConnectionError,
    ChannelsError,
    FailedJob,
    Recording,
)
from .recording import ChannelChanged, SwitchError, SwitchResult, switch_to_recording
from .sync import LeaderError, SyncResult, sync_follower, wait_until_reachable

__all__ = [
    "DEFAULT_APP_PORT",
    "DEFAULT_DVR_PORT",
    "AppClient",
    "AppStatus",
    "ChannelChanged",
    "ChannelsConnectionError",
    "ChannelsError",
    "Clock",
    "DvrClient",
    "FailedJob",
    "FollowSession",
    "Follower",
    "LeaderError",
    "Recording",
    "SwitchError",
    "SwitchResult",
    "SyncResult",
    "SystemClock",
    "switch_to_recording",
    "sync_follower",
    "wait_until_reachable",
]
