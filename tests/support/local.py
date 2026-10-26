"""The local cases' recordings and snapshots (recorded.py's LOCAL), kept in the maintainers' private store: a release
asset of a private repository, reached with `gh` signed in to an account that may read it. Anyone else records them
afresh with `just record-tools` (tests/README.md).

`python -m tests.support.local fetch` puts them in LOCAL; `push`, after `just record-tools`
or a changed local snapshot, replaces the stored copy."""
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from .recorded import LOCAL

REPO = "Enf0/wipebot-dev"
TAG = "local-fixtures"
ASSET = "local-fixtures.tar.gz"


def _gh(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["gh", *args, "--repo", REPO], capture_output=True, text=True)
    except FileNotFoundError:
        sys.exit("this needs GitHub's command line, gh (https://cli.github.com), signed in to an account that may "
                 f"read {REPO}")


def download(repo: str, tag: str, asset: str, dest: Path) -> str:
    """A release's asset into `dest`, through GitHub's REST API: `gh release download` takes GraphQL, which a Claude
    Code cloud session can't reach. What went wrong, or "" once it's there."""
    try:
        found = subprocess.run(["gh", "api", f"repos/{repo}/releases/tags/{tag}",
                                "--jq", f'.assets[] | select(.name == "{asset}") | .id'],
                               capture_output=True, text=True)
    except FileNotFoundError:
        sys.exit("this needs GitHub's command line, gh (https://cli.github.com), signed in to an account that may "
                 f"read {repo}")
    if found.returncode or not found.stdout.strip():
        return found.stderr or f"no {asset} in the release {tag}\n"
    with open(dest, "wb") as out:
        got = subprocess.run(["gh", "api", "-H", "Accept: application/octet-stream",
                              f"repos/{repo}/releases/assets/{found.stdout.strip()}"],
                             stdout=out, stderr=subprocess.PIPE)
    return got.stderr.decode() if got.returncode else ""


def fetch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        if error := download(REPO, TAG, ASSET, Path(tmp) / ASSET):
            sys.exit(f"couldn't download {ASSET} from {REPO} (gh needs an account that may read it):\n{error}"
                     "Without access, `just record-tools` with your own Warcraft Logs keys records the local cases "
                     "afresh (tests/README.md).")
        with tarfile.open(Path(tmp) / ASSET) as tar:
            tar.extractall(LOCAL.parent, filter="data")
    print(f"fetched {len(list(LOCAL.iterdir()))} files into {LOCAL}")


def push() -> None:
    if not LOCAL.exists():
        sys.exit(f"nothing to push: {LOCAL} is missing")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / ASSET
        with tarfile.open(archive, "w:gz") as tar:
            for f in sorted(LOCAL.iterdir()):
                tar.add(f, arcname=f"{LOCAL.name}/{f.name}")
        if _gh("release", "view", TAG).returncode:
            made = _gh("release", "create", TAG, "--title", "The local test cases' recordings and snapshots",
                       "--notes", "Fetched by tests/support/local.py in Wipebot.")
            if made.returncode:
                sys.exit(made.stderr)
        sent = _gh("release", "upload", TAG, str(archive), "--clobber")
        if sent.returncode:
            sys.exit(sent.stderr)
        print(f"pushed {archive.stat().st_size // 1024} KB to {REPO}, release {TAG}")


if __name__ == "__main__":
    {"fetch": fetch, "push": push}[sys.argv[1] if len(sys.argv) > 1 else "fetch"]()
