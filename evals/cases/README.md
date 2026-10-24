# Eval cases

Eval cases: the questions raiders and raid leaders ask after a pull and after a night, on the logs of The
Dragonfell Tribe (EU-Kazzak), the maintainers' guild. Each has the main point a right
answer makes, and what would make it wrong: often the tempting misreading of the log.

  question  what is asked, with the context a Discord message gives (asker, discord_name, pull, boss);
            a list for follow-ups in one conversation (the last answer is judged)
  expect    the main point a right answer gets across (a judge checks it; it fails the answer)
  facts     supporting facts a full answer states (counted apart; a missing one doesn't fail it)
  wrong     what would make it wrong (a judge checks the answer states none)
  wrong_in_channel  more of it in the server channel only (naming who died or had a kick ready): the bot's rule,
            left out with the connector only, where the asker reads the answer in their own chat
  report    when it isn't
  tools     the tools its right answers call (tagged from the runs: those at least two in three passing answers
            called); `just evals --coverage` counts them, and a check fails when a core tool has no case
  calls     tools an answer must call, or it fails: a step of the method its text doesn't show (`spec_guide`
            before advising a player how to play their spec); the judge sees only the answer
  cause     for a wipe: what an experienced raid leader names first ("enrage: Unbound Ferocity", "ability: Mutilate",
            "deaths from 0:46", "reset"), which `just evals --endings` checks pull_summary's "Ended by" against

One file per kind of question; `bot.toml` holds the cases on the Discord bot's own rules, which only `--bot` runs,
and `connector.toml` the connector's own (an officer saving a pasted note), which every mode but `--bot` runs.
`heldout.toml` holds cases written blind on a night nothing else was built on; only `--held-out` runs them, alone,
and only at milestones (a release), never while changing the method: a gap between them and the regular cases is the
method fitted to its own cases.
Every file's cases share one id space (`-k` picks by id) and `[defaults]`.
