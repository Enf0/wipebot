"""The evals' frozen data (run.py's CACHE, evals/cache.sqlite), kept in the maintainers' private repository beside the
local test cases' (tests/support/local.py), as a release asset (too big for git), zstd-compressed, reached with `gh`
and git signed in to an account that may. It holds rankings and the guide sites' pages, so it's never public.

`python -m evals.data fetch` puts it at evals/cache.sqlite, there not being one yet (`--replace` replaces one).
`push` commits what a run added since (the answers stored after the copy's `store_until`) to DELTAS in that repository,
whose workflow merges it into the release: a Claude Code cloud session may push to git, but not write a release.
Without access, a run fetches what the cases read (README.md)."""
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from compression import zstd
from pathlib import Path

from tests.support.local import commit

CACHE = Path(__file__).parent / "cache.sqlite"
REPO = "Enf0/wipebot-dev"
TAG = "eval-data"
ASSET = "eval-cache.sqlite.zst"
DELTAS = "eval-data"  # the folder its workflow (.github/workflows/eval-data.yml there) merges into the release
MAX_DELTA = 90 * 1024**2  # under git's 100 MB for a file on GitHub


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
        if error := download(REPO, TAG, ASSET, Path(tmp) / ASSET):
            sys.exit(f"couldn't download {ASSET} from {REPO} (gh needs an account that may read it):\n{error}"
                     "Without access, `just evals` fetches what the cases read on its first run (evals/README.md).")
        answers = unpack(Path(tmp) / ASSET, CACHE)
    mark(CACHE)
    print(f"fetched {answers} answers into {CACHE}")


def mark(db: Path) -> None:
    """`db` is in the store up to its newest answer: a push sends only what's stored after."""
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.execute("INSERT OR REPLACE INTO meta SELECT 'store_until', COALESCE(MAX(stored_at), 0) FROM cache")
    con.commit()
    con.close()


def delta(db: Path, archive: Path) -> int:
    """What `db` has that the store hasn't (every answer, without a `store_until`), packed into `archive`; how
    many answers."""
    with tempfile.TemporaryDirectory() as tmp:
        part = Path(tmp) / "delta.sqlite"
        con = sqlite3.connect(db)
        con.execute("ATTACH ? AS d", (str(part),))
        con.execute("CREATE TABLE d.cache (key TEXT PRIMARY KEY, stored_at REAL NOT NULL, value BLOB NOT NULL)")
        has_meta = con.execute("SELECT 1 FROM sqlite_master WHERE name = 'meta'").fetchone()
        row = has_meta and con.execute("SELECT value FROM meta WHERE key = 'store_until'").fetchone()
        con.execute("INSERT INTO d.cache SELECT * FROM cache WHERE stored_at > ?", (float(row[0]) if row else 0,))
        answers = con.execute("SELECT COUNT(*) FROM d.cache").fetchone()[0]
        con.commit()
        con.close()
        if answers:
            pack(part, archive)
    return answers


def push() -> None:
    if not CACHE.exists():
        sys.exit(f"nothing to push: {CACHE} is missing")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / f"{time.strftime('%Y%m%dT%H%M%S')}.sqlite.zst"
        if not (answers := delta(CACHE, archive)):
            print(f"nothing to push: {CACHE} has no answers the store hasn't")
            return
        if (size := archive.stat().st_size) > MAX_DELTA:
            sys.exit(f"{answers} new answers, {size // 1024**2} MB packed: over {MAX_DELTA // 1024**2} MB, too big to "
                     "push through git")

        def put(to: Path) -> None:
            to.mkdir(parents=True, exist_ok=True)
            shutil.copy(archive, to)
        commit(DELTAS, put, f"chore: {answers} new eval answers, for the workflow to merge into the {TAG} release")
    mark(CACHE)
    print(f"pushed {answers} new answers ({size // 1024} KB) to {REPO}, whose workflow merges them into the release")


if __name__ == "__main__":
    action = next((a for a in sys.argv[1:] if not a.startswith("-")), "fetch")
    if action not in ("fetch", "push"):
        sys.exit(f"{action!r}: say fetch (--replace to replace what's here) or push")
    fetch("--replace" in sys.argv) if action == "fetch" else push()
