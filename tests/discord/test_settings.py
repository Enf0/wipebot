"""The settings a host sets: the code, docs/setup.md and .env.example name the same ones."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# os.environ.get("NAME") and os.environ["NAME"], and Wipebot's own by name where a helper reads it
READ = re.compile(r"""environ(?:\.get)?[(\[]\s*["']([A-Z][A-Z0-9_]+)["']|["'](WIPEBOT_[A-Z0-9_]+)["']""")
SETTING = re.compile(r"\b((?:WIPEBOT|WCL|DISCORD|ANTHROPIC|OPENROUTER)_[A-Z0-9_]*[A-Z0-9])\b")
EVALS = {"WIPEBOT_CACHE_FROZEN"}  # the evals freeze their cache with it: no host sets it
# set up by hand where docs/setup.md says how ("Raid notes and plans"), and left out of the example
BY_HAND = {"RAIDSTRATS_CLIENT_ID", "RAIDSTRATS_CLIENT_SECRET", "RSGG_REST_API_KEY", "WOWUTILS_API_KEY"}


def read_by_the_code() -> set[str]:
    """Every setting the code reads from the environment, and those compose.yml takes from .env."""
    names = {a or b for path in (ROOT / "src").rglob("*.py") for a, b in READ.findall(path.read_text(encoding="utf-8"))}
    return names | set(re.findall(r"\$\{([A-Z][A-Z0-9_]+)", (ROOT / "compose.yml").read_text(encoding="utf-8")))


def test_the_docs_and_the_example_name_the_settings_the_code_reads_and_no_others():
    """They drifted: 34 names in the code, 33 in the docs and 20 in the example, which also named commands that were
    gone. A new setting comes with its line in both; one that's removed leaves both."""
    code = read_by_the_code()
    docs = (ROOT / "docs" / "setup.md").read_text(encoding="utf-8")
    example = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M))
    assert {name for name in code - EVALS if f"`{name}" not in docs} == set()  # `WIPEBOT_MODEL`, `WIPEBOT_MODEL=haiku`
    assert example == code - EVALS - BY_HAND
    assert set(SETTING.findall(docs)) <= code  # none the code no longer reads
