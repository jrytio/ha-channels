"""Constants for the Channels integration."""

from datetime import timedelta

DOMAIN = "channels"
MANUFACTURER = "Fancy Bits"

CONF_KIND = "kind"
KIND_APP = "app"
KIND_DVR = "dvr"

CONF_SYNC_OFFSET_MS = "sync_offset_ms"
MAX_SYNC_OFFSET_MS = 500

ZEROCONF_APP = "_channels_app._tcp.local."
ZEROCONF_DVR = "_channels_dvr._tcp.local."

SCAN_REACHABLE = timedelta(seconds=5)
SCAN_UNREACHABLE = timedelta(seconds=10)

SERVICE_SEEK_BY = "seek_by"
SERVICE_SEEK_FORWARD = "seek_forward"
SERVICE_SEEK_BACKWARD = "seek_backward"
SERVICE_SWITCH_TO_RECORDING = "switch_to_recording"
SERVICE_SYNC_PLAYBACK = "sync_playback"
SERVICE_START_FOLLOW = "start_follow"
SERVICE_STOP_FOLLOW = "stop_follow"

ATTR_SECONDS = "seconds"
ATTR_BEHIND_LIVE = "behind_live"
ATTR_LEADER = "leader"
ATTR_FOLLOWERS = "followers"
ATTR_FOLLOWER_TIMEOUT = "follower_timeout"
ATTR_TOLERANCE_MS = "tolerance_ms"
ATTR_LIVE_SETTLE = "live_settle"

EVENT_FOLLOW_PROBLEM = "channels_follow_problem"
SIGNAL_FOLLOW_UPDATED = "channels_follow_updated"
