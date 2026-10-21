# Contributing

Wipebot is maintained on a best-effort basis. Bug reports and questions raiders ask that it can't answer are the
most useful contributions: open an issue. Fixes are welcome as pull requests. For a feature, open an issue first, so
we can agree it's wanted before you build it: every tool and parameter is one more thing the model must choose
between, so a feature has to be worth what it costs in complexity and seconds per answer.

## Setting up

Needs [uv](https://docs.astral.sh/uv/) and [just](https://just.systems). `uv sync`, then `just` lists the commands.
The tests run offline; `just call` and `just ask` need a Warcraft Logs client in `.env`
([.env.example](.env.example)), and `just ask` a Claude login.

## Before a pull request

- `just check` passes: tests, ruff, ty and the import layers.
- A change to what a tool prints shows in `just snapshot`'s diff: read it, as a snapshot pins what's in it, right or
  wrong. A new tool comes with a snapshot case ([tests/README.md](tests/README.md#adding-a-tools-case)) and an eval
  case.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org) (`fix(agent): ...`); a change raiders
  would notice adds a line to [CHANGELOG.md](CHANGELOG.md).
- Evals cost money and need the maintainers' data, so you don't have to run them: say if your change can move
  answers, and they're run on the pull request.

## The rules

[CLAUDE.md](CLAUDE.md) holds the project's rules. It's written for AI coding agents and applies to everyone; using
one is fine, as long as you've read what you send. In short:

- **Numbers come from code, never from the model.** A tool computes every fact it states; the model explains.
- **Don't hardcode game knowledge** (specs, spell ids, cooldowns): read it from a source through `core/knowledge/`.
- **Test data must be publishable.** Real Warcraft Logs data in the repo comes only from the maintainers' guild's
  reports so far; to add your own guild's, ask in an issue first. Never add other people's reports, Warcraft Logs
  rankings or character pages, Raidbots' data, or text and images from the guide sites: a test that needs them makes
  the data up ([tests/README.md](tests/README.md#where-the-data-comes-from) says how).
- **Keep answers fast:** batch queries, keep on disk what can't change, and make reading many reports opt-in.

## License

What you contribute is under the project's license, the [GNU AGPL v3.0 or later](LICENSE).

## Security

Report a security problem privately, with **Security → Report a vulnerability** on this repository, not in an issue.
