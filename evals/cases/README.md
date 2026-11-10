# Eval cases

Eval cases: the questions raiders and raid leaders ask after a pull and after a night, on the logs of The
Dragonfell Tribe (EU-Kazzak), the maintainers' guild. Each has the main point a right
answer makes, and what would make it wrong: often the tempting misreading of the log.

  id        its name: unique over every file, what `-k` picks by and what a baseline keeps its results under
  question  what is asked, with the context a Discord message gives (asker, discord_name, pull, boss);
            a list for follow-ups in one conversation (the last answer is judged)
  where     where it is asked as the bot ("in a DM"; a server channel when left out); a client's chat elsewhere
  expect    the main point a right answer gets across (a judge checks it; it fails the answer). What a right
            answer may also say goes here in plain words ("naming the few held long is fair"): the judge reads
            the expect and the wrong list, never a comment beside them
  facts     supporting facts a full answer states (counted apart; a missing one doesn't fail it)
  wrong     what would make it wrong, each a claim an answer could make (a judge checks the answer states none);
            something a right answer must hold belongs in `expect`, a call it must make in `calls`
  report    when it isn't the file's own (`[defaults]`)
  numbers   numbers of the `expect` that code looks for in the answer, and fails it without: the time or count
            that is the case's point, read as the lint reads a number (2:32 and 02:32 are one). An entry may
            name the same moment two ways ("7:58 or 7:59": the cast, and its deaths a second later). Only
            numbers the expect states (the loader refuses another), and never one from a ranking
            (wipe-tank-death: `numbers = ["7:00", "7:13"]`, the tank's death and the Stone Breaker set after it)
  form      for the few cases whose point is the form: the most the answer may hold as the bot, measured by
            code (`wipebot.discord.form`) and failing it beyond: `bullets`, `bullet_sentences`, `bullet_words`,
            `verdict_words`, `lines`, `chars` (about so many, as a post's). Not checked in a client, whose form
            is its own (live-raid-plain-question: `form = {bullets = 3, bullet_sentences = 1}`; the posts:
            `form = {lines = 4, chars = 500}`)
  derived   numbers in `expect` or `facts` that no tool prints as such, since the case's writer worked them out
            (a gap between two times, a count of the output's lines), with the arithmetic in a comment:
            `just evals --lint` doesn't flag them (real-raid-dps-at-a-moment)
  drifts    numbers from rankings or the top parses, which move when the frozen data is fetched again: the lint
            reports one that no tool prints now apart, and it never fails the lint (me-played-well)
  tools     the tools its right answers call (tagged from the runs: those at least two in three passing answers
            called); `just evals --coverage` counts them, and a check fails when a core tool has no case
  calls     tools an answer must call, or it fails: a step of the method its text doesn't show (`spec_guide`
            before advising a player how to play their spec); the judge sees only the answer;
            not checked with the connector only, which has no skill to give it the step
  asks_first  tools only the last question may call, and it must: what the user has to agree to first (sending a
            report with `report_answer`), checked in every mode
  live      true: asked while the raid is going on (the report's line says "being logged now", though the
            frozen report is over), where the bot answers as between pulls; only `--bot` has that line
  request   for a post after a wipe (`posts.toml`): what the raider asked to have posted; without it, the
            asker's own feedback. The question is built as the bot builds it, so a post case has none
  cause     for a wipe: what an experienced raid leader names first ("enrage: Unbound Ferocity", "ability: Mutilate",
            "deaths from 0:46", "reset"), which `just evals --endings` checks pull_summary's "Ended by" against

A case and the baselines' results for it carry a hash of what an answer is asked with and what the judge is asked:
question, expect, facts, wrong, calls, the report and who asks. A changed word there makes the case a new one to its
baseline, to be asked three times again (`just evals -k <id> --trials 3 --save-baseline` puts it into the baseline
without a run of the suite). What only code reads is left out of the hash: `numbers`, `form`, `derived`, `drifts`,
`tools` and `cause`. There is no `hard` mark any more: a run splits its total by what the baseline passed always,
sometimes and never.

One file per kind of question; `bot.toml` holds the cases on the Discord bot's own rules, which only `--bot` runs,
and `connector.toml` the connector's own (an officer saving a pasted note, a raider reporting an answer), which every mode but `--bot` runs.
`posts.toml` holds the bot's posts after a wipe (`follow_pulls`), asked as the bot asks itself when a pull ends:
`--bot` only.
`heldout.toml` holds cases written blind on a night nothing else was built on; only `--held-out` runs them, alone,
and only at milestones (a release), never while changing the method: a gap between them and the regular cases is the
method fitted to its own cases.
Every file's cases share one id space (`-k` picks by id) and `[defaults]`.

## What a Discord message carries

A raider's question reaches the bot with more than its text: the conversation before it, the server's saved raid
note, what the bot remembers of the asker, the server's language and today's date. A case sets them with the keys
below, or its file's `[defaults]` does for all of its cases (`report`, `set_up`, `language`, `today` and `where` may
stand there). A case with none of them is asked as it always was (`evals/asked.py`).

  set_up    true: asked from the guild's own Discord server, as `/wipebot setup` leaves it. With `--bot` the
            question's Context is then made by the bot's own code, so its first lines hold the guild, the server's
            language, what the bot remembers of the asker and the bosses with a saved note, and `remember` and
            `answer_in_dm` are the bot's own tools. The keys below but `today` need it
  earlier   what was said in the place before the first question: `[who, text]` pairs, oldest first, handed over as
            the bot hands over a place's earlier messages. "Wipebot" is the bot, answering whoever wrote before it.
            A conversation asked for real, question by question, is a list in `question`, as before
  note      a raid note an officer pasted before the night's first pull: a file under `tests/fixtures/real/notes/`
            (the guild's own), or under `evals/cases/notes/` when a case needs a note no night had.
            `raid_note` finds it as in a real server, and the other tools say that one is saved
  notes     what the bot remembers of the asker: a list of lines, as the `remember` tool keeps them
  language  the server's language, as its admin set it ("Norwegian")
  today     the day the question is asked, in quotes ("2026-10-22"): the date in the question's first line and the
            tools' clock, at noon UTC. Without it the day is the recorded data's, 13 October 2026. In every mode

A case with `earlier`, `note` or `notes` is asked only with `--bot`: a client has none of them, so there it would be
another question. `set_up` and `language` alone leave a case in every mode, where the clients ignore them.

- Give a case on a night after 13 October its `today`, the day after its log, so that "last night" and the report's
  "yesterday" are true. A `today` later than the day the frozen data was last added to (`tested`'s `data`) finds no
  later night: the lists of reports were fetched then.
- `just evals --lint` replays a case's recorded calls on the recorded data's day and with no note saved, so it checks
  the numbers of a `today` or `note` case only where the call doesn't turn on either.
- Questions are in English or Norwegian, as raiders ask: Norwegian bokmål with the game's words in English (pull,
  wipe, soak, kick). They are written for the case, never copied from a raider. `expect`, `facts` and `wrong` stay
  in English: the judge reads the answer in either language.
- An answer in another language than its question fails, by code, whatever the judge made of it (the `language`
  check, `evals/checks.py`): the bot's own way of telling Norwegian from English, on the last question, then on the
  ones before it, then, as the bot, on the server's `language`. A post and an answer whose language doesn't show are
  held to nothing.

The files these keys came with: `norsk.toml` (questions in Norwegian, of every kind), `conversations.toml` (late in a
conversation, a raider pushing back who is right, an instruction in someone's earlier message), `notes.toml` (a note
saved in the server; one holds a line addressed to the bot) and `server.toml` (another night than the one at hand,
names asked for in the raid's channel, a game rule with no log). A case that tells the bot what to do from inside a
note or an earlier message has the obeying in `wrong`: what a raider or a note says is talk, the log is the answer.
The held-out cases are on two pulls no other case reads; what they expect stands in `heldout.toml` and nowhere else.
