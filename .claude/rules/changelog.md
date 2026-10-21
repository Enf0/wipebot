---
paths:
  - "CHANGELOG.md"
---

# Writing CHANGELOG.md

It is public.

- [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)'s format: a change goes under `## [Unreleased]` at the top,
  in `### Added`, `### Changed`, `### Removed` or `### Fixed` (in that order, only those). A release renames it
  `## [0.1.0] - 2026-10-25` and starts a new Unreleased above it; the 0.0.x versions are the days before the first
  public release.
- Each item starts with where it shows, in backticks: `Agent` (the analysis itself, so both apps), `MCP` (Wipebot in
  Claude and ChatGPT), `Discord` (Wipebot in Discord); two when it shows in two (`` `Agent` `Discord` ``). A notable item gets a
  **bold lead.** sentence.
- Say what a raider can now ask, see or do, in their words, in a sentence or two; a fix only when raiders could see
  the bug. Leave out what they can't notice (refactors, tests, evals, prompt tweaks, infrastructure) and code names
  (tools, files, functions).
- Nothing sensitive: no secrets, hostnames or internal setup; no BattleTags, account ids or a real raider's results
  or mistakes (character names only as neutral examples); no guild matters, outside sources or inspirations; and
  nothing about how sign-in or access checks work beyond what a user needs to sign in.
