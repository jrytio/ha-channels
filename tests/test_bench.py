"""Tests for the bench script's argument handling; nothing here touches a network."""

import argparse

from scripts import bench


async def test_follow_leaves_the_leader_out_of_its_followers(monkeypatch, capsys):
    made: list[str] = []

    def app_client(host, session):
        made.append(host)
        return object()

    monkeypatch.setattr(bench, "AppClient", app_client)
    args = argparse.Namespace(
        leader="10.0.0.1",
        followers=["10.0.0.2", "10.0.0.1"],
        watch_only=True,
        seconds=0,
    )

    await bench.cmd_follow(args, session=None)

    assert made == ["10.0.0.1", "10.0.0.2"]
    assert "10.0.0.1 is the leader, so it is not also a follower" in (
        capsys.readouterr().out
    )
