import time

from tests.support import FIXTURES
from wipebot.core.knowledge import news
from wipebot.core.knowledge.news import HotfixParser, mentions


def test_hotfix_post_becomes_dated_changes_under_their_headings():
    parser = HotfixParser()
    parser.feed((FIXTURES / "knowledge" / "blizzard-hotfix-post.html").read_text(encoding="utf-8"))
    assert parser.rows == [
        ("October 6, 2026", "The Venomous Abyss › Sszorak", "Tempest damage reduced by 20%."),
        ("October 1, 2026", "Monk › Brewmaster", "All damage increased by 3%."),
    ]


def test_a_hotfix_mentions_a_name_by_its_whole_words():
    # "Twin Fangs" matched a dungeon boss's hotfixes, which hid the news of the raid boss's nerf (answer #484).
    assert not mentions("Altar of Fangs › Twinfang Harrower: Health reduced by 6%.", "Twin Fangs")
    assert mentions("The Venomous Abyss › The Twin Fangs: Health reduced by 5%.", "The Twin Fangs")
    assert mentions("Monk › Brewmaster: All damage increased by 3%.", "Brewmaster")
    assert mentions("Sszorak's rage gained reduced.", "Sszorak")
    assert mentions("Warlocks: Demonology damage increased.", "warlock")


async def test_the_news_post_read_is_the_one_about_tuning(monkeypatch):
    # The newest post was a solo-kill video, so the nerf's post was a bare title (answer #485).
    posts = [{"posted_at": "2026-09-25", "title": "Rextroy Solos Twin Fangs", "url": "solo"},
             {"posted_at": "2026-09-01", "title": "Mythic Twin Fangs Changed - Raid Tuning on Weekly Reset",
              "url": "tuning"}]

    async def news_posts(about, since):
        return posts

    async def news_post(url):
        return f"text of {url}"
    monkeypatch.setattr(news, "news_posts", news_posts)
    monkeypatch.setattr(news, "news_post", news_post)
    text = await news._news_text("The Twin Fangs", 180)
    assert 'The post "Mythic Twin Fangs Changed - Raid Tuning on Weekly Reset" says: text of tuning' in text
    posts.pop()
    text = await news._news_text("x", 180, time.time() - 9e6)
    assert 'The post "Rextroy Solos Twin Fangs" says: text of solo' in text
