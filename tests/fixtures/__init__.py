"""Response bodies captured from real devices on 2026-10-01."""

STATUS_STOPPED = {"status": "stopped", "muted": False}

STATUS_LIVE = {
    "status": "playing",
    "muted": False,
    "channel": {
        "number": "6.1",
        "name": "KAAA",
        "image_url": "https://tmsimg.fancybits.co/assets/s28717_ll_h15_ad.png?w=180",
    },
    "now_playing": {
        "identifiers": {"gracenote": "22712535"},
        "title": "NBC News Daily",
        "image_url": "https://tmsimg.fancybits.co/assets/p22712535_b_h9_ad.jpg?w=720",
        "type": "tv",
        "episode_number": 182,
        "summary": "NBC News provides viewers with up-to-the-minute news.",
    },
}

STATUS_RECORDING = {
    "status": "playing",
    "muted": False,
    "playback_time": 311.9480165,
    "now_playing": {
        "summary": "The Emmy-winning quiz show.",
        "identifiers": {"gracenote": "184056", "tmdb": "2912"},
        "episode_number": 13,
        "season_number": 42,
        "title": "Jeopardy!",
        "duration": 1800.384966,
        "image_url": "https://tmsimg.fancybits.co/assets/p184056_b_h9_aa.jpg?w=720",
        "type": "tv",
        "episode_title": "September 24, 2025",
        "thumb_url": "http://10.0.0.9:8089/dvr/files/15017/preview.jpg",
    },
}

STATUS_IN_PROGRESS = {
    "status": "paused",
    "muted": True,
    "playback_time": 9.5298835,
    "now_playing": {
        "title": "Aging Untold",
        "duration": 0,
        "type": "tv",
        "thumb_url": "http://10.0.0.9:8089/dvr/files/15018/preview.jpg?unprocessed",
    },
}

FAVORITE_CHANNELS = [
    {"number": "3.1", "call_sign": "KBBB", "name": "KBBB", "hd": True},
    {"number": "6.1", "call_sign": "KAAA", "name": "KAAA", "hd": True},
]

EPISODE_COMPLETED = {
    "id": "15017",
    "channel": "6.1",
    "title": "Jeopardy!",
    "duration": 1800.384966,
    "completed": True,
    "created_at": 1790838000000,
}

EPISODE_IN_PROGRESS = {
    "id": "15018",
    "channel": "6.1",
    "title": "Aging Untold",
    "completed": False,
    "created_at": 1790879087000,
}

EPISODE_IN_PROGRESS_OLDER = {
    "id": "15016",
    "channel": "6.1",
    "title": "Left running",
    "completed": False,
    "created_at": 1790870000000,
}

EPISODE_IN_PROGRESS_OTHER_CHANNEL = {
    "id": "15019",
    "channel": "3.1",
    "title": "Something else",
    "completed": False,
    "created_at": 1790879090000,
}

JOB_FAILED = {
    "id": "1790665200-7",
    "name": "Jeopardy!",
    "channel": "6.1",
    "channels": ["6.1"],
    "skipped": False,
    "failed": True,
    "error": "could not start stream on channels=[6.1]: HDHomeRun: No Video Data",
    "updated_at": 1790667016656,
}

JOB_OK = {
    "id": "1790879087-ch6.1",
    "name": "Aging Untold",
    "channel": "6.1",
    "channels": ["6.1"],
    "skipped": False,
    "failed": False,
    "error": "",
    "updated_at": 1790879087000,
}

# A running manual recording: the only kind of job seen with a `channel` key.
# start_time and end_time are the programme's window, in seconds.
JOB_MANUAL_RUNNING = {
    "id": "1790945529-ch6.1",
    "name": "Today",
    "start_time": 1790938800,
    "end_time": 1790946000,
    "duration": 7200,
    "channels": ["6.1"],
    "channel": "6.1",
    "skipped": False,
    "failed": False,
    "updated_at": 1790945530721,
    "item": {"title": "Today", "program_id": "EP019152145452"},
}

# A scheduled job lists its channel only in `channels`.
JOB_SCHEDULED = {
    "id": "1790949600-7",
    "name": "Evening News",
    "start_time": 1790949600,
    "end_time": 1790951400,
    "duration": 1800,
    "channels": ["3.1"],
    "skipped": False,
    "failed": False,
    "updated_at": 1790900000000,
    "item": {"title": "Evening News"},
}

JOB_SCHEDULED_FAILED = {
    "id": "1790942400-7",
    "name": "Midday",
    "start_time": 1790942400,
    "end_time": 1790944200,
    "duration": 1800,
    "channels": ["3.1"],
    "skipped": False,
    "failed": True,
    "error": "could not start stream on channels=[3.1]: HDHomeRun: No Video Data",
    "updated_at": 1790942410000,
}

DVR_STATUS = {
    "name": "channels-dvr",
    "os": "linux",
    "arch": "x86_64",
    "version": "2026.08.07.0346",
    "local_port": 8089,
}
