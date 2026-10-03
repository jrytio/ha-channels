"""Run the clients and the sync engine against real devices, from a laptop.

Engine changes are tried here first: every code change in the installed
integration needs a Home Assistant restart, and this needs none.

    uv run python -m scripts.bench status 10.0.0.1 10.0.0.2
    uv run python -m scripts.bench sync 10.0.0.1 10.0.0.2 --monitor 20
    uv run python -m scripts.bench switch 10.0.0.1 --dvr 10.0.0.9
    uv run python -m scripts.bench follow 10.0.0.1 10.0.0.2 --dvr 10.0.0.9
    uv run python -m scripts.bench follow 10.0.0.1 10.0.0.2 --watch-only
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
from dataclasses import asdict
import json

import aiohttp

from custom_components.channels.lib import (
    AppClient,
    ChannelsError,
    DvrClient,
    Follower,
    FollowSession,
    SystemClock,
    switch_to_recording,
    sync_follower,
)
from custom_components.channels.lib.sync import measure_offset


async def cmd_status(args: argparse.Namespace, session: aiohttp.ClientSession) -> None:
    for host in args.hosts:
        try:
            status = await AppClient(host, session).status()
        except ChannelsError as err:
            print(f"{host}: {err}")
            continue
        print(f"{host}: {json.dumps(asdict(status), indent=1)}")


async def cmd_sync(args: argparse.Namespace, session: aiohttp.ClientSession) -> None:
    clock = SystemClock()
    leader = AppClient(args.leader, session)
    followers = {host: AppClient(host, session) for host in args.followers}
    results = await asyncio.gather(
        *(
            sync_follower(
                leader,
                follower,
                clock=clock,
                tolerance=args.tolerance_ms / 1000,
                target=-args.offset_ms / 1000,
            )
            for follower in followers.values()
        ),
        return_exceptions=True,
    )
    for host, result in zip(followers, results, strict=True):
        shown = result if isinstance(result, Exception) else result.as_dict()
        print(f"{host}: {shown}")

    started = clock.time()
    while clock.time() - started < args.monitor:
        for host, follower in followers.items():
            offset = await measure_offset(leader, follower, clock)
            shown = "no position" if offset is None else f"{offset * 1000:+7.1f} ms"
            print(f"  t+{clock.time() - started:5.1f}s {host}: {shown}")
        await clock.sleep(max(1.0, args.monitor / 10))


async def cmd_switch(args: argparse.Namespace, session: aiohttp.ClientSession) -> None:
    result = await switch_to_recording(
        AppClient(args.host, session),
        DvrClient(args.dvr, session),
        clock=SystemClock(),
        behind_live=args.behind_live,
    )
    print(asdict(result))


async def _paired_offsets(leader: AppClient, followers: dict[str, AppClient]) -> str:
    """Read every app once and describe each follower against the leader."""
    readings = await asyncio.gather(
        leader.status(),
        *(follower.status() for follower in followers.values()),
        return_exceptions=True,
    )
    lead, rest = readings[0], readings[1:]
    if isinstance(lead, BaseException):
        return f"leader: {lead}"
    where = "live" if lead.position is None else f"{lead.position:8.2f}"
    parts = [f"leader {lead.state} {where}"]
    for host, status in zip(followers, rest, strict=True):
        if isinstance(status, BaseException):
            parts.append(f"{host} absent")
        elif status.position is None or lead.position is None:
            parts.append(f"{host} {status.state}")
        else:
            offset = status.position - lead.position
            if lead.state == "playing":
                offset -= status.sampled_at - lead.sampled_at
            parts.append(f"{host} {status.state} {offset * 1000:+8.1f} ms")
    return " | ".join(parts)


async def cmd_follow(args: argparse.Namespace, session: aiohttp.ClientSession) -> None:
    clock = SystemClock()
    leader = AppClient(args.leader, session)
    hosts = [host for host in args.followers if host != args.leader]
    if len(hosts) < len(args.followers):
        # A TV cannot follow itself; the integration leaves it out too.
        print(f"{args.leader} is the leader, so it is not also a follower")
    followers = {host: AppClient(host, session) for host in hosts}
    task: asyncio.Task[None] | None = None
    if not args.watch_only:
        follow = FollowSession(
            leader,
            {
                host: Follower(client, target=-args.offset_ms / 1000)
                for host, client in followers.items()
            },
            dvr=DvrClient(args.dvr, session) if args.dvr else None,
            clock=clock,
            tolerance=args.tolerance_ms / 1000,
            live_settle=args.live_settle,
            behind_live=args.behind_live,
            on_problem=lambda who, message: print(
                f"  PROBLEM {who or 'leader'}: {message}"
            ),
            on_status=lambda who, status: print(f"  {who} -> {status}"),
        )
        task = asyncio.create_task(follow.run())
    started = clock.time()
    try:
        while clock.time() - started < args.seconds:
            await clock.sleep(1.0)
            line = await _paired_offsets(leader, followers)
            print(f"t+{clock.time() - started:6.1f}s {line}")
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status", help="print what each app reports")
    status.add_argument("hosts", nargs="+")
    status.set_defaults(run=cmd_status)

    sync = commands.add_parser("sync", help="sync followers to a leader")
    sync.add_argument("leader")
    sync.add_argument("followers", nargs="+")
    sync.add_argument("--tolerance-ms", type=float, default=50)
    sync.add_argument("--offset-ms", type=float, default=0)
    sync.add_argument("--monitor", type=float, default=0, help="seconds to watch drift")
    sync.set_defaults(run=cmd_sync)

    switch = commands.add_parser(
        "switch", help="move an app from live TV to a recording"
    )
    switch.add_argument("host")
    switch.add_argument("--dvr", required=True)
    switch.add_argument("--behind-live", type=float, default=5)
    switch.set_defaults(run=cmd_switch)

    follow = commands.add_parser(
        "follow", help="keep followers on a leader until the time is up"
    )
    follow.add_argument("leader")
    follow.add_argument("followers", nargs="+")
    follow.add_argument("--dvr", help="needed to follow the leader onto live TV")
    follow.add_argument("--seconds", type=float, default=120)
    follow.add_argument("--tolerance-ms", type=float, default=250)
    follow.add_argument("--offset-ms", type=float, default=0)
    follow.add_argument("--live-settle", type=float, default=10)
    follow.add_argument("--behind-live", type=float, default=5)
    follow.add_argument(
        "--watch-only",
        action="store_true",
        help="print each follower's offset once a second and correct nothing",
    )
    follow.set_defaults(run=cmd_follow)

    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    async with aiohttp.ClientSession() as session:
        try:
            await args.run(args, session)
        except ChannelsError as err:
            raise SystemExit(f"error: {err}") from err


if __name__ == "__main__":
    asyncio.run(main())
