import json
import stat

import pytest

from ship import t3
from ship.errors import Refusal
from ship.shell import CommandFailed


def _fake_cli(tmp_path, monkeypatch, *, active_run: str | None, exit_code: int = 0):
    shown = {"thread": {"threadId": "t1", "status": "running", "activeRunId": active_run}, "messages": []}
    cli = tmp_path / "t3"
    cli.write_text(
        f"#!/bin/sh\necho \"$@\" > {tmp_path}/args\ncat <<'EOF'\n{json.dumps(shown)}\nEOF\nexit {exit_code}\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv(t3.CLI_ENV, str(cli))


def test_a_thread_with_an_active_run_is_running(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, active_run="run:1")
    assert t3.thread_running("t1")
    assert (tmp_path / "args").read_text().split() == ["thread", "show", "--json", "--messages", "0", "t1"]


def test_a_thread_whose_run_ended_is_not(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, active_run=None)
    assert not t3.thread_running("t1")


def test_a_thread_t3_doesnt_know_fails(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, active_run=None, exit_code=1)
    with pytest.raises(CommandFailed):
        t3.thread_running("t1")


def test_outside_t3_it_refuses_rather_than_guess(monkeypatch):
    monkeypatch.delenv(t3.CLI_ENV, raising=False)
    with pytest.raises(Refusal, match="isn't a T3 Code session"):
        t3.thread_running("t1")
