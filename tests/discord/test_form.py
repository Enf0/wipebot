"""An answer's form, measured by code (wipebot.discord.form): what a raider reads of it, its verdict, its bullets and
where the change to make stands, and where it leaves the form the bot's prompt asks for."""
import re
from types import SimpleNamespace
from typing import cast

from wipebot.core.report import Report
from wipebot.discord import form, prompt
from wipebot.discord.form import measure
from wipebot.discord.host import Answer

NUMBER = {1: "one", 2: "two", 3: "three", 5: "five"}  # as the prompt writes its small numbers

KEPT = """\
**One Curse of 15 went through on the kill, at 6:26.**
- ⚠️ **Give every Curse a kicker and a backup.** Five players had a kick ready at 6:26 and nobody used one.
- **The rest were handled:** 11 kicked, 3 ended as the add died.
-# Whose kick it was isn't in the log.
[picture: none]
Follow-up: Who kicked on the wipe before it?
Follow-up: Replay the cast at 6:26?"""


def test_an_answer_that_keeps_to_the_form_has_no_slips():
    f = measure(KEPT)
    assert f.verdict_words == 11 and not f.preamble
    assert f.bullet_words == [20, 12] and f.bullet_sentences == [1, 1]
    assert (f.fix, f.markers, f.small, f.follow_ups, f.pictures) == (1, 1, 1, 2, 0)
    assert not f.closing and f.lines == 4
    assert f.slips() == {} and f.slips(form.LIVE) == {}


def test_what_a_raider_reads_leaves_out_addresses_icons_marks_and_buttons():
    answer = ("**[Sszorak pull 13](https://www.warcraftlogs.com/reports/CODE#fight=21) ended on the enrage.**\n"
              "- <:holy_paladin:123456789> Rowan died to `Tempest` at 2:31.\n"
              "[picture: pull_summary]\n"
              "Follow-up: Why did Rowan die?")
    f = measure(answer)
    assert f.verdict_words == 7  # the link's words, not its address
    assert f.bullet_words == [6]  # the spec's icon is no word, the code marks are none
    assert f.words == 13 and f.pictures == 1 and f.follow_ups == 1
    assert f.posted > f.chars  # Discord counts the address and the icon too
    assert f.numbers == 2  # pull 13 and 2:31; the address's fight=21 isn't read


def test_the_recorded_answer_is_measured_as_the_bot_writes_it():
    """Answer.full: the text with its picture's place and its follow-ups."""
    recorded = Answer(text="**The enrage ended it.**\n- First point.\n- Second point.",
                      marks=[(len("**The enrage ended it.**\n- First point."), "mechanic_hits:by_pull")],
                      follow_ups=["Who died first?"]).full
    f = measure(recorded)
    assert "[picture: mechanic_hits by_pull]" in recorded
    assert (f.bullets, f.pictures, f.follow_ups, f.lines) == (2, 1, 1, 3)


def test_a_bullets_sentences_are_counted_after_its_bold_point():
    f = measure("**Verdict.**\n"
                "- **Move the first Rewind later.** It ran out at 1:02. The spike came at 1:10. Cast it at 1:05.\n"
                "- **Fine:** kicks, f.eks. 0:07 and ca. 6:01, nr. 2 too.\n"
                "- No bold point here. Just two sentences.")
    assert f.bullet_sentences == [3, 1, 2]  # an abbreviation's stop ends no sentence
    assert f.slips() == {"a bullet of many sentences": "1 of 3 bullets over 2 sentences after the bold point"}
    assert "a bullet of many sentences" in f.slips(form.LIVE)
    assert f.slips(form.LIVE)["a bullet of many sentences"].startswith("2 of 3 bullets over 1 sentence after")


def test_a_bullets_words_count_its_own_line_not_the_list_under_it():
    long = " ".join(["word"] * 31)
    f = measure(f"**Verdict.**\n- **Healers:**\n  - Rowan: Rewind at 1:05\n  - Mira: Hymn at 2:10\n- {long}")
    assert f.bullet_words == [1, 31] and f.nested == 2
    assert f.slips() == {"a long bullet": "1 of 2 bullets over 30 words (31)"}


def test_the_verdict_must_open_the_answer_and_stay_short():
    assert measure("- one\n- two").slips() == {"no bold verdict": "opens with no bold verdict"}
    before = measure("I went through the pull. Here's what I found:\n**The enrage ended it.**\n- one")
    assert before.preamble and before.slips() == {"a line before the verdict": "a line comes before the bold verdict"}
    long = measure("**" + " ".join(["word"] * 21) + ".**\n- one")
    assert long.slips() == {"a long verdict": "the verdict is 21 words (at most 20)"}
    assert measure("Hey Rowan! 👋 What's up?").slips() == {}  # no bullets: a greeting has no verdict to open with


def test_what_backs_the_verdict_goes_in_the_bullets_not_between_it_and_them():
    """The bot takes the bold off a second sentence in the opening (discord_text.verdict_bold), so the verdict's
    own words stay within the budget while the opening runs on: a third of the recorded answers had one."""
    second = measure("**The enrage ended it.** The stack after the intermission never formed.\n- one\n- two")
    assert (second.verdict_words, second.beside_verdict) == (4, 7)
    assert second.slips() == {
        "text between the verdict and the bullets": "7 words between the verdict and the first bullet"}
    under = measure("**The enrage ended it.**\nHere is the story of the pull.\n-# Only Mythic pulls.\n- one")
    assert under.beside_verdict == 7 and list(under.slips()) == ["text between the verdict and the bullets"]
    late = measure("I went through the pull.\n**The enrage ended it.** It came at 2:32.\n- one")
    assert late.beside_verdict == 4  # after the verdict; the line before it is the preamble
    assert list(late.slips()) == ["a line before the verdict", "text between the verdict and the bullets"]
    titled = measure("**Verdict.**\n**Rytmen i P1**\n- one\n**P2:**\n- two")
    assert titled.beside_verdict == 0 and titled.slips() == {}  # a part's title has its list under it
    plain = measure("**Yes.** Rowan died at 2:31 to Tempest.")
    assert plain.beside_verdict == 0 and plain.slips() == {}  # no bullets: the line is the whole answer
    assert measure("**Wiped.** The enrage.\n- one").slips(form.post(4, 500)) == {}  # a post has no verdict to keep to


def test_the_change_to_make_is_one_marked_and_first():
    second = measure("**Verdict.**\n- **Fine:** kicks.\n- ⚠️ **Move Rewind.** To 1:05.")
    assert second.fix == 2 and second.slips() == {
        "the change to make isn't first": "the change to make is bullet 2 (the first)"}
    two = measure("**Verdict.**\n- ⚠️ **Move Rewind.** To 1:05.\n- ⚠ **Soak the orbs.** Three went off.")
    assert two.fix == 1 and list(two.slips()) == ["several changes marked"]
    outside = measure("**⚠️ Verdict.**\n- **Fine:** kicks.")
    assert list(outside.slips()) == ["the marker outside the bullets"]
    iconed = measure("**Verdict.**\n- <:shaman:1> ⚠️ **Kick the add.** At 0:40.")
    assert iconed.fix == 1 and iconed.bullet_words == [5]  # neither the icon nor the marker is a word


def test_a_paragraph_after_the_last_bullet_is_a_closing_one_the_small_line_is_not():
    assert measure("**Verdict.**\n- one\n-# Only the last three pulls were logged.").slips() == {}
    closing = measure("**Verdict.**\n- one\n\nOverall a good night with room to grow.")
    assert closing.closing and closing.slips() == {"a closing paragraph": "a paragraph after the last bullet"}
    titled = measure("**Verdict.**\n**Rytmen i P1**\n- one\n**P2**\n- two")
    assert not titled.closing  # a part's title has its list under it


def test_a_text_charts_lines_are_no_bullets():
    f = measure("**Verdict.**\n- one\n```\n- 1:00 ████\n- 2:00 ██\n```")
    assert f.bullets == 1 and not f.closing


def test_more_bullets_than_any_kind_of_question_gets():
    f = measure("**Verdict.**\n" + "\n".join(f"- point {n}" for n in range(6)))
    assert f.slips() == {"many bullets": "6 bullets (at most 5)"}
    assert measure("**Verdict.**\n" + "\n".join(f"{n}. point" for n in range(1, 5))).slips(form.LIVE) == {
        "many bullets": "4 bullets (at most 3)"}


def test_a_post_after_a_wipe_is_held_to_its_lines_and_about_its_characters():
    budget = form.post(4, 500)
    post = "⚠️ Soak the orb at 1:10.\n- no verdict\n- needed\n- here"
    assert measure(post).slips(budget) == {}
    assert measure(post + "\nA fifth line.").slips(budget) == {"many lines": "5 lines (at most 4)"}
    wordy = ("x" * 100 + " ") * 6  # 605 characters: "about 500" is still about up to 625
    assert measure(wordy).slips(budget) == {}
    assert measure(wordy + "x" * 40).slips(budget) == {"a long post": "646 characters to read (about 500)"}


def test_norwegian_is_measured_the_same():
    f = measure("**På 11 av 14 deaths hadde Barkskin holdt deg i live.**\n"
                "- ⚠️ **Bruk healthstone.** På pull 5 døde du av Tempest før 0:30. Åtte sekunder senere kom healen.\n"
                "- **Ikke dine å redde:** 6 deaths til kom i treff som tok mange.")
    assert f.verdict_words == 11 and f.bullet_sentences == [2, 1] and f.fix == 1 and f.slips() == {}


def test_the_budget_holds_the_numbers_the_bots_prompt_sets():
    """host-prompt.md sets the form; a number changed there and not here would measure answers against a rule the
    model no longer reads."""
    said = " ".join(prompt.HOST_PROMPT.split())
    assert f"one short sentence of at most {form.CHAT.verdict_words} words" in said
    assert "what backs it goes in the bullets, never in a second sentence after it" in said  # beside_verdict
    assert f"a night, a player or a plan, at most {NUMBER.get(form.CHAT.bullets)}" in said
    assert f"Then the bullets, each at most {form.CHAT.bullet_words} words" in said
    # after its bold point a bullet has one sentence, and a second for what to do
    assert "then one sentence that backs it" in said and "and a second only where it says what to do" in said
    assert form.CHAT.bullet_sentences == 2
    assert (f"at most {NUMBER.get(form.LIVE.bullets)} bullets of one sentence each, as when asked for something quick, "
            "the fix first") in " ".join(said.split())
    assert form.LIVE.bullet_sentences == 1


def test_the_prompts_own_worked_answers_keep_to_the_form_it_asks_for():
    """The two answers the prompt shows the model, one English and one Norwegian: an example that broke a rule
    beside it would teach the model the rule is soft."""
    shown = re.findall(r'<example question="([^"]+)">\n(.*?)</example>', prompt.HOST_PROMPT, re.S)
    assert len(shown) == 2
    for question, answer in shown:
        f = measure(answer)
        assert f.slips() == {}, question
        assert f.verdict_words and f.fix == 1 and 1 <= f.follow_ups <= 3 and max(f.bullet_words) <= 30
    english, norwegian = (measure(answer) for _, answer in shown)
    assert (english.bullets, english.small) == (4, 0)  # a night: at most five
    assert (norwegian.bullets, norwegian.small) == (2, 1)  # a one-line question: the verdict and at most two


def test_each_kind_of_answer_is_held_to_its_own_budget():
    assert prompt.budget() is form.CHAT and prompt.budget(live=True) is form.LIVE
    a_post = form.post(prompt.POST_LINES, prompt.POST_CHARS)
    assert prompt.budget(post=True, live=True) == a_post  # a post keeps the lines its request sets
    assert a_post.lines == prompt.POST_LINES and a_post.verdict_words is None
    assert prompt.recorded_budget("follow", None) == a_post
    report = cast(Report, SimpleNamespace(start_ms=0, final=False, by_boss={}, guild_name="X", title="Y", code="8TcX"))
    live = prompt.report_line(report) + "\n[asked by Rowan]\nwhy did we wipe?"
    assert prompt.recorded_budget("chat", live) is form.LIVE  # the report's own line says the raid is going on
    assert prompt.recorded_budget("chat", "[...]\nis the raid being logged now?") is form.CHAT  # a raider's words
    assert prompt.recorded_budget("chat", None) is form.CHAT
