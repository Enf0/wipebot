"""Every test here runs offline (`offline`, on for all of them). The fixtures every layer shares are here; each
layer's own are in its conftest.py (core/, discord/), the helpers and the data in support/. README.md says how the
suite works."""
import asyncio
import os
import socket
import time

import pytest

pytest.register_assert_rewrite("tests.support")  # its asserts (assert_snapshot) show a diff too

from tests.support import CODE, FakeWCL, Night  # noqa: E402

os.environ["WIPEBOT_CACHE"] = "off"  # the fakes' answers must not land in the real cache on disk
os.environ["WARCRAFTLOGS_CACHE_BACKEND"] = "none"  # nor come from warcraft_cli's own, which another machine lacks
LOCAL = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture
def offline(monkeypatch):
    """No connection to anywhere but this machine, and no warcraft_cli command: Warcraft Logs and the guides come
    from the fakes or the recorded answers (support/recorded.py). A test that tries fails at once, instead of slowing
    the suite or passing only with the developer's Warcraft Logs login."""
    connect = socket.socket.connect

    def local_only(self, address):
        if isinstance(address, tuple) and address[0] not in LOCAL:
            raise ConnectionRefusedError(f"a test tried to reach {address[0]}: tests run offline")
        return connect(self, address)
    monkeypatch.setattr(socket.socket, "connect", local_only)

    spawn = asyncio.create_subprocess_exec

    async def no_cli(*args, **kwargs):  # warcraft_cli runs in a subprocess (knowledge/fetch.py), out of reach above
        if any("_cli.main import run" in str(a) for a in args):
            raise ConnectionRefusedError(f"a test tried to run warcraft_cli {' '.join(map(str, args[3:]))[:80]}: "
                                         "tests run offline")
        return await spawn(*args, **kwargs)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_cli)


@pytest.fixture(scope="session")
def night() -> Night:
    """The captured night (support/night.py)."""
    return Night(CODE)


@pytest.fixture
def live_wcl(night) -> FakeWCL:
    """The captured night playing live, the last pull having just ended."""
    return FakeWCL(night, time.time() * 1000)


@pytest.fixture(autouse=True)
def _offline(offline):
    """Every test runs offline."""
