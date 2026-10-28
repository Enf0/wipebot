"""Cutting a release (scripts/release.py): the changelog's Unreleased becomes the version's section, and which version
the changelog calls for."""
import datetime

from scripts import release

CHANGELOG = """# Changelog

Intro.

## [Unreleased]

### Added

- `Agent` **New.** Something to ask.

### Fixed

- `Agent` A fix.

## [0.2.0] - 2026-10-22

### Fixed

- `Agent` An older fix.
"""


def test_unreleased_becomes_the_version_dated_under_a_new_empty_one():
    cut = release.cut(CHANGELOG, "0.3.0", datetime.date(2026, 10, 23))
    assert "## [Unreleased]\n\n## [0.3.0] - 2026-10-23\n\n### Added\n\n- `Agent` **New.**" in cut
    assert release.unreleased(cut) == "" and cut.count("## [0.2.0]") == 1
    assert release.unreleased(CHANGELOG) == ("### Added\n\n- `Agent` **New.** Something to ask.\n\n### Fixed\n\n"
                                             "- `Agent` A fix.")


def test_anything_added_or_changed_calls_for_a_minor_version_and_fixes_alone_a_patch():
    assert release.suggest("0.2.0", release.unreleased(CHANGELOG)) == "0.3.0"
    assert release.suggest("0.2.0", "### Fixed\n\n- `Agent` A fix.") == "0.2.1"
    assert release.suggest("1.4.2", "### Changed\n\n- `Discord` Different.") == "1.5.0"


def test_only_the_projects_version_is_bumped():
    pyproject = '[project]\nname = "wipebot"\nversion = "0.2.0"\n\n[tool.x]\nversion = "9.9.9"\n'
    assert release.bump(pyproject, "0.3.0") == pyproject.replace('"0.2.0"', '"0.3.0"')


def test_a_released_versions_notes_are_its_section():
    cut = release.cut(CHANGELOG, "0.3.0", datetime.date(2026, 10, 23))
    assert release.section(cut, "0.3.0") == release.unreleased(CHANGELOG)
    assert release.section(cut, "0.2.0") == "### Fixed\n\n- `Agent` An older fix."


def test_a_line_about_a_picture_needs_the_picture_under_it():
    notes = ("### Added\n\n- `Discord` **The night, drawn.** A new picture\n  of the night.\n"
             "- `Discord` **The map, pictured.** A map.\n  ![The map](https://raw.githubusercontent.com/x/map.webp)\n"
             "- `Agent` **Faster.** No drawing here, no pic.\n- `Agent` A plain fix with a chart in it.")
    assert release.unpictured(notes) == ["The night, drawn.", "A plain fix with a chart in it."]
