"""The local cases' recordings and snapshots (recorded.py's LOCAL), kept in the maintainers' private store: the folder
tests/local/ of a private repository, read with `gh` and written with git, signed in to an account that may. Anyone
else records them afresh with `just record-tools` (tests/README.md).

`python -m tests.support.local fetch` puts them in LOCAL; `push`, after `just record-tools` or a changed local
snapshot, commits them there: only the files changed here since the fetch, taking the ones changed there, so two
checkouts don't put their older files over each other's newer ones. `replace` makes the store a copy of LOCAL, after
recording everything afresh. None takes GitHub's GraphQL API or its releases, which a Claude Code cloud session can't
use."""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from .recorded import LOCAL

REPO = "Enf0/wipebot-dev"
FOLDER = "tests/local"
FETCHED = ".fetched.json"  # in LOCAL: each file's hash in the store when this copy was fetched or last pushed


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, cwd=cwd)
    except FileNotFoundError:
        sys.exit(f"this needs {args[0]}, and for gh (https://cli.github.com) an account that may read {REPO}")


def fetch() -> None:
    files = _run("gh", "api", f"repos/{REPO}/git/trees/main?recursive=1",
                 "--jq", f'.tree[] | select(.type == "blob" and (.path | startswith("{FOLDER}/"))) | .path')
    if files.returncode or not files.stdout.strip():
        sys.exit(f"couldn't read {FOLDER} in {REPO} (gh needs an account that may read it):\n{files.stderr}"
                 "Without access, `just record-tools` with your own Warcraft Logs keys records the local cases "
                 "afresh (tests/README.md).")
    paths = files.stdout.splitlines()
    for path in paths:
        to = LOCAL / path.removeprefix(f"{FOLDER}/")
        to.parent.mkdir(parents=True, exist_ok=True)
        with open(to, "wb") as out:
            got = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.raw",
                                  f"repos/{REPO}/contents/{path}"], stdout=out, stderr=subprocess.PIPE)
        if got.returncode:
            sys.exit(f"couldn't download {path} from {REPO}:\n{got.stderr.decode()}")
    theirs = {path.removeprefix(f"{FOLDER}/") for path in paths}
    (LOCAL / FETCHED).write_text(json.dumps({f: h for f, h in hashes(LOCAL).items() if f in theirs}, indent=1))
    print(f"fetched {len(paths)} files into {LOCAL}")


def hashes(folder: Path) -> dict[str, str]:
    """Each file of a copy of the store, by its path in it, with its content's hash."""
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file() and p.name != FETCHED and "__pycache__" not in p.parts}


def merge(local: Path, store: Path, was: dict[str, str] | None) -> tuple[list[str], list[str]]:
    """Brings `local` and `store` (the store's folder as it is now) to one state, and says which files went to the
    store and which came from it. `was` is the store's hashes when `local` was fetched: a file changed only here
    since goes there, one changed only there comes here (a file gone on one side is a change), and one changed on
    both sides stops it, before anything moved. Without `was` (recorded afresh, or fetched before there was one) the
    store becomes a copy of `local`."""
    mine, theirs = hashes(local), hashes(store)
    differ = sorted(f for f in mine.keys() | theirs.keys() if mine.get(f) != theirs.get(f))
    if was is None:
        sent, taken = differ, []
    else:
        sent = [f for f in differ if theirs.get(f) == was.get(f)]
        taken = [f for f in differ if mine.get(f) == was.get(f) and f not in sent]
        if both := [f for f in differ if f not in sent and f not in taken]:
            sys.exit(f"changed both here and in {REPO} since the fetch: {', '.join(both)}. Fetch again "
                     "(`python -m tests.support.local fetch`, which puts the store's over these), redo the "
                     "recording or snapshot that changed them, and push.")
    for files, source, to in ((sent, local, store), (taken, store, local)):
        for f in files:
            if (source / f).exists():
                (to / f).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / f, to / f)
            else:
                (to / f).unlink()
    return sent, taken


def commit(folder: str, put: Callable[[Path], None], message: str) -> bool:
    """A commit to `folder` in REPO, pushed: `put` fills it (in a sparse clone, only that folder checked out).
    False when that changed nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "store"
        for step in (("git", "clone", "--quiet", "--depth", "1", "--filter=blob:none", "--sparse",
                      f"https://github.com/{REPO}.git", str(repo)),
                     ("git", "-C", str(repo), "sparse-checkout", "set", folder)):
            if (done := _run(*step)).returncode:
                sys.exit(done.stderr)
        put(repo / folder)
        _run("git", "add", "--all", folder, cwd=repo)
        if not _run("git", "status", "--porcelain", cwd=repo).stdout.strip():
            return False
        for step in (("git", "commit", "--quiet", "-m", message), ("git", "push", "--quiet")):
            if (done := _run(*step, cwd=repo)).returncode:
                sys.exit(done.stderr)
    return True


def push(replace: bool = False) -> None:
    if not LOCAL.exists():
        sys.exit(f"nothing to push: {LOCAL} is missing")
    fetched = LOCAL / FETCHED
    was = json.loads(fetched.read_text()) if fetched.exists() and not replace else None
    sent: list[str] = []
    taken: list[str] = []

    def put(to: Path) -> None:
        to.mkdir(parents=True, exist_ok=True)
        if was is None and not replace and hashes(to):
            sys.exit(f"{LOCAL} doesn't say when it was fetched (it was fetched before that was written down, or "
                     f"recorded afresh), so its files may be older than {REPO}'s. Fetch (`python -m "
                     "tests.support.local fetch`, which puts the store's over these), redo the recording or "
                     "snapshot you changed, and push; or `replace`, to make the store a copy of this one.")
        moved = merge(LOCAL, to, was)
        sent.extend(moved[0])
        taken.extend(moved[1])
    wipebot = _run("git", "rev-parse", "--short", "HEAD").stdout.strip() or "a working tree"
    if commit(FOLDER, put, f"test: the local cases' recordings and snapshots, from Wipebot {wipebot}"):
        print(f"pushed {', '.join(sent)} to {REPO}, {FOLDER}")
    else:
        print(f"{REPO} has these already: nothing to push")
    if taken:
        print(f"took what changed there since the fetch: {', '.join(taken)}")
    fetched.write_text(json.dumps(hashes(LOCAL), indent=1))


if __name__ == "__main__":
    actions = {"fetch": fetch, "push": push, "replace": lambda: push(replace=True)}
    actions[sys.argv[1] if len(sys.argv) > 1 else "fetch"]()
