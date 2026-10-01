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

Tested on Apple TV. Android TV and Fire TV are untested.

## Install

1. In HACS, open the menu, choose **Custom repositories**, and add
   `https://github.com/jrytio/ha-channels` with the type **Integration**.
2. Install **Channels** and restart Home Assistant.
3. Open Channels on each TV. Each one, and the DVR server, appears under
   **Settings → Devices & services → Discovered**.

## Actions

### `channels.sync_playback`

| Field | Default | |
| --- | --- | --- |
| `leader` | required | The Channels player to copy. Must be on a recording |
| `followers` | required | The Channels players to bring in line |
| `follower_timeout` | 30 | Seconds to wait for each follower's app to answer |
| `tolerance_ms` | 50 | How close counts as synced |

Returns a response and must be called with one:

```yaml
error: null
followers:
  media_player.channels_office:
    status: synced        # synced | out_of_tolerance | skipped | not_set_up
    offset_ms: -33.7
    rounds: 3
```

A follower's app does not need to be reachable when the action is called;
the action waits for it. Launch Channels on the follower first.

### `channels.switch_to_recording`

Targets one Channels player. If it is on live TV, finds the recording in
progress on that channel, or starts one, and switches the TV to it
`behind_live` seconds (default 5, minimum 3) behind the live edge. Needs a
DVR server to be set up.

```yaml
media_player.channels_living_room:
  recording_id: "15018"
  switched: true
  started_recording: true
  error: null
```

Both actions report failures in `error` rather than raising, so a script can
read the reason.

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
