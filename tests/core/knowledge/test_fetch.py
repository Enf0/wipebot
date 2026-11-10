import asyncio
import json

import pytest

from wipebot.core import cache
from wipebot.core.knowledge import fetch
from wipebot.core.knowledge.fetch import KnowledgeError


def fake_process(envelope: dict, running: list[int] | None = None):
    """A warcraft_cli process answering `envelope`; with `running`, it notes how many run at once."""
    async def create_subprocess_exec(*args, **kwargs):
        class Process:
            returncode = 0

            async def communicate(self):
                if running is not None:
                    running.append(running[-1] + 1)
                    await asyncio.sleep(0.01)
                    running.append(running[-1] - 1)
                return json.dumps(envelope).encode(), b""
        return Process()
    return create_subprocess_exec


async def test_cli_errors_become_knowledge_errors(monkeypatch):
    envelope = {"ok": False, "error": {"code": "not_found", "message": "Report not found."}}
    monkeypatch.setattr(fetch.asyncio, "create_subprocess_exec", fake_process(envelope))
    with pytest.raises(KnowledgeError, match="not_found: Report not found"):
        await fetch.cli("lorrgs", "spec-spells", "x")


async def test_a_thing_the_source_lacks_is_not_found(monkeypatch):
    envelope = {"ok": False, "error": {"code": "not_found", "message": "Wowhead returned HTTP 404"}}
    monkeypatch.setattr(fetch.asyncio, "create_subprocess_exec", fake_process(envelope))
    with pytest.raises(fetch.NotFound):
        await fetch.cli("wowhead", "entity", "spell", "999999999")


async def test_a_not_found_is_remembered_as_a_found_answer_is(monkeypatch):
    """Lorrgs answers "Invalid Boss" for a boss it doesn't track, each time anyone asks: asked once, like a page
    that was found. Another failure is no answer and is asked again."""
    ran = []
    envelope = {"ok": False, "error": {"code": "not_found", "message": "Invalid Boss."}}

    async def create_subprocess_exec(*args, **kwargs):
        ran.append(args[3:])
        return await fake_process(envelope)()
    monkeypatch.setattr(fetch.asyncio, "create_subprocess_exec", create_subprocess_exec)
    monkeypatch.setattr(cache, "_cache", cache.Cache(":memory:"))
    for _ in range(2):
        with pytest.raises(fetch.NotFound, match="lorrgs boss-spells: not_found: Invalid Boss."):
            await fetch.cli("lorrgs", "boss-spells", "rotmire")
    assert len(ran) == 1
    envelope["error"] = {"code": "upstream_error", "message": "HTTP 503"}
    for _ in range(2):
        with pytest.raises(KnowledgeError, match="503"):
            await fetch.cli("lorrgs", "boss-spells", "sszorak")
    assert len(ran) == 3


async def test_a_tooltip_gives_up_sooner_than_a_page(monkeypatch):
    """A boss has dozens of tooltips, read a few at a time: one that hangs mustn't hold the others' answer long."""
    waited = []

    async def wait_for(reading, timeout):
        reading.close()
        waited.append(timeout)
        raise TimeoutError
    monkeypatch.setattr(fetch.asyncio, "create_subprocess_exec", fake_process({"ok": True, "data": {}}))
    monkeypatch.setattr(fetch.asyncio, "wait_for", wait_for)
    with pytest.raises(KnowledgeError, match=r"wowhead entity gave no answer in 20 s"):
        await fetch.cli("wowhead", "entity", "spell", "1")
    with pytest.raises(KnowledgeError, match=r"method guide-full gave no answer in 60 s"):
        await fetch.cli("method", "guide-full", "https://www.method.gg/guides/x")
    assert waited == [20, 60]


async def test_a_crashed_command_is_logged_and_told_without_its_output(monkeypatch, caplog):
    async def create_subprocess_exec(*args, **kwargs):
        class Process:
            returncode = 1

            async def communicate(self):
                return b"", b'Traceback (most recent call last):\n  File "/srv/app/x.py"\nKeyError: token'
        return Process()
    monkeypatch.setattr(fetch.asyncio, "create_subprocess_exec", create_subprocess_exec)
    with pytest.raises(KnowledgeError) as told:
        await fetch.cli("wowhead", "entity", "spell", "1")
    assert str(told.value) == "wowhead entity failed with no readable answer"
    assert "KeyError: token" in caplog.text


async def test_commands_run_a_few_at_a_time_whoever_asks(monkeypatch):
    running = [0]
    monkeypatch.setattr(fetch.asyncio, "create_subprocess_exec", fake_process({"ok": True, "data": {}}, running))
    await asyncio.gather(*(fetch.cli("wowhead", "entity", "spell", str(i)) for i in range(20)))
    assert max(running) == fetch.CLI_CONCURRENCY


async def test_a_hung_command_gives_up(monkeypatch):
    killed = []

    async def create_subprocess_exec(*args, **kwargs):
        class Process:
            returncode = None

            async def communicate(self):
                await asyncio.sleep(10)

            def kill(self):
                killed.append(True)

            async def wait(self):
                return -9
        return Process()

    monkeypatch.setattr(fetch.asyncio, "create_subprocess_exec", create_subprocess_exec)
    monkeypatch.setattr(fetch, "TIMEOUT_S", {})
    monkeypatch.setattr(fetch, "CLI_TIMEOUT_S", 0.01)
    with pytest.raises(KnowledgeError, match="gave no answer"):
        await fetch.cli("wowhead", "entity", "spell", "1")
    assert killed
    killed.clear()
    monkeypatch.setattr(fetch, "CLI_TIMEOUT_S", 60)
    asked = asyncio.ensure_future(fetch.cli("wowhead", "entity", "spell", "2"))
    await asyncio.sleep(0.01)
    asked.cancel()  # the answer it was for gave up: the command doesn't run on
    with pytest.raises(asyncio.CancelledError):
        await asked
    assert killed


async def test_what_a_model_wrote_is_never_an_option_and_keeps_its_cache_key(monkeypatch):
    ran = []

    async def create_subprocess_exec(*args, **kwargs):
        ran.append(args[3:])
        return await fake_process({"ok": True, "data": {"x": 1}})()
    monkeypatch.setattr(fetch.asyncio, "create_subprocess_exec", create_subprocess_exec)
    keys = []

    async def cached(key, max_age_s, get, cancel=False):
        keys.append(key)
        return await get()
    monkeypatch.setattr(fetch.cache(), "fetch", cached)
    await fetch.cli("warcraft", "search", "--brief", "--limit", "16", "--", "--help")
    assert ran == [("search", "--brief", "--limit", "16", "--", "--help")]
    assert keys == [fetch.cache_key("cli", "warcraft", "search", "--help", "--brief", "--limit", "16")]  # as before
    assert fetch._key_args(("guides", "raids", "--", "Sszorak")) == ("guides", "raids", "Sszorak")


async def test_the_tests_run_no_warcraft_cli_command():
    """The root conftest's `offline`: a subprocess would reach the sites past the socket guard."""
    with pytest.raises(ConnectionRefusedError, match="tests run offline"):
        await fetch.cli("lorrgs", "specs")
