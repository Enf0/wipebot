"""The command line (`python -m wipebot`, src/wipebot/__main__.py): it starts, and `call` runs a tool, here on the
recorded answers (support/recorded.py)."""
import subprocess
import sys

import pytest

from tests.support.recorded import TOOLS, Replay, serve_from
from wipebot import __main__ as cli
from wipebot.core import server


def test_the_command_line_starts_and_lists_its_commands():
    shown = subprocess.run([sys.executable, "-m", "wipebot", "--help"], capture_output=True, text=True, timeout=60)
    assert shown.returncode == 0, shown.stderr
    assert all(c in shown.stdout for c in ("mcp", "serve", "call", "bot", "ask"))


@pytest.fixture
def recorded(monkeypatch):
    serve_from(monkeypatch, Replay())
    monkeypatch.setattr(server, "configure", lambda *a: server.DEFAULT.library)  # keep the recorded answers'


def test_call_prints_a_tools_output(recorded, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["wipebot", "call", "pull_summary", '{"report": "KBYT4xXn7VNjDgh8", "pull": 13}'])
    cli.main()
    assert capsys.readouterr().out == (TOOLS / "pull-summary.txt").read_text(encoding="utf-8")  # the case's output


def test_call_with_arguments_the_tool_refuses_exits_with_why(recorded, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["wipebot", "call", "pull_summary", '{"report": "KBYT4xXn7VNjDgh8", "pull": 99}'])
    with pytest.raises(SystemExit, match="99"):
        cli.main()
