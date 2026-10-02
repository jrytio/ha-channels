# Channels for Home Assistant

A Home Assistant integration for [Channels](https://getchannels.com): the app
on each TV, and the Channels DVR server.

It uses the domain `channels`, so **it replaces Home Assistant's built-in
Channels integration** while it is installed. The two cannot run side by side.

## What it does

- Finds Channels apps and the DVR server on your network.
- Adds a media player for each TV with the exact playback position and the
  ID of the recording being played.
- **Syncs TVs**: puts other TVs on the recording one TV is playing, at the
  same position, typically within 50 ms.
- Moves a TV from live TV onto the recording of the same programme, starting
  a recording if there is none.

A Channels app only answers while it is open and in front on its TV. Its
media player shows as unavailable the rest of the time.

Developed against Apple TV. Android TV and Fire TV are untested.

## Install

Remove any `platform: channels` media player from your YAML configuration
first. The built-in integration's YAML setup is not supported.

1. In HACS, open the menu, choose **Custom repositories**, and add
   `https://github.com/jrytio/ha-channels` with the type **Integration**.
2. Install **Channels** and restart Home Assistant.
3. Open Channels on each TV. Each one, and the DVR server, appears under
   **Settings → Devices & services → Discovered**. One DVR server is
   supported; a second is refused until the first is removed.

## Actions

### `channels.sync_playback`

| Field | Default | |
| --- | --- | --- |
| `leader` | required | The Channels player to copy. Must be on a recording |
| `followers` | required | The Channels players to bring in line |
| `follower_timeout` | 30 | Seconds to wait for each follower's app to answer |
| `tolerance_ms` | 50 | How close counts as synced, in ms. Minimum 50 |

Returns a response and must be called with one:

```yaml
error: null
followers:
  media_player.channels_office:
    status: synced        # synced | out_of_tolerance | skipped | not_set_up
    offset_ms: -33.7
    rounds: 3
```

Each follower gets one of four statuses:

- `synced`: it is within `tolerance_ms` of where it should be.
- `out_of_tolerance`: it was brought as close as five corrections allowed,
  but not within `tolerance_ms`.
- `skipped`: it was not synced; `reason` says why (it never answered, it is
  the leader, its Channels entry is not loaded, the leader stopped, it left the
  recording, and so on).
- `not_set_up`: no Channels media player exists by that entity ID. A script
  can list a room ahead of its TV being added and ignore this status.

`offset_ms` is the measured offset of the follower relative to the leader;
negative means behind. A TV with a +80 ms sync offset is aimed 80 ms behind
the leader, so it reports about −80 when synced.

A follower's app does not need to be reachable when the action is called;
the action waits for it. Launch Channels on the follower first.

### `channels.switch_to_recording`

Takes one or more Channels players as its target and returns a result per
player; they are switched one at a time. If a player is on live TV, finds the
recording in progress on that channel, or starts one, and switches the TV to
it `behind_live` seconds (default 5, minimum 3) behind the live edge. A new
recording is too young to play that far back at first, so the TV stays on
live TV until the recording is `behind_live` seconds old, then switches.
Needs a DVR server to be set up, unless the TV is already on a recording.

```yaml
action: channels.switch_to_recording
target:
  entity_id: media_player.channels_living_room
data:
  behind_live: 5
response_variable: switched
```

```yaml
media_player.channels_living_room:
  recording_id: "15018"
  switched: true
  started_recording: true
  error: null
```

On failure, `started_recording` can still be `true`. That means a recording
was started and is running. Do not retry blindly: a second call made before
that recording shows up on the DVR server would toggle it off. Read `error`
first.

Both actions report operational failures in `error`, so a script can read
the reason. They raise only for invalid input: a target or leader that is not
a Channels media player, or a field out of range.

### `channels.seek_by`, `channels.seek_forward`, `channels.seek_backward`

Kept from the built-in integration. `seek_by` takes fractional seconds.

## Options

Each TV has a **sync offset** in milliseconds. A positive value makes that TV
play later than the leader when syncing.

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

`custom_components/channels/lib/` has no Home Assistant imports. Try engine
changes against real TVs without restarting Home Assistant:

```bash
uv run python -m scripts.bench status 10.0.0.1 10.0.0.2
uv run python -m scripts.bench sync 10.0.0.1 10.0.0.2 --monitor 20
```
