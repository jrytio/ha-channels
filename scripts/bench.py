"""Run the clients and the sync engine against real devices, from a laptop.

Engine changes are tried here first: every code change in the installed
integration needs a Home Assistant restart, and this needs none.

    uv run python -m scripts.bench status 10.0.0.1 10.0.0.2
    uv run python -m scripts.bench sync 10.0.0.1 10.0.0.2 --monitor 20
    uv run python -m scripts.bench switch 10.0.0.1 --dvr 10.0.0.9
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json

import aiohttp

from custom_components.channels.lib import (
    AppClient,
    ChannelsError,
    DvrClient,
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
