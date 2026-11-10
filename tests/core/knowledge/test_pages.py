import pytest

from wipebot.core.knowledge import KnowledgeError
from wipebot.core.knowledge.pages import PART_CHARS, _command, _parts


@pytest.mark.parametrize("url, command", [
    ("https://www.wowhead.com/news/some-tuning-382704", ("wowhead", "news-post")),
    ("https://www.wowhead.com/blue-tracker/topic/us/hotfixes-2382684", ("wowhead", "blue-topic")),
    ("https://www.wowhead.com/guide/midnight/raids/some-boss-strategy", ("wowhead", "guide-full")),
    ("www.method.gg/guides/the-venomous-abyss/the-twin-fangs", ("method", "guide-full")),
    ("https://www.icy-veins.com/wow/twin-fangs-raid-guide", ("icy-veins", "guide")),
])
def test_a_page_is_read_by_its_sites_reader(url, command):
    assert _command(url)[:2] == command


def test_a_wiki_page_is_read_by_its_title():
    assert _command("https://warcraft.wiki.gg/wiki/Kith%27ix_raid") == ("warcraft-wiki", "article-full", "--",
                                                                         "Kith'ix raid")
    with pytest.raises(KnowledgeError):
        _command("https://notwowhead.com/news/a-post")  # only the site itself and its subdomains


@pytest.mark.parametrize("url", [
    "https://example.com\\.wowhead.com/news/a-post",  # a host a browser reads as example.com
    "https://example.com/news/@wowhead.com",
    "https://wowhead.com.example.com/news/a-post",
    "http://www.wowhead.com/news/a-post",  # not over https
    "ftp://www.wowhead.com/news/a-post",
    "https://[www.wowhead.com/news/a-post",  # no address at all
])
def test_an_address_that_only_looks_like_a_sites_is_not_read(url):
    with pytest.raises(KnowledgeError):
        _command(url)


def test_a_login_or_a_port_in_the_address_does_not_hide_the_site():
    assert _command("https://user@www.wowhead.com:443/news/a-post")[:2] == ("wowhead", "news-post")


def test_a_site_no_reader_has_says_which_it_can_read():
    with pytest.raises(KnowledgeError, match="Method, Icy Veins and Warcraft Wiki"):
        _command("https://example.com/twin-fangs")


def test_a_long_page_is_cut_into_parts_at_line_breaks_or_sentences():
    lines = "\n".join(f"Line {i} of the guide." for i in range(1000))
    parts = _parts(lines)
    assert len(parts) > 1 and all(len(p) <= PART_CHARS for p in parts)
    assert "\n".join(parts) == lines  # nothing lost or doubled
    prose = " ".join(f"Sentence {i} of one long paragraph." for i in range(500))
    assert all(p.endswith(".") for p in _parts(prose))
