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
- **Keeps them synced**: holds other TVs to one TV for as long as you like,
  through pauses, rewinds, commercial skips and changes of programme.
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

### `channels.start_follow` and `channels.stop_follow`

`sync_playback` lines TVs up once. `start_follow` keeps them lined up: it
starts a follow session that runs until `stop_follow`, a restart of Home
Assistant, or the leader's entry being unloaded.

| Field | Default | |
| --- | --- | --- |
| `leader` | required | The Channels player the others are held to |
| `followers` | required | The Channels players to keep in line. One that does not exist yet is ignored |
| `tolerance_ms` | 250 | How far out a playing follower may be before it is corrected. 100 to 900 |
| `live_settle` | 10 | Seconds the leader must stay on an unrecorded live channel before it is recorded |
| `behind_live` | 5 | Seconds behind live the leader lands when moved onto a recording |

```yaml
action: channels.start_follow
data:
  leader: media_player.channels_living_room
  followers:
    - media_player.channels_office
    - media_player.channels_back_yard
```

```yaml
action: channels.stop_follow
data:
  leader: media_player.channels_living_room
```

Once a second the session reads the leader and each follower, and corrects
any follower that is out of line. It does not matter why: a skip on the
leader, a rewind with the follower's own remote and plain drift are treated
alike.

- A follower that is not answering (another app in front, TV off) is left
  alone, and picked up again when Channels is back in front on it. The
  session never wakes a TV or opens the app; do that before starting it.
- A follower more than a second out is seeked at once and then fine-tuned to
  within 50 ms. One between `tolerance_ms` and a second out on two readings
  running is fine-tuned. Inside `tolerance_ms` it is left alone.
- While the leader is paused the followers are paused, to within about 1.5 s
  of the leader's frame. They are tightened when play resumes.
- When the leader goes to live TV on a channel that is already being
  recorded, it is moved onto that recording at once and the followers
  follow. On a channel that is not being recorded, that happens once the
  leader has stayed there for `live_settle` seconds, and a recording is
  started. Flipping through channels faster than that records nothing.
- With no DVR server set up, a leader on live TV cannot be followed. A
  problem is reported, once per visit to live TV and only after the leader
  has stayed on one channel for `live_settle`.
- A failure to move the leader onto a recording is reported once and not
  tried again until the leader changes channel or leaves live TV. A moment
  in which the leader cannot be read does not count as leaving.
- A leader that is stopped, out of Channels, or left at the end of a
  recording is not followed; the followers stay as they are. The session
  keeps running.

Reaction time is about a second, the interval between readings.

While a session runs, each follower's media player has the attributes
`following` (the leader's entity ID) and `follow_status`, and the leader's
has `followed_by`. They are absent otherwise, and an unavailable entity
shows no attributes.

| `follow_status` | Meaning |
| --- | --- |
| `in_sync` | Within tolerance, or the leader has nothing to follow |
| `correcting` | Being brought into line |
| `resting` | Five corrections in a row failed; left alone for 30 s |
| `absent` | Its app is not answering |

Problems a person would want to hear about are fired as the event
`channels_follow_problem`, with `leader`, `follower` (null when it is about
the leader) and `message`, a sentence fit for a notification:

```yaml
triggers:
  - trigger: event
    event_type: channels_follow_problem
actions:
  - action: persistent_notification.create
    data:
      title: Channels
      message: "{{ trigger.event.data.message }}"
```

A TV can follow one leader at a time, a TV that is following cannot lead,
and a TV that is leading cannot be made a follower of another. `start_follow`
raises for any of these, and for a leader that is not a Channels media player
or whose entry is not loaded.

If no follower is left after leaving out the leader itself and TVs that do
not exist yet, no session is started, and the leader's existing session, if
it has one, is ended. Nothing is raised.

Stopping a session while a follower is in the middle of a correction leaves
that follower playing, not paused. A session that ends by itself, which
should not happen, is forgotten: its attributes go and its followers can be
followed again.

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
