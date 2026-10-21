"""The core's fixtures (README.md says how the suite works). Tests can't import from a conftest, so the helpers are
fixtures too: `patch_wcl(rep, ...)` and `fake_knowledge(...)` give Warcraft Logs and the guides the answers a test
needs."""
import inspect
from collections.abc import Callable
from typing import Any

import pytest

from tests.support import CODE, RESTO_SPELLS, FakeWCL
from wipebot.core import knowledge
from wipebot.core.report import Report


def _answering(answer: Any) -> Callable[..., Any]:
    """An async function: `answer` itself if it is one, else one that returns `answer`'s result (a function) or
    `answer` (anything else), whatever it's asked."""
    if inspect.iscoroutinefunction(answer):
        return answer

    async def answers(*args, **kwargs):
        return answer(*args, **kwargs) if callable(answer) else answer
    return answers


@pytest.fixture
def patch_wcl(monkeypatch) -> Callable[..., None]:
    """patch_wcl(rep, name=answer, ...): the report's Warcraft Logs client answers `name` with `answer` (an async
    function, a function, or the value itself), methods FakeWCL lacks included."""
    def patch(rep: Report, **answers: Any) -> None:
        for name, answer in answers.items():
            monkeypatch.setattr(rep.wcl, name, _answering(answer), raising=False)
    return patch


@pytest.fixture
def fake_knowledge(monkeypatch) -> Callable[..., None]:
    """fake_knowledge(name=answer, ...): `wipebot.core.knowledge.<name>` answers with `answer`, as patch_wcl."""
    def patch(**answers: Any) -> None:
        for name, answer in answers.items():
            monkeypatch.setattr(knowledge, name, _answering(answer))
    return patch


@pytest.fixture
async def rep(night, fake_knowledge) -> Report:
    """The captured night as a loaded Report (FakeWCL: everyone a Restoration Shaman, Lorrgs listing RESTO_SPELLS and
    no boss casts)."""
    fake_knowledge(spec_spells=RESTO_SPELLS, lust_debuffs=[57723], boss_spells=[])
    r = Report(FakeWCL(night).client(), CODE)
    await r.load()
    return r
