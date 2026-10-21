"""Conversations: one per asker and place, follow-ups, what was said in between."""

import asyncio

import pytest

from wipebot.discord import chat
from wipebot.discord.chat import Conversations, split_message
from wipebot.discord.host import Context


class FakeSession:
    opened: list[FakeSession] = []

    worn_out = False

    def __init__(self, code):
        self.code, self.questions, self.closed = code, [], False
        FakeSession.opened.append(self)

    async def ask(self, question, ctx):
        self.questions.append(question)
        self.ctxs = [*getattr(self, "ctxs", []), ctx]
        return f"{self.code}: {question}"

    async def __aexit__(self, *exc):
        self.closed = True


@pytest.fixture
def convs():
    FakeSession.opened = []

    async def open_session(code):
        return FakeSession(code)
    return Conversations(open_session)


async def test_follow_ups_reuse_the_conversation(convs):
    assert await convs.ask(("dm", 1), "R1", "why did we wipe?", None) == "R1: why did we wipe?"
    await convs.ask(("dm", 1), "R1", "and the pull before?", None)
    await convs.ask(("dm", 2), "R1", "how did I do?", None)
    assert [s.questions for s in FakeSession.opened] == [["why did we wipe?", "and the pull before?"],
                                                         ["how did I do?"]]


async def test_a_new_report_starts_a_new_conversation(convs):
    await convs.ask(("dm", 1), "R1", "q1", None)
    await convs.ask(("dm", 1), "R2", "q2", None)
    first, second = FakeSession.opened
    assert first.closed and not second.closed and second.code == "R2"


async def test_a_new_report_lets_the_conversation_still_answering_finish(convs):
    go_on = asyncio.Event()

    class Slow(FakeSession):
        async def ask(self, question, ctx):
            if self.code == "R1":
                await go_on.wait()
            return await super().ask(question, ctx)
    convs._open = lambda code: _ready(Slow(code))
    first = asyncio.create_task(convs.ask("k", "R1", "q1", None))
    await asyncio.sleep(0.01)
    assert await convs.ask("k", "R2", "q2", None) == "R2: q2"  # the live report came up meanwhile
    old, new = FakeSession.opened
    assert not old.closed
    go_on.set()
    assert await first == "R1: q1"
    assert old.closed and not new.closed


async def test_a_worn_out_conversation_is_closed_and_the_next_question_starts_over(convs):
    await convs.ask(("dm", 1), "R1", "q1", None, message_id=10)
    FakeSession.opened[0].worn_out = True  # as its answer would have made it

    async def earlier(after, seen):
        return f"after {after}, seen {sorted(seen)}"
    await convs.ask(("dm", 1), "R1", "q2", Context(), earlier, message_id=11)
    first, second = FakeSession.opened
    assert first.closed and first.questions == ["q1"] and second.questions == ["q2"]
    assert second.ctxs[0].earlier == "after None, seen []" and not second.ctxs[0].follow_up


async def test_a_question_queued_behind_a_worn_out_one_gets_a_new_session(convs):
    import asyncio

    class Slow(FakeSession):
        async def ask(self, question, ctx):
            await asyncio.sleep(0.01)
            self.worn_out = True
            return await super().ask(question, ctx)
    convs._open = lambda code: _ready(Slow(code))
    first, second = await asyncio.gather(convs.ask("k", "R1", "q1", None), convs.ask("k", "R1", "q2", None))
    assert [s.questions for s in FakeSession.opened] == [["q1"], ["q2"]]
    assert FakeSession.opened[0].closed  # q2 ran on its own session, closed too once it wore out


async def _ready(session):
    return session


@pytest.mark.parametrize("failure", ["timeout", "error"])
async def test_a_question_queued_behind_a_failed_one_gets_a_new_session(convs, failure):
    """A post after a wipe that times out, or a session that breaks, is closed under the question waiting behind it:
    that one is answered on a session of its own."""
    class Stuck(FakeSession):
        async def ask(self, question, ctx):
            if len(FakeSession.opened) == 1:  # the first session hangs or breaks
                if failure == "error":
                    raise RuntimeError("Claude Code is gone")
                await asyncio.sleep(1)
            return await super().ask(question, ctx)
    convs._open = lambda code: _ready(Stuck(code))
    post = asyncio.create_task(convs.ask("k", "R1", "post", None, timeout=0.01))
    await asyncio.sleep(0)
    chat_q = asyncio.create_task(convs.ask("k", "R1", "why?", None, timeout=1))
    with pytest.raises(TimeoutError if failure == "timeout" else RuntimeError):
        await post
    assert await chat_q == "R1: why?"
    first, second = FakeSession.opened
    assert first.closed and not second.closed and second.questions == ["why?"]


async def test_after_an_error_that_leaves_the_session_working_the_conversation_goes_on():
    FakeSession.opened = []

    class Refused(FakeSession):
        async def ask(self, question, ctx):
            if not self.questions:
                self.questions.append(question)
                raise LookupError("rate_limit")
            return await super().ask(question, ctx)

    async def open_session(code):
        return Refused(code)
    convs = Conversations(open_session, survives=(LookupError,))
    with pytest.raises(LookupError):
        await convs.ask("k", "R1", "q1", None)
    assert await convs.ask("k", "R1", "q2", None) == "R1: q2"
    assert len(FakeSession.opened) == 1


async def test_a_session_that_wont_open_is_given_up_on(convs, monkeypatch):
    monkeypatch.setattr(chat, "OPEN_TIMEOUT_S", 0.01)

    async def hangs(code):
        await asyncio.sleep(1)
    convs._open = hangs
    with pytest.raises(chat.SlowStart, match="opening a conversation took over"):
        await convs.ask("k", "R1", "q", None)


async def test_idle_and_excess_conversations_are_closed(convs, monkeypatch):
    await convs.ask("old", "R1", "q", None)
    convs._convs["old"].last_used -= chat.IDLE_S + 1
    await convs.ask("new", "R1", "q", None)
    assert FakeSession.opened[0].closed
    monkeypatch.setattr(chat, "MAX_SESSIONS", 2)
    await convs.ask("a", "R1", "q", None)
    await convs.ask("b", "R1", "q", None)
    assert sum(not s.closed for s in FakeSession.opened) == 2


async def test_the_cap_never_closes_a_conversation_still_answering(monkeypatch):
    """After a wipe every follower's post is asked at once: past the cap, the one still answering must finish."""
    monkeypatch.setattr(chat, "MAX_SESSIONS", 2)
    FakeSession.opened = []
    answering, go_on = asyncio.Event(), asyncio.Event()

    class Slow(FakeSession):
        async def ask(self, question, ctx):
            answering.set()
            await go_on.wait()
            return "done"

    async def open_session(code):
        return Slow(code) if not FakeSession.opened else FakeSession(code)
    convs = Conversations(open_session)
    first = asyncio.create_task(convs.ask("a", "R1", "q", None))
    await asyncio.wait_for(answering.wait(), 1)
    await convs.ask("b", "R1", "q", None)
    await convs.ask("c", "R1", "q", None)  # over the cap: "b" is the oldest not answering
    go_on.set()
    assert await asyncio.wait_for(first, 1) == "done"
    assert [s.closed for s in FakeSession.opened] == [False, True, False]


async def test_a_session_opening_holds_up_only_its_own_conversation():
    """Opening a session loads the report (most of a minute when Warcraft Logs hangs): another asker's question
    goes ahead, and a second question for the same conversation waits for that one session."""
    FakeSession.opened = []
    go_on = asyncio.Event()

    async def open_session(code):
        if code == "SLOW":
            await go_on.wait()
        return FakeSession(code)
    convs = Conversations(open_session)
    slow = [asyncio.create_task(convs.ask("a", "SLOW", q, None)) for q in ("q1", "q2")]
    await asyncio.sleep(0)
    assert await asyncio.wait_for(convs.ask("b", "R1", "q", None), 1) == "R1: q"
    go_on.set()
    assert await asyncio.wait_for(asyncio.gather(*slow), 1) == ["SLOW: q1", "SLOW: q2"]
    assert [s.questions for s in FakeSession.opened] == [["q"], ["q1", "q2"]]


async def test_the_timeout_counts_from_the_questions_turn(convs):
    """A post after a wipe queued behind a long answer in the same conversation gets its whole time."""
    class Slow(FakeSession):
        async def ask(self, question, ctx):
            await asyncio.sleep(0.05)
            return await super().ask(question, ctx)
    convs._open = lambda code: _ready(Slow(code))
    first = asyncio.create_task(convs.ask("k", "R1", "long", None))
    await asyncio.sleep(0)
    assert await convs.ask("k", "R1", "post", None, timeout=0.08) == "R1: post"  # waited 0.05 s, answered in 0.05
    await first
    with pytest.raises(TimeoutError):
        await convs.ask("k", "R1", "post", None, timeout=0.01)


async def test_each_question_gets_what_was_said_since_its_conversations_last_one(convs):
    read = []

    async def earlier(after, seen):
        read.append((after, set(seen)))
        return f"since {after}"
    await convs.ask(("c", 1), "R1", "q1", Context(), earlier, message_id=10)
    convs.saw(("c", 1), [11, 12])  # the answer to q1, posted as two messages
    await convs.ask(("c", 1), "R1", "q2", Context(), earlier, message_id=20)
    await convs.ask(("c", 1), "R2", "q3", Context(), earlier, message_id=30)  # a new report: starts over
    # The first question reads further back; a follow-up what came after the previous question, less what the
    # session already has (its questions and answers), so other people's questions and answers in between.
    assert read == [(None, set()), (10, {10, 11, 12}), (None, set())]
    first, second = FakeSession.opened
    assert [(c.earlier, c.follow_up) for c in first.ctxs] == [("since None", False), ("since 10", True)]
    assert [(c.earlier, c.follow_up) for c in second.ctxs] == [("since None", False)]


def test_split_message_keeps_lines_and_the_limit():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(100))
    chunks = split_message(text, limit=500)
    assert all(len(c) <= 500 for c in chunks)
    assert "\n".join(chunks) == text
    assert split_message("short") == ["short"]
    assert split_message("") == []
