"""The evals' frozen data (run.py's CACHE, evals/cache.sqlite), kept in the maintainers' private store as the local
test cases' are (tests/support/local.py): a release asset of a private repository, zstd-compressed, reached with
`gh` signed in to an account that may read it. It holds rankings and the guide sites' pages, so it's never public.

`python -m evals.data fetch` puts it at evals/cache.sqlite, there not being one yet (`--replace`
replaces one); `push` stores this machine's copy. Without access, a run fetches what the cases read (README.md)."""
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from compression import zstd
from pathlib import Path

CACHE = Path(__file__).parent / "cache.sqlite"
REPO = "Enf0/wipebot-dev"
TAG = "eval-data"
ASSET = "eval-cache.sqlite.zst"
MAX_ASSET = 2 * 1024**3  # GitHub's largest release asset


def _gh(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["gh", *args, "--repo", REPO], capture_output=True, text=True)
    except FileNotFoundError:
        sys.exit("this needs GitHub's command line, gh (https://cli.github.com), signed in to an account that may "
                 f"read {REPO}")


def pack(db: Path, archive: Path) -> None:
    """`db`, as one consistent copy (another run may be writing it), compressed into `archive`."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "cache.sqlite"
        src, dst = sqlite3.connect(db), sqlite3.connect(copy)
        src.backup(dst)
        src.close()
        dst.close()
        with open(copy, "rb") as f, zstd.open(archive, "wb", level=9) as out:
            shutil.copyfileobj(f, out, 1 << 20)


def unpack(archive: Path, db: Path) -> int:
    """`archive` unpacked at `db`, only once it reads as the cache; its number of answers."""
    part = db.with_name(db.name + ".part")
    with zstd.open(archive, "rb") as f, open(part, "wb") as out:
        shutil.copyfileobj(f, out, 1 << 20)
    check = sqlite3.connect(part)
    try:
        answers = check.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
    except sqlite3.DatabaseError as e:
        check.close()
        part.unlink()
        sys.exit(f"{ASSET} isn't the evals' cache: {e}")
    check.close()  # Windows can't replace a file still open
    part.replace(db)
    return answers


def fetch(replace: bool = False) -> None:
    if CACHE.exists() and not replace:
        sys.exit(f"{CACHE} is here already: `python -m evals.data fetch --replace` replaces it")
    with tempfile.TemporaryDirectory() as tmp:
        got = _gh("release", "download", TAG, "--pattern", ASSET, "--dir", tmp)
        if got.returncode:
            sys.exit(f"couldn't download {ASSET} from {REPO} (gh needs an account that may read it):\n{got.stderr}"
                     "Without access, `just evals` fetches what the cases read on its first run (evals/README.md).")
        answers = unpack(Path(tmp) / ASSET, CACHE)
    print(f"fetched {answers} answers into {CACHE}")


def push() -> None:
    if not CACHE.exists():
        sys.exit(f"nothing to push: {CACHE} is missing")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / ASSET
        pack(CACHE, archive)
        if (size := archive.stat().st_size) > MAX_ASSET:
            sys.exit(f"{ASSET} is {size // 1024**2} MB, over GitHub's {MAX_ASSET // 1024**3} GB for a release asset")
        if _gh("release", "view", TAG).returncode:
            made = _gh("release", "create", TAG, "--title", "The evals' frozen data",
                       "--notes", "Fetched by evals/data.py in Wipebot.")
            if made.returncode:
                sys.exit(made.stderr)
        sent = _gh("release", "upload", TAG, str(archive), "--clobber")
        if sent.returncode:
            sys.exit(sent.stderr)
        print(f"pushed {size // 1024**2} MB to {REPO}, release {TAG}")


if __name__ == "__main__":
    action = next((a for a in sys.argv[1:] if not a.startswith("-")), "fetch")
    if action not in ("fetch", "push"):
        sys.exit(f"{action!r}: say fetch (--replace to replace what's here) or push")
    fetch("--replace" in sys.argv) if action == "fetch" else push()
