"""Cut a release of Wipebot (`just release <version>`): CHANGELOG.md's Unreleased becomes the version's section, dated
today, under a new empty Unreleased; pyproject.toml and uv.lock take the version; then a commit "chore: Wipebot
<version>", pushed to main. Once Checks passes on it, the Release workflow makes the tag v<version> and the GitHub
release, whose notes are the section (.github/workflows/release.yml, which reads them with `--notes <version>`): a
Claude Code cloud session may push main, but neither a tag nor a release. Without a version it
says which one the changelog calls for and changes nothing: a minor version for anything Added, Changed or Removed,
a patch for fixes only. What goes in the changelog, and the commit's summary (`--summary`), are written by hand
first; deploying is a deployment's own. A line about a picture (one drawn, changed or shown) needs that picture
under it, as the website, the release notes and Discord's what's new show it: the release stops without, unless
`--without-pictures`.

It also says how far the release is from the evals: for each mode, how many commits that can change an answer and how
many days lie since its last recorded run: of the smoke set (evals/smoke-runs.json, `just evals --smoke`, the check a
small plan's usage affords) or a full one (evals/full-runs.json), whichever is later. A commit
can change a mode's answers when it touches what the mode answers with (ANSWERS), or says `full eval: pending` and
touches no mode's (a dependency, say). That is printed with or without a version and written into the release
commit's body. Past EVAL_COMMITS such commits or EVAL_DAYS days the release stops, until a run is recorded or
`--without-evals "<reason>"` says why not; the reason goes into the commit's body too."""
import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
PYPROJECT = ROOT / "pyproject.toml"
UNRELEASED = "## [Unreleased]"
FULL_RUNS, SMOKE_RUNS = Path("evals") / "full-runs.json", Path("evals") / "smoke-runs.json"
# What an answer is made of, per eval mode: a commit that touches one of these since the mode's last full run can have
# moved its answers. The core (the tools and the skill) and the cases are every mode's; then the app that answers.
ANSWERS = {"bot": ("src/wipebot/core", "src/wipebot/discord", "evals/cases"),
           "plugin": ("src/wipebot/core", "src/wipebot/mcp", "evals/cases"),
           "connector": ("src/wipebot/core", "src/wipebot/mcp", "evals/cases")}
# How far from a mode's last full run a release may be: past either, it stops (--without-evals "<reason>" to pass).
# By distance and not at every release: a gate that stops each one is overridden each time.
EVAL_COMMITS, EVAL_DAYS = 30, 21
# What a commit's body says when it can move answers and no full run has covered it yet (CLAUDE.md), as git greps it
PENDING = r"full evals\?: pending"
VERSION = re.compile(r'^version = "([^"]+)"$', re.M)  # the first is [project]'s


def unreleased(changelog: str) -> str:
    """The Unreleased section's text, without its heading."""
    start = changelog.index(UNRELEASED) + len(UNRELEASED)
    end = changelog.find("\n## [", start)
    return changelog[start:end if end != -1 else len(changelog)].strip()


def section(changelog: str, version: str) -> str:
    """A released version's section, without its heading: its release notes."""
    start = changelog.index("\n", changelog.index(f"## [{version}]"))
    end = changelog.find("\n## [", start)
    return changelog[start:end if end != -1 else len(changelog)].strip()


# What a changelog line says when raiders see something drawn: its picture belongs under it
DRAWN = re.compile(r"\b(?:pictures?|draw[sn]?|chart|map|meters?|card|image|screenshot)\b", re.I)


def unpictured(notes: str) -> list[str]:
    """The bold leads (else starts) of the lines that talk about a picture and have none under them."""
    items: list[str] = []
    for line in notes.splitlines():
        if line.startswith("- "):
            items.append(line[2:])
        elif line.startswith("  ") and items:
            items[-1] += "\n" + line.strip()
    out = []
    for item in items:
        if DRAWN.search(re.sub(r"!\[[^\]]*\]\([^)]*\)", "", item)) and "![" not in item:
            lead = re.search(r"\*\*(.+?)\*\*", item)
            out.append(lead[1] if lead else re.sub(r"^(?:`[^`]+`\s*)*", "", item)[:60])
    return out


def suggest(current: str, notes: str) -> str:
    """The next version for these notes: a minor one for anything added, changed or removed, else a patch."""
    major, minor, patch = (int(x) for x in current.split("."))
    if re.search(r"^### (Added|Changed|Removed)$", notes, re.M):
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def cut(changelog: str, version: str, day: datetime.date) -> str:
    """The changelog with Unreleased renamed to the version, dated, under a new empty Unreleased."""
    return changelog.replace(UNRELEASED, f"{UNRELEASED}\n\n## [{version}] - {day.isoformat()}", 1)


def bump(pyproject: str, version: str) -> str:
    return VERSION.sub(f'version = "{version}"', pyproject, count=1)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def run(*args: str) -> None:
    print("$", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True, text=True)


def since(commit: str, *paths: str, pending: bool = False) -> set[str]:
    """The commits after `commit` up to HEAD: those that touch `paths`, or those that say a full eval is pending."""
    return set(git("rev-list", *(("-i", f"--grep={PENDING}") if pending else ()), f"{commit}..HEAD", "--",
                   *paths).split())


def some(n: int, what: str) -> str:
    return f"{n} {what}{'s' * (n != 1)}"


def evals_distance(today: datetime.date) -> tuple[list[str], list[str]]:
    """How far HEAD is from each mode's last recorded full eval run: a line per mode, for the terminal and the
    release commit's body, and the modes past EVAL_COMMITS answer-changing commits or EVAL_DAYS days, with why. A
    commit counts for a mode when it touches the mode's ANSWERS, and for every mode when it says a full eval is
    pending and touches no mode's: which answers it moves, its paths can't say. A mode with no run on record, or
    one whose commit this checkout doesn't have, is said and stops nothing."""
    runs, smoke = ({} if not (ROOT / path).exists() else json.loads((ROOT / path).read_text(encoding="utf-8"))
                   for path in (FULL_RUNS, SMOKE_RUNS))
    for mode, last in smoke.items():  # the later of the two is the one a release counts from
        if mode not in runs or last["date"] >= runs[mode]["date"]:
            runs[mode] = last | {"trials": ""}
    anywhere = sorted({p for paths in ANSWERS.values() for p in paths})
    lines, past = [], []
    for mode, paths in ANSWERS.items():
        if not (last := runs.get(mode)):
            lines.append(f"Evals, {mode}: no full run on record.")  # nor one of the smoke set
            continue
        days = (today - datetime.date.fromisoformat(last["date"])).days
        known = subprocess.run(["git", "cat-file", "-e", f"{last['commit']}^{{commit}}"], cwd=ROOT,
                               capture_output=True).returncode == 0
        if known:
            pending = since(last["commit"], pending=True)
            counted = since(last["commit"], *paths) | (pending - since(last["commit"], *anywhere))
            commits, said = len(counted), len(counted & pending)
            far = f"{some(commits, 'commit')} that can change an answer" + (
                f" ({said} of them say{'s' * (said == 1)} a full eval is pending)" if said else "")
        else:
            commits, far = None, "an unknown number of commits (its commit isn't in this checkout)"
        kind = f"full run {last['date']} at {last['commit']} ({last['trials']}, " if last["trials"] else (
            f"smoke run {last['date']} at {last['commit']} (")
        lines.append(f"Evals, {mode}: last {kind}"
                     f"{last['passed']}/{last['of']} answers passed, {last['errors']} errors); since then {far} "
                     f"and {some(days, 'day')}.")
        if (commits or 0) > EVAL_COMMITS or days > EVAL_DAYS:
            past.append(f"{mode} ({'? commits' if commits is None else some(commits, 'commit')}, "
                        f"{some(days, 'day')})")
    return lines, past


def too_far(past: list[str]) -> str:
    """What a release past the distance is told, and what the run without a version says it would be told."""
    return (f"Too far from the last eval run: {', '.join(past)}; the limit is {EVAL_COMMITS} commits that "
            f"can change an answer or {EVAL_DAYS} days. Ask the smoke set (`just evals --smoke`, in the modes "
            "named) and commit evals/smoke-runs.json, or run the full suite where the login's usage allows it "
            "(`just evals --trials auto`, evals/full-runs.json), or say why not: --without-evals \"<reason>\".")


def body(summary: str, distance: list[str], without_evals: str = "") -> str:
    """The release commit's body: what the version brings, then how far it is from the evals, and why it went out
    past the limit when it did."""
    return "\n\n".join(part for part in (summary, "\n".join(distance),
                                         f"Released without an eval run: {without_evals}" if without_evals else "")
                       if part)


def ready() -> str:
    """Why the tree can't be released from, or ''."""
    if git("status", "--porcelain", "--untracked-files=no"):
        return "commit or stash the changes first"
    if git("branch", "--show-current") != "main":
        return "release from main"
    git("fetch", "--quiet", "origin", "main")
    if subprocess.run(["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"], cwd=ROOT).returncode:
        return "origin/main has commits this checkout lacks: pull first"
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("version", nargs="?", help="e.g. 0.3.0; omit to see which the changelog calls for")
    parser.add_argument("--summary", default="", help="the release commit's body: what the version brings")
    parser.add_argument("--notes", action="store_true", help="only print the released version's notes")
    parser.add_argument("--without-pictures", action="store_true",
                        help="release even with lines about a picture that have none under them")
    parser.add_argument("--without-evals", default="", metavar="REASON",
                        help=f"release although a mode's last eval run is more than {EVAL_COMMITS} "
                             f"answer-changing commits or {EVAL_DAYS} days back: why, kept in the commit's body")
    args = parser.parse_args()
    changelog, pyproject = CHANGELOG.read_text(encoding="utf-8"), PYPROJECT.read_text(encoding="utf-8")
    if args.notes:
        print(section(changelog, args.version))
        return
    if not (found := VERSION.search(pyproject)):
        sys.exit("pyproject.toml has no version")
    current = found[1]
    notes = unreleased(changelog)
    if not notes:
        sys.exit("CHANGELOG.md has nothing under Unreleased")
    distance, past = evals_distance(datetime.date.today())
    print("\n".join(distance))
    if not args.version:
        if past:
            print("A release would stop here. " + too_far(past))
        print(f"Wipebot {current}; the changelog calls for {suggest(current, notes)}: just release "
              f"{suggest(current, notes)} --summary \"...\"")
        return
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        sys.exit(f"not a version: {args.version}")
    tag = f"v{args.version}"
    if git("tag", "--list", tag):
        sys.exit(f"{tag} exists already")
    if why := ready():
        sys.exit(why)
    if (missing := unpictured(notes)) and not args.without_pictures:
        sys.exit("These lines talk about a picture but have none under them (docs/images, by its "
                 "raw.githubusercontent.com address): " + "; ".join(missing) + ". Add them, or --without-pictures.")
    if past and not args.without_evals:
        sys.exit(too_far(past))
    CHANGELOG.write_text(cut(changelog, args.version, datetime.date.today()), encoding="utf-8")
    PYPROJECT.write_text(bump(pyproject, args.version), encoding="utf-8")
    run("uv", "lock", "--quiet")
    run("git", "add", "CHANGELOG.md", "pyproject.toml", "uv.lock")
    title = f"Wipebot {args.version}"
    run("git", "commit", "--quiet", "-m", f"chore: {title}\n\n" + body(args.summary, distance, args.without_evals))
    run("git", "push", "origin", "main")
    print(f"pushed {title}: once Checks passes, the Release workflow makes {tag} and the GitHub release")


if __name__ == "__main__":
    main()
