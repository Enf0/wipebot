"""Cutting a release (scripts/release.py): the changelog's Unreleased becomes the version's section, and which version
the changelog calls for."""
import datetime
import json
import subprocess

import pytest

from scripts import release

CHANGELOG = """# Changelog

Intro.

## [Unreleased]

### Added

- `Agent` **New.** Something to ask.

### Fixed

- `Agent` A fix.

## [0.2.0] - 2026-10-22

### Fixed

- `Agent` An older fix.
"""


def test_unreleased_becomes_the_version_dated_under_a_new_empty_one():
    cut = release.cut(CHANGELOG, "0.3.0", datetime.date(2026, 10, 23))
    assert "## [Unreleased]\n\n## [0.3.0] - 2026-10-23\n\n### Added\n\n- `Agent` **New.**" in cut
    assert release.unreleased(cut) == "" and cut.count("## [0.2.0]") == 1
    assert release.unreleased(CHANGELOG) == ("### Added\n\n- `Agent` **New.** Something to ask.\n\n### Fixed\n\n"
                                             "- `Agent` A fix.")


def test_anything_added_or_changed_calls_for_a_minor_version_and_fixes_alone_a_patch():
    assert release.suggest("0.2.0", release.unreleased(CHANGELOG)) == "0.3.0"
    assert release.suggest("0.2.0", "### Fixed\n\n- `Agent` A fix.") == "0.2.1"
    assert release.suggest("1.4.2", "### Changed\n\n- `Discord` Different.") == "1.5.0"


def test_only_the_projects_version_is_bumped():
    pyproject = '[project]\nname = "wipebot"\nversion = "0.2.0"\n\n[tool.x]\nversion = "9.9.9"\n'
    assert release.bump(pyproject, "0.3.0") == pyproject.replace('"0.2.0"', '"0.3.0"')


def test_a_released_versions_notes_are_its_section():
    cut = release.cut(CHANGELOG, "0.3.0", datetime.date(2026, 10, 23))
    assert release.section(cut, "0.3.0") == release.unreleased(CHANGELOG)
    assert release.section(cut, "0.2.0") == "### Fixed\n\n- `Agent` An older fix."


def test_a_line_about_a_picture_needs_the_picture_under_it():
    notes = ("### Added\n\n- `Discord` **The night, drawn.** A new picture\n  of the night.\n"
             "- `Discord` **The map, pictured.** A map.\n  ![The map](https://raw.githubusercontent.com/x/map.webp)\n"
             "- `Agent` **Faster.** No drawing here, no pic.\n- `Agent` A plain fix with a chart in it.")
    assert release.unpictured(notes) == ["The night, drawn.", "A plain fix with a chart in it."]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A throwaway repository (no remote) as the release script's root; `commit(path)` adds a commit touching it."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=tmp_path,
                              check=True, capture_output=True, text=True).stdout.strip()
    git("init", "--quiet", "--initial-branch=main")
    count = iter(range(1000))

    def commit(path: str) -> str:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(f"{next(count)}\n")
        git("add", path)
        git("commit", "--quiet", "-m", f"touch {path}")
        return git("rev-parse", "--short", "HEAD")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    return tmp_path, commit


def full_run(commit: str, date: str) -> dict:
    return {"commit": commit, "date": date, "trials": "2-6 trials", "passed": 190, "of": 207, "errors": 0,
            "verdict": "VERDICT: no change shown."}


def test_a_release_says_how_far_it_is_from_each_modes_last_full_eval_run(repo):
    root, commit = repo
    at = commit("src/wipebot/core/server.py")
    (root / "evals").mkdir()
    (root / "evals" / "full-runs.json").write_text(json.dumps({"bot": full_run(at, "2026-11-01"),
                                                              "plugin": full_run("0000000", "2026-11-05")}))
    for path in ("docs/models.md", "src/wipebot/discord/host-prompt.md", "src/wipebot/mcp/remote.py",
                 "src/wipebot/core/skills/raid-log-analysis/SKILL.md", "evals/cases/wipe.toml", "evals/run.py"):
        commit(path)
    lines, past = release.evals_distance(datetime.date(2026, 11, 10))
    assert lines == [  # the bot's prompt, the skill and a case can move the bot's answers; docs, the server, no
        f"Evals, bot: last full run 2026-11-01 at {at} (2-6 trials, 190/207 answers passed, 0 errors); since then 3 "
        "commits that can change an answer and 9 days.",
        "Evals, plugin: last full run 2026-11-05 at 0000000 (2-6 trials, 190/207 answers passed, 0 errors); since "
        "then an unknown number of commits (its commit isn't in this checkout) and 5 days.",
        "Evals, connector: no full run on record."]
    assert past == []  # a mode without a record, or with a commit not here, stops nothing
    assert release.body("What it brings.", lines) == "What it brings.\n\n" + "\n".join(lines)
    assert release.body("", lines, "the suite is down").endswith(
        "days.\nEvals, connector: no full run on record.\n\nReleased without a full eval run: the suite is down")


def test_a_release_stops_only_past_the_distance(repo, monkeypatch):
    root, commit = repo
    at = commit("src/wipebot/core/server.py")
    (root / "evals").mkdir()
    (root / "evals" / "full-runs.json").write_text(json.dumps({"bot": full_run(at, "2026-11-01")}))
    monkeypatch.setattr(release, "EVAL_COMMITS", 2)
    commit("src/wipebot/core/a.py")
    commit("src/wipebot/core/b.py")
    assert release.evals_distance(datetime.date(2026, 11, 2))[1] == []  # at the limit, not past it
    commit("src/wipebot/core/c.py")
    assert release.evals_distance(datetime.date(2026, 11, 2))[1] == ["bot (3 commits, 1 days)"]
    monkeypatch.setattr(release, "EVAL_COMMITS", 30)
    assert release.evals_distance(datetime.date(2026, 11, 22))[1] == []  # 21 days
    assert release.evals_distance(datetime.date(2026, 11, 23))[1] == ["bot (3 commits, 22 days)"]
    (root / "evals" / "full-runs.json").unlink()
    lines, past = release.evals_distance(datetime.date(2026, 11, 23))
    assert past == [] and lines[0] == "Evals, bot: no full run on record."
