# Channels integration design

This is the design of the `channels` integration, kept up to date with the
code.

## Measured behaviour

Everything below was measured on 2026-10-01 and 2026-10-02 against the Living
Room and Office Apple TVs and the DVR server on `nas6`. The design rests on
these.

### The Channels app API (port 57000 on each Apple TV)

- The app serves an unauthenticated HTTP API and advertises it over Bonjour as
  `_channels_app._tcp`. The Bonjour record carries no identifier beyond the
  host name (`Office.local`, `Living-Area.local`).
- The API answers **only while the app is in the foreground**. Switching to
  another app, or sleeping the Apple TV, makes it unreachable at once.
- `GET /api/status` while a recording plays returns `playback_time` (float
  seconds, about 17 ms resolution) and `now_playing.thumb_url`, which contains
  the DVR file ID (`/dvr/files/<id>/preview.jpg`).
- On live TV, status has `channel.number` but no `playback_time` and no file
  ID.
- `POST /api/play/recording/<id>` resumes from the DVR's saved position for
  that file, or from 0 when there is none.
- When a recording plays to its end, the app is left reporting `paused` at
  position 0, still on that recording — the state a TV is in the day after
  something was watched to the end. From there the first
  `play/recording` is dropped: its reply shows the old paused state, and a
  second later the app reports `stopped` with nothing playing, and stays so.
  An identical second play command works within a second. From `playing`,
  from `paused` mid-recording, and from `stopped`, the first play works.
- For about 1.5 s after `play/recording` the app is loading. Within about
  0.3 s it reports the recording and a position, but the position does not
  move. A seek sent then is acknowledged — the reply and the next few status
  reads show the new position — and about 1.3 s later the position snaps
  back to where playback really starts (the DVR's resume point) and plays
  from there. Playback was advancing 2.1 s after the play command; a seek
  sent then landed within 0.3 s and stayed.
- `POST /api/seek/<seconds>` is relative only. It accepts fractional and
  negative values. Large seeks land within about 0.1–1 s of the request;
  seeks under 1 s land unpredictably.
- A timed `pause` then `resume` shifts playback by the pause length plus
  45–70 ms, with no settling time. This is the precise adjustment.
- While paused, a seek lands on a keyframe, not on the position asked for:
  the keyframe at or before the target, with keyframes about 0.7–1.0 s
  apart. Seeks of +0.1 to +0.3 s moved a paused player not at all; seeks of
  ±0.5 to ±2 s landed 0.23–0.74 s short; seeks of ±5 s landed within 5 ms.
  A timed resume-then-pause from paused moves the position by the time
  asked for, less about 0.04 s on average (seven trials, +0.017 to −0.082 s).
- After a recording plays to its end the app is left in one of three states.
  Seen: `stopped`, with nothing playing (a TV that had simply played the
  recording through); `playing` at position 0, never advancing, still on
  that recording (two TVs that had been synced to the first); and `paused`
  at position 0, still on that recording. From the last, a `resume` makes it
  report `playing` with the position stuck at 0. A `play/recording` sent in
  the position-0 states is dropped once and works when resent.
- The app answers a command before acting on it: the reply to a `stop`
  still describes what was playing, and the status read about a second
  later shows `stopped`.
- Two TVs synced by reported position stayed within 35 ms of each other for
  20 minutes with no correction.

### In-progress recordings

- `POST /api/toggle_record` starts a DVR recording of the live programme
  within about 1.5 s. It is a toggle: called when that programme is already
  recording, it stops the recording.
- The new file appears in `GET /api/v1/episodes` on the DVR with
  `completed: false`, its `channel`, and `created_at` (recording start, ms).
- Live TV on the Apple TV goes straight to the HDHomeRun, not through the DVR.
  A live viewer plus a new recording therefore uses both tuners until the
  viewer switches to the file.
- Status reports `duration: 0` for an in-progress file, so the live edge is
  `now - created_at`.
- Playback cannot sit closer than about 1.8 s behind the live edge. Seeks
  near the edge are clamped or stall.
- With the leader 3.95 s behind live, a follower synced to −34 ms in four
  rounds.
- A failed recording shows in `GET /api/v1/jobs` with `failed: true` and an
  `error` string (seen: `HDHomeRun: No Video Data`).
- While a recording runs, `GET /api/v1/jobs` lists its job with
  `failed: false`, `skipped: false`, and `start_time` and `end_time`: the
  programme's window in seconds, not the moment recording began. The job
  appears within 4 s of `toggle_record`, and its file within the same time.
  When the recording is stopped its job leaves the list and its file becomes
  `completed: true`; no stale job is left behind.
- Only a manual recording's job has a `channel` key. Scheduled jobs,
  including failed ones (101 of 102 in the list seen), name their channel
  only in `channels`, a list. The raw `GET /dvr/jobs/<id>` object carries
  the job's `FileID`.
- A second `toggle_record`, sent while the TV was on live TV on a channel
  that was recording, did not stop that recording within 15 s (the app may
  have put a confirmation on screen). The DVR's web interface stops a
  recording with `DELETE /dvr/jobs/<id>`. The code still never sends the
  toggle while a recording exists, since the API documents it as a toggle.
- The DVR's HTTP `Date` header ran 0.8 s behind the machine running the
  tests (1 s resolution).

### Home Assistant

- After `media_player.turn_on` on the Apple TV entity, the Channels API stays
  down. `media_player.select_source` with `source: Channels` brings it up
  within about 2 s, from another app or after a wake. On 2026-10-02 it
  worked on both Apple TVs sent straight after `turn_on`.
- The Apple TV integration reports `media_position` in whole seconds, too
  coarse for sync.
- No Channels integration, pyscript, AppDaemon or Node-RED is installed.

### DVR server caution

Never call `GET /dvr/files` without a limit. The full listing is tens of
megabytes and has crashed servers. The DVR client has no method for it.

## Verified on real devices

On 2026-10-02 the library was run against two Apple TVs (a leader and a
follower, both running the Channels app) and a Channels DVR server, with
`scripts/bench.py` and direct calls to the app API. Addresses were of the
form `10.0.0.x`. What worked:

| Case | Result |
| --- | --- |
| Sync, both TVs on a finished recording | `synced` at +0.2 ms in 1 round |
| Follower knocked 45 s ahead | `synced` at +16.1 ms in 2 rounds |
| Follower knocked 30 s behind | `synced` at −16.9 ms in 2 rounds |
| Drift over 10–20 s after a sync | within about ±17 ms |
| Switch from live TV, nothing recording | `switched: true, started_recording: true` in 7.2 s; the TV stayed on live TV for about 6 s, then played the recording from 0.5 s, about 4.2 s behind live |
| Switch again while on that recording | `switched: false, started_recording: false` |
| Sync to that in-progress recording, leader 4–5 s behind live | `synced` at −15.7 ms in 3 rounds |
| Switch from live TV while the programme was already recording | used the existing recording, sent no toggle; the DVR kept recording and no second file appeared |
| Follower whose app had been relaunched seconds earlier (back paused on its recording) | `synced` at +31.0 ms in 2 rounds |

#### Re-test with the fixes (2026-10-02)

Run on the same two Apple TVs after the resend, wait-until-advancing and
job-guard fixes:

| Case | Result |
| --- | --- |
| Follower in the end-of-recording state on a different recording, leader on another | `synced` at −17.2 ms in 2 rounds (it was `skipped` before the fix) |
| Switch from live TV, nothing recording | 8.2 s; landed 4.5 s behind live |
| Switch from live TV, programme already recording, with a resume point | 5.5 s, no toggle; landed 5.9 s behind live (8.5 s before the fix) |
| Sync to that in-progress recording | `synced` at −16.9 ms in 2 rounds |
| DVR job list | no job listed more than one channel (102 jobs checked) |

The same session found the dropped play command, the discarded seek while
loading, the paused keyframe seek and the job list described under Measured
behaviour. The first two, and the job list as a guard for `toggle_record`,
are handled as described below; the paused keyframe seek is a known limit.

## The `channels` integration

### Repo and install

- Public repo `jrytio/ha-channels`, MIT licence, installed in HACS as a
  custom repository of category Integration.
- Domain `channels`. Home Assistant loads a custom integration in preference
  to a built-in one of the same domain, so the built-in never loads while
  this is installed. The manifest carries a `version`, which HA requires.
- `main` changes only through merged pull requests; phase 1 is built on one
  branch and merged as one pull request, then tagged `v0.1.0`.

### Layout

```
custom_components/channels/
  manifest.json  const.py  config_flow.py  coordinator.py  helpers.py
  media_player.py  services.py  services.yaml  diagnostics.py
  translations/en.json
  lib/               # nothing in here imports Home Assistant
    models.py        # status, recording and job models; errors
    clock.py         # the clock the engine is driven by
    app_client.py    # the app API on a TV
    dvr_client.py    # the DVR server API
    playback.py      # start a recording and wait until it is playing
    sync.py          # the sync engine
    recording.py     # live TV to recording
    follow.py        # the follow session: keeps followers on a leader
  follow.py          # running sessions, one per leader, as background tasks
tests/
```

`lib/` is plain async Python on `aiohttp`. Nothing in it imports Home
Assistant, so it can be run from a laptop against real TVs. Everything that
waits takes a clock as an argument, so the tests run against a simulated
player with a fake clock and take milliseconds.

### App client

Wraps every endpoint of the app API: status, favourite channels, play
channel, play recording, pause, resume, stop, toggle pause, seek (float
seconds), seek forward/backward, skip forward/backward, toggle mute, toggle
captions, toggle record, toggle picture-in-picture, navigate, notify.

`status()` returns a model that also carries the wall-clock midpoint of the
request, so two samples taken at slightly different instants can be compared.
The recording ID is parsed from `thumb_url`.

### DVR client

- `in_progress_recording(channel)` — newest file on that channel with
  `completed: false`, from `GET /api/v1/episodes` and `GET /api/v1/movies`,
  each sorted by date added, newest first, limit 20.
- `has_active_job(channel, now)` — whether `GET /api/v1/jobs` lists a job on
  that channel that has neither failed nor been skipped and whose
  `start_time <= now < end_time`. A job is on a channel when its `channel`
  equals it or its `channels` list contains it.
- `recent_failed_job(channel, since)` — a failed job on that channel, matched
  the same way, for its error text.
- `status()` — used at setup, for the device's software version, and for
  diagnostics, which keep only `version`, `os`, `arch` and `name`.

### Sync engine

Inputs: a leader client, a follower client, a tolerance (default 50 ms), and
a target offset (default 0). The target is the negative of the follower's
configured sync offset: a TV set to +80 ms is aimed 80 ms behind the leader.

1. Read the leader. It must be playing or paused with a recording ID.
2. If the follower is not on that recording, or is on it but paused at
   position 0 while the leader plays, start it there (see "Starting
   playback" below) and wait up to 15 s for playback to be advancing. If it
   is not, the follower is skipped ("It did not start the recording").
3. Re-match the follower's play state to the leader's: resume it if the
   leader is playing, pause it if the leader is paused. This also checks the
   leader is still on the same recording; a leader that has changed
   recording part-way raises `LeaderError`.
4. Measure the offset: read both players concurrently, then
   `offset = (follower_pos − leader_pos) − (follower_sample_time − leader_sample_time)`.
   Take the median of five samples 200 ms apart. Positive means the follower
   is ahead. A follower that reports no position ends the sync as skipped.
5. Re-match the play state again. If the leader paused or resumed while the
   samples were taken, they mix two play states: discard the measurement and
   go back to step 3.
6. Compare with the target offset. Within tolerance: done.
7. Otherwise correct as below, then wait 1 s and go back to step 3:
   - **Leader paused:** seek the follower by the error. Nothing is moving, so
     a seek is the only tool.
   - **Follower ahead by 45 ms to 3 s:** pause the follower for the error
     minus 45 ms, then resume. The resume is in a `finally`, run through
     `asyncio.shield`, so a sync or a session cancelled mid-hold does not
     leave a TV paused.
   - **Anything else:** seek the follower to 1 s ahead of the leader, so the
     next round can pause.

The loop is at most five corrections plus a final measurement; a discarded
measurement uses up one of those rounds. The follower is left in the leader's
play state (paused if the leader is paused). It returns the final offset, the
corrections made (`rounds`), and whether tolerance was met. If the last
measurement was discarded because the leader paused or resumed during it,
there is no final offset and the follower is skipped ("The leader kept
pausing and resuming during the sync").

A follower paused at exactly position 0 on the leader's recording is the
end-of-recording state, where a `resume` would report `playing` at a frozen
0 and the engine would correct against a player that is not playing. So it
gets a play command instead. A follower genuinely paused at the very start of
the recording looks the same and is handled the same way; a play command from
an ordinary pause works. With a paused leader this does not apply.

With a paused leader, a follower that has to be started is started playing,
seen to be advancing, and then paused by step 3. That order is fine.

**Known limit — a paused leader.** While paused, a seek lands on the
keyframe at or before the position asked for, not on it. Syncing to a paused
leader therefore currently leaves the follower paused on that keyframe, up
to about 1 s early, and reports `out_of_tolerance` (seen: five seeks landing
on the same keyframe, −317 ms). What to do about it is an open product
decision.

Several followers are synced concurrently and independently; one failing does
not affect the others.

The constants (1 s lead, 3 s pause ceiling, 45 ms pause overhead, 1 s settle)
are module-level and were measured on tvOS.

### Starting playback

Both the sync engine and `switch_to_recording` start a recording through one
helper in `playback.py`. It sends `play/recording`, then reads the app's
status every 0.5 s:

- If the app is not on the recording 3 s after the last play command, the
  command is sent again — a play from the end of a finished recording is
  dropped once.
- It returns only once playback is advancing: two reads 0.5 s apart on the
  recording whose positions differ by at least 0.25 s. A seek or a
  measurement before then would see the frozen position of a loading app,
  and the seek would be discarded.

Both are bounded by the caller's limit, 15 s in both cases; at the limit the
helper gives up.

### Config flow and discovery

- **Zeroconf** for `_channels_app._tcp.local.` (a TV) and
  `_channels_dvr._tcp.local.` (the server). Each discovery offers a confirm
  step where the name can be edited.
- **Manual entry** of host and port for either type, as a fallback.
- One config entry per TV and one per server. Only one DVR server is
  supported: a second, different one is aborted as `single_dvr_only`, after
  the unique-ID check so the same server rediscovered at a new address still
  updates its host.
- **Unique ID:** the kind and the Bonjour host name, lower-cased
  (`app_office.local`, `dvr_dvr-nas6.local`). The stored host is the IP
  address from discovery, updated when a rediscovery shows it has changed. A
  device entered by hand is keyed by the address typed in and does not
  follow an address change.
- **No duplicates:** discovery is dropped for a device already added by hand
  at the same address, and the reverse.
- **Default name:** for an app, the host name without `.local`, dashes as
  spaces ("Living Area", "Office"), because every Apple TV advertises its app
  as "Apple TV". For a server, "Channels DVR" and its Bonjour instance name.
- **Options per TV:** sync offset in milliseconds, −500 to 500, default 0.
  Positive delays that TV relative to the leader.

A TV whose app has never been in the foreground since the integration was
installed is not discovered yet. It appears the first time Channels is opened
on it.

### Media player entity

One per TV. It is the device's main entity and takes the device's name, for
example "Living Room Channels", which may be renamed to an entity ID such as
`media_player.channels_living_room`.

| Property | Source |
| --- | --- |
| State | `playing` / `paused` / `idle` (stopped, or left at the end of a recording) |
| Available | The app API answered the last poll |
| Title, series, season, episode, artwork | `now_playing` |
| `media_position`, `media_position_updated_at`, `media_duration` | `playback_time` and `duration` (recordings only) |
| `media_content_id` | Recording ID, or channel number on live TV |
| `media_content_type` | `episode`, `movie`, `video` or `channel` |
| Extra attributes | `recording_id`, `channel_number`, `channel_name` |
| Muted | `muted` |
| Source list | Favourite channels |

Supported: play, pause, stop, mute, seek to position (done as a relative
seek from the current position), next/previous track (skip to the next or
previous commercial marker), select source (tune a favourite channel), play
media (a channel number or a recording ID).

A recording reported at position 0 for 3 s or more, whether as `playing` or
`paused`, has ended and the entity is `idle`. The 3 s keeps a recording that
was just started from the beginning, which reports 0 for a second or two
while it loads, from showing as idle. The sync actions read the app
directly and do not apply this rule; a script should check the leader's
entity state first.

After every command the entity takes the app's reply as its state and reads
the status again 1 s later, because the reply can describe the state before
the command took effect.

Polling is every 5 s while reachable and every 10 s while not. An
unreachable app is an ordinary state and is not logged as an error. The sync
actions talk to the TVs directly and do not depend on this poll.

The server's config entry creates a device and no entities in phase 1.

### Actions

Both actions below return a response and must be called with one. They
report an operational failure in the response's `error` field instead of
raising, because a Home Assistant script cannot read the text of an error it
catches, and the reason (no tuner free, say) is what the person needs. They
raise only for invalid input: a target or leader that is not a Channels media
player, or a field that fails the schema.

**`channels.switch_to_recording`** — domain action. It takes Channels media
players by entity ID only, in its `entity_id` field (a script's
`target: {entity_id: ...}` is merged into that field by Home Assistant).
Being a domain action rather than an entity action, it works while an entity
is unavailable and reports why. Response, keyed by entity ID:
`{recording_id, switched, started_recording, error}`.

| Field | Default | Meaning |
| --- | --- | --- |
| `entity_id` | required | One or more Channels media players |
| `behind_live` | 5 | Seconds behind the live edge to land. Minimum 3 |

Several players are switched one at a time, and overlapping calls of the
action wait for each other. Two TVs on the same channel would otherwise both
find no recording and both toggle, and the second toggle would stop the
recording the first one started. Switched in turn, the second finds the
first's recording.

For each player:

1. Read status. Stopped: fail with "nothing is playing". Already on a
   recording: return it with `switched: false, started_recording: false`.
   Neither on a recording nor on a live channel: fail.
2. On live TV: ask the DVR for an in-progress recording on that channel.
   None listed: ask whether the DVR has an active job on the channel at the
   current time (`has_active_job`). If it has, a recording is running whose
   file is not listed yet, and a toggle could stop it: send no toggle, poll
   for the file every 0.5 s for up to 10 s, and use it when it appears. If
   it never appears, fail with "The DVR is recording this channel but the
   recording has not appeared" and `started_recording: false`.
3. No file and no active job: re-read the app's channel and fail if it has
   changed, since
   `toggle_record` on another channel could stop a recording there. Note the
   latest failed job on the channel, then call `toggle_record` and poll the
   DVR every 0.5 s for up to 10 s for the new file. A failed job newer than
   the one noted (judged on the DVR's own clock) ends the wait early with
   that job's error text. If neither a file nor a new failed job appears in
   10 s, fail with "A recording was requested but has not appeared; it may
   still be starting" and `started_recording: true`.
4. If the recording is younger than `behind_live`, wait on live TV until it
   is `behind_live` seconds old, so the TV can land that far back.
5. Start the recording on the TV (see "Starting playback") and wait up to
   15 s for playback to be advancing; otherwise fail.
6. Seek to `(now − created_at) − behind_live`. Wait 1 s and read the
   position; if it is more than 2 s from where it should be by then (the
   wanted position plus the time elapsed), send the seek again, at most
   twice.
7. Check playback is advancing: two samples 1 s apart should differ by at
   least 0.5 s. If not, seek back 2 s and check again, up to three times,
   then fail.
8. Return `{recording_id, switched: true, started_recording: <bool>}`.

The viewer sees a short interruption and lands a few seconds behind where
they were. Because live TV reports no position, a viewer who had paused live
TV still lands `behind_live` seconds behind the live edge.

"Fail" in these steps means returning with `error` set, `recording_id`
empty and `switched: false`. A failure after `toggle_record` was sent has
`started_recording: true`: a recording was started and is running, and
calling the action again blindly could toggle it off.

The DVR is needed only from step 2. With no DVR server set up, a player
already on a recording is still returned as in step 1, an unreachable one
reports that, and any other gets "No Channels DVR server is set up". A
player whose Channels config entry is not loaded gets "Its Channels entry is
not loaded".

**`channels.sync_playback`** — domain action. Response:
`{error, followers: {entity_id: result}}`.

| Field | Default | Meaning |
| --- | --- | --- |
| `leader` | required | A Channels media player entity |
| `followers` | required | A list of Channels media player entities |
| `follower_timeout` | 30 | Seconds to wait for each follower's API to answer |
| `tolerance_ms` | 50 | Acceptable final offset. Minimum 50 |

`leader` and `followers` are data fields, not a `target`, on purpose. Entity
actions skip unavailable entities, and a follower is unavailable until its
app is launched. The action resolves each entity to its host through the
registry and polls the API directly every 0.5 s for up to `follower_timeout`.

The response lists every follower with one of these statuses:

- `synced` — with `offset_ms` and `rounds`
- `out_of_tolerance` — synced as closely as five rounds allowed, with
  `offset_ms` and `rounds`
- `skipped` — with a `reason`: the API never answered, the follower never
  started the recording, it is the leader, the leader stopped part-way, the
  leader changed recording, the leader kept pausing and resuming, the
  follower left the recording, the follower stopped reporting a position, a connection error, the follower's
  Channels config entry is not loaded, or an unexpected error (logged)
- `not_set_up` — there is no Channels media player by that ID. Kept apart
  from `skipped` so a caller can list a room ahead of its TV existing and
  ignore this one status

`offset_ms` is the measured offset of the follower relative to the leader;
negative means behind. A TV with a +80 ms sync offset is aimed 80 ms behind,
so it reports about −80 when synced.

Followers are synced concurrently; one failing does not affect the others.
When the leader is unreachable, stopped or on live TV, or its config entry
is not loaded, `error` says so and `followers` is empty.

**`channels.seek_by`, `channels.seek_forward`, `channels.seek_backward`** —
entity actions kept from the built-in integration so this one is a drop-in
replacement. `seek_by` accepts fractional seconds.

**`channels.start_follow`** and **`channels.stop_follow`** — domain actions
with no response. `start_follow` takes `leader`, `followers`, `tolerance_ms`
(default 250, 100 to 900), `live_settle` (default 10 s) and `behind_live`
(default 5 s), builds a `FollowSession` and runs it as a background task of
the leader's config entry. A session for a leader that already has one
replaces it. `stop_follow` takes `leader` and cancels its session.

Followers with no entity registry entry, and the leader itself, are left
out. A follower already in another leader's session, a follower that is
leading a session of its own, and a leader that is itself following, raise
`ServiceValidationError`: two sessions pulling one TV two ways would never
settle. If no follower is left after the leaving out, no session is started,
the leader's existing session, if it has one, is ended, and nothing is
raised.

A done-callback on the session's task removes it from the registry if it ends
by itself, logs its exception, and refreshes the attributes. It removes only
its own entry, so a replaced session's late end cannot remove its successor.

A follower's app is reached through its config entry each time, not through
a client captured at start. While the entry is not loaded the follower is
treated as not answering, and after a reload the new client is used.

The session's problem callback fires the event `channels_follow_problem`
(`leader`, `follower` or null, `message`). Its status callback sends a
dispatcher signal; every Channels media player listens and rewrites its
state, which is how `following`, `follow_status` and `followed_by` stay
current between polls.

### The follow session

`lib/follow.py`. One task reads the leader every `TICK` (1 s) and publishes
the reading. Each follower has its own task, woken by each new reading, so a
follower in the middle of a several-second fine-tune does not delay the
others.

The leader's reading is published as "nothing to follow" when the app is
unreachable, stopped, on live TV, or at position 0. Position 0 is where a
recording that played to its end sits, reported as playing or paused;
following it would drag every follower to the start. A recording just
started from the beginning reports 0 too, for a second or two while it
loads, and is followed as soon as it moves.

On live TV the session looks at the DVR once when the channel is first seen.
If the channel has a recording in progress or an active job, it calls
`switch_to_recording` at once, under the lock the switch action uses. If
not, it waits until the leader has been on that channel for `live_settle`
and then calls it. A failure is reported once and not retried until the
leader changes channel or leaves live TV, since a retry could send the record
toggle again. Any exception while moving the leader, not only a
`ChannelsError`, ends the attempts for that channel; one that was not
expected is reported with the `UNEXPECTED` message. A moment in which the
leader cannot be read does not count as leaving live TV.

With no DVR client the session cannot move the leader. It reports that once
per visit to live TV, and only after the leader has stayed on one channel for
`live_settle`.

Each follower pass makes at most one correction, the first of these that
applies:

1. Not answering: `absent`, nothing sent.
2. Not on the leader's recording, or sitting at position 0 while the leader
   is past 1 s: `start_playback`.
3. Play state differs: pause or resume.
4. Leader paused and the follower more than `PAUSED_SLACK` (1.5 s) out: a
   plain seek. It is counted toward resting exactly like the playing jump in
   rule 5: a seek that is needed again on the next pass, with the leader not
   having moved, is a failed correction.
5. More than `JUMP` (1 s) out: a plain seek by the difference.
6. A coarse correction (2, a resume, or 5) was made last time, or the
   follower has been outside the tolerance for `CONFIRM` (2) passes running:
   `sync_follower` with a 50 ms tolerance.

Where it should be is the leader's position, carried forward to the moment
the follower was read, plus the follower's target offset.

A fine-tune is given a guarded view of the leader that raises `LeaderError`
when the leader's reading is not followable (stopped, live TV, position 0), so
a leader that reaches the end of its recording mid-fine-tune cannot drag the
follower to the start. A fine-tune takes its own readings of the leader; if
the leader seeks mid-fine-tune the fine-tune follows it. It is abandoned only
when the leader stops being followable or changes recording.

Rule 6 fine-tunes after every coarse correction because a plain seek lands
within a second, not within the tolerance; a fine-tune that finds the
follower already close sends nothing. The two-pass rule exists because the
fine-tune corrects to 50 ms: one stray reading must not cause a visible hop
on a follower that is inside the tolerance.

A failed correction is counted: a recording that will not start, a command
that errors, a fine-tune that does not end synced, a plain seek that is
still needed on the next pass. A success clears the count. After
`REST_AFTER` (5) in a row the follower is left alone for `REST_FOR` (30 s)
and a problem is reported. Failures are not counted when the leader has
moved since, which the leader task tracks as an epoch that goes up on every
change of recording, play state, or position beyond what playing accounts
for. Someone pressing skip ten times is not a follower failing to keep up.

An unexpected exception in a follower's task rests the follower and is
reported with the `UNEXPECTED` message, not the "could not be kept in step"
one. The session's callbacks are called inside a guard: a callback that
raises is logged and does not end the session.

### Errors

The two sync actions above put operational failures in their response. Both
raise `ServiceValidationError` for a target or leader that is not a Channels
media player, and the schema rejects a field that is missing or out of range.
The ordinary media player commands and the three seek actions raise
`HomeAssistantError` when the app does not answer; the seek actions, being
entity actions, are skipped by Home Assistant for an unavailable entity
rather than raising. Every message is a sentence a person can read in a
notification.

### Testing

- **Sync engine:** unit tests against a simulated player with a clock, which
  models lossy seeks, pause overhead, the live-edge limit, the play dropped
  at the end of a finished recording, the 1.5 s load after a play during
  which seeks are discarded, a follower that never starts, and a leader that
  stops mid-sync. The simulated DVR lists an active job while a recording it
  started runs, and scheduled jobs that name their channel only in
  `channels`.
- **Clients:** mocked HTTP, using response bodies captured from the real
  devices on 2026-10-01.
- **Follow session:** scenario tests against the same simulated players,
  several at once, on a fake clock that parks each sleeping task and jumps
  to the next wake time. The simulated player has remote-control actions
  that are not logged as engine commands, so a test can say "someone
  rewound the Office" and then assert what the session sent.
- **HA layer:** `pytest-homeassistant-custom-component` for the config flow
  (zeroconf, manual, duplicate, IP change), entity state mapping, and action
  schemas and responses.
- **CI:** hassfest, the HACS validation action, ruff, pytest.
- **Bench script:** a small CLI in the repo that runs the engine against real
  hosts and prints offsets. Changes to the engine are tried here first,
  because every code change in the installed integration needs an HA
  restart.
