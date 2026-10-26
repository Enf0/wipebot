"""The local cases' recordings and snapshots (recorded.py's LOCAL), kept in the maintainers' private store: the folder
tests/local/ of a private repository, read with `gh` and written with git, signed in to an account that may. Anyone
else records them afresh with `just record-tools` (tests/README.md).

`python -m tests.support.local fetch` puts them in LOCAL; `push`, after `just record-tools` or a changed local
snapshot, commits them there. Neither takes GitHub's GraphQL API or its releases, which a Claude Code cloud session
can't use."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .recorded import LOCAL

REPO = "Enf0/wipebot-dev"
FOLDER = "tests/local"


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
    print(f"fetched {len(paths)} files into {LOCAL}")


def push() -> None:
    if not LOCAL.exists():
        sys.exit(f"nothing to push: {LOCAL} is missing")
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "store"
        for step in (("git", "clone", "--quiet", "--depth", "1", "--filter=blob:none", "--sparse",
                      f"https://github.com/{REPO}.git", str(repo)),
                     ("git", "-C", str(repo), "sparse-checkout", "set", FOLDER)):
            if (done := _run(*step)).returncode:
                sys.exit(done.stderr)
        shutil.rmtree(repo / FOLDER, ignore_errors=True)
        shutil.copytree(LOCAL, repo / FOLDER, ignore=shutil.ignore_patterns("__pycache__"))
        _run("git", "add", "--all", FOLDER, cwd=repo)
        if not _run("git", "status", "--porcelain", cwd=repo).stdout.strip():
            print(f"{REPO} has these already: nothing to push")
            return
        wipebot = _run("git", "rev-parse", "--short", "HEAD").stdout.strip() or "a working tree"
        message = f"test: the local cases' recordings and snapshots, from Wipebot {wipebot}"
        for step in (("git", "commit", "--quiet", "-m", message), ("git", "push", "--quiet")):
            if (done := _run(*step, cwd=repo)).returncode:
                sys.exit(done.stderr)
    print(f"pushed {LOCAL} to {REPO}, {FOLDER}")


if __name__ == "__main__":
    {"fetch": fetch, "push": push}[sys.argv[1] if len(sys.argv) > 1 else "fetch"]()
