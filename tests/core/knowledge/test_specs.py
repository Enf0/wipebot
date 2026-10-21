from wipebot.core.knowledge import fetch, specs

WOWHEAD_DRUID = "https://www.wowhead.com/guide/classes/druid/balance/"


async def test_spec_guide_follows_the_topic_page_and_skips_leveling(monkeypatch):
    calls = []

    async def fake_cli(tool, *args):
        args = fetch._key_args(args)  # the command as it means, wherever `--` stands
        calls.append((tool, args))
        if args[0] == "resolve":
            return {"resolved": True, "next_command": "wowhead guide 3103"}
        if args[0] == "guides":
            return {"results": [{"id": 3098, "url": WOWHEAD_DRUID + "rotation-cooldowns-pve-dps",
                                 "last_updated": "2026-09-03T14:59:59-05:00"}]}
        if args[1] == "3103":  # the overview: the topic in its text only, and a Rotation sub-page
            return {"guide": {"id": 3103, "page_url": WOWHEAD_DRUID + "overview-pve-dps"},
                    "body": {"section_chunks": [{"title": "Overview", "content_text": "Use cooldowns on pull."}]},
                    "navigation": {"raw_markup": "[url=guide/classes/druid/balance/rotation-cooldowns-pve-dps]"
                                                 "Rotation[/url][url=guide/classes/druid/balance/dps-leveling-tips]"
                                                 "Leveling[/url]"}}
        return {"guide": {"page_url": WOWHEAD_DRUID + "rotation-cooldowns-pve-dps"},
                "body": {"section_chunks": [
                    {"title": "Leveling Rotation", "content_text": "Spam Wrath."},
                    {"title": "Best Balance Druid Single Target Rotation", "content_text": "Starfire in Eclipse."},
                    {"title": "Cooldowns", "content_text": "Celestial Alignment on cooldown."},
                    {"title": "Mythic+ Tips", "content_text": "Cooldown every pack."}]},
                "navigation": {"raw_markup": ""}}
    monkeypatch.setattr(fetch, "cli", fake_cli)
    text = await specs.spec_guide_text("balance druid", "cooldowns", "wowhead")
    assert ("wowhead", ("guide-full", WOWHEAD_DRUID + "rotation-cooldowns-pve-dps")) in calls
    assert text.startswith(f"Wowhead guide: {WOWHEAD_DRUID}rotation-cooldowns-pve-dps (last updated 2026-09-03")
    assert "## Cooldowns\nCelestial Alignment on cooldown." in text
    assert "Mythic+" not in text and "Leveling" not in text
    rotation = await specs.spec_guide_text("balance druid", "rotation", "wowhead")
    assert "## Best Balance Druid Single Target Rotation" in rotation and "Spam Wrath" not in rotation
    leveling = await specs.spec_guide_text("balance druid", "leveling rotation", "wowhead")
    assert "## Leveling Rotation\nSpam Wrath." in leveling


def test_topic_words_are_stemmed():
    assert specs.topic_words("Cooldowns and defensives") == ["coold", "defen"]
    assert specs.topic_words("mana") == ["mana"]


async def test_a_page_is_read_from_its_own_site_whatever_the_source(monkeypatch):
    # the model passes a page an earlier call listed and leaves source at its default, Method
    calls = []

    async def fake_cli(tool, *args):
        args = fetch._key_args(args)  # the command as it means, wherever `--` stands
        calls.append((tool, args))
        return {"guide": {"page_url": args[1]}, "body": {"section_chunks": []}, "navigation": {"raw_markup": ""}}
    monkeypatch.setattr(fetch, "cli", fake_cli)
    await specs.spec_guide_text("balance druid", None, "method", WOWHEAD_DRUID + "rotation-cooldowns-pve-dps")
    assert calls[0] == ("wowhead", ("guide-full", WOWHEAD_DRUID + "rotation-cooldowns-pve-dps"))


async def test_spec_cooldowns_are_read_again_after_a_day_and_an_untracked_spec_sooner(monkeypatch):
    asked = []

    async def fake_cli(tool, *args):
        args = fetch._key_args(args)  # the command as it means, wherever `--` stands
        asked.append(args[1])
        if args[1] == "evoker-augmentation":
            raise fetch.NotFound("lorrgs spec-spells: not_found")
        return {"1": {"name": "Barkskin", "spell_type": "druid"}}
    monkeypatch.setattr(fetch, "cli", fake_cli)
    monkeypatch.setattr(specs, "_spec_spells", fetch.Memo())
    now = [1000.0]
    monkeypatch.setattr(fetch.time, "monotonic", lambda: now[0])
    for _ in range(2):
        assert [s["name"] for s in await specs.spec_spells("Druid", "Guardian")] == ["Barkskin"]
        assert await specs.spec_spells("Evoker", "Augmentation") == []
    assert len(asked) == 2  # kept
    now[0] += specs.NOT_TRACKED_S + 1
    await specs.spec_spells("Druid", "Guardian")
    await specs.spec_spells("Evoker", "Augmentation")
    assert asked[2:] == ["evoker-augmentation"]  # "not tracked" is asked again soon, the cooldowns after a day
    now[0] += fetch.MEMO_S
    await specs.spec_spells("Druid", "Guardian")
    assert asked[-1] == "druid-guardian"
