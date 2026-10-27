"""Cut a release of Wipebot (`just release <version>`): CHANGELOG.md's Unreleased becomes the version's section, dated
today, under a new empty Unreleased; pyproject.toml and uv.lock take the version; then a commit "chore: Wipebot
<version>", pushed to main. Its push makes the tag v<version> and the GitHub release, whose notes are the section
(.github/workflows/release.yml, which reads them with `--notes <version>`): a Claude Code cloud session may push
main, but neither a tag nor a release. Without a version it
says which one the changelog calls for and changes nothing: a minor version for anything Added, Changed or Removed,
a patch for fixes only. What goes in the changelog, and the commit's summary (`--summary`), are written by hand
first; deploying is a deployment's own."""
import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
PYPROJECT = ROOT / "pyproject.toml"
UNRELEASED = "## [Unreleased]"
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
    if not args.version:
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
    CHANGELOG.write_text(cut(changelog, args.version, datetime.date.today()), encoding="utf-8")
    PYPROJECT.write_text(bump(pyproject, args.version), encoding="utf-8")
    run("uv", "lock", "--quiet")
    run("git", "add", "CHANGELOG.md", "pyproject.toml", "uv.lock")
    title = f"Wipebot {args.version}"
    run("git", "commit", "--quiet", "-m", f"chore: {title}" + (f"\n\n{args.summary}" if args.summary else ""))
    run("git", "push", "origin", "main")
    print(f"pushed {title}: the Release workflow makes {tag} and the GitHub release")


if __name__ == "__main__":
    main()
