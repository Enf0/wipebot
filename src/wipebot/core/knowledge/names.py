"""Names as each site spells them: slugs, spaced class names, a boss's words for matching titles and typed names."""
import re

from ..text import fold


def fold_quotes(s: str) -> str:
    return s.replace("’", "'")


def slug(name: str) -> str:
    """"Nek'zali the Soulcoiler" -> 'nekzali-the-soulcoiler', the way Method, Wowhead and Lorrgs slug names."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "")).strip("-")


def spaced(name: str) -> str:
    """'DeathKnight' -> 'Death Knight'."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def lorrgs_slug(class_name: str, spec: str) -> str:
    """WCL's 'DeathKnight', 'BeastMastery' -> Lorrgs' 'deathknight-beastmastery'."""
    return f"{class_name}-{spec}".replace(" ", "").lower()


def boss_words(boss: str) -> list[str]:
    """The words that name a boss, folded for matching: "The Twin Fangs" -> ['twin', 'fangs'], "Nek’zali the
    Soulcoiler" -> ['nekzali', 'soulcoiler']."""
    return [w for w in re.findall(r"[a-z0-9]+", fold_quotes(boss.lower()).replace("'", "")) if w != "the"]


def names_boss(title: str, boss: str) -> bool:
    """Whether a title names the boss: every word of the boss's name is in it, or the name run together is words
    of it in a row ("Twinfangs" names "The Twin Fangs")."""
    words, asked = boss_words(title), boss_words(boss)
    if all(w in words for w in asked):
        return True
    joined = "".join(asked)
    return any("".join(words[i:j]) == joined for i in range(len(words)) for j in range(i + 2, len(words) + 1))


def matches_boss(typed: str, boss: str) -> bool:
    """Whether a boss's name as someone typed it, or part of it, is `boss`'s (the log tools' `boss`): every word of
    it is part of the name, accents and apostrophes optional, or the words run together are part of the name run
    together ("sszo" is Sszorak, "twinfangs" The Twin Fangs). `names_boss` asks whether a title names a boss, in
    whole words."""
    def plain(s: str) -> str:  # "Nek'zali" matches "nekzali"
        return fold(fold_quotes(s.lower())).replace("'", "")
    words, name = re.findall(r"\w+", plain(typed)), plain(boss)
    return all(w in name for w in words) or "".join(words) in re.sub(r"\W+", "", name)
