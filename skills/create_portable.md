---
name: create-portable
description: Portable dashboard export pattern — packages only the dashboard, launcher, and compact JSON into a zip for offline USB transfer. Use when preparing the project for environments without internet or dev tooling.
type: skill
---

# Portable Dashboard Export

## What it does

Creates a self-contained zip (`PORTABLE ProjectYouTube.zip`) at the project root containing only the files needed to view the dashboard offline.

## Included files

| File | Why |
|------|-----|
| `runproject.command` | Double-click to launch the dashboard via local HTTP server |
| `dashboard/` | The full dashboard folder (HTML, any assets) |
| `data/youtube_playlists_compact.json` | The only data file the dashboard fetches |

## Excluded files

| File / folder | Why excluded |
|---------------|-------------|
| `scripts/` | Fetcher scripts — not needed for viewing |
| `.env/` | Credentials — never leave the dev machine |
| `.claude/` | Claude Code config — dev-only |
| `skills/`, `next steps/`, `CLAUDE.md` | Dev workflow files |
| `data/youtube_playlists.json` | Full enriched JSON — dashboard doesn't use it |
| `data/fetch_log.json` | Fetch run log |
| `data/last_run_summary.json` | Fetch summary |
| `data/enrich_run.log` | Enrichment log |
| `venv/` | Python virtual environment |

## Design decisions

- **Whitelist, not blacklist**: the command copies only known-needed files rather than copying everything and deleting extras. This prevents credentials or large files from accidentally leaking into the zip.
- **Only compact JSON**: the dashboard fetches `youtube_playlists_compact.json` via relative path `../data/`. The full `youtube_playlists.json` is never referenced by the dashboard.
- **Folder name with space**: `PORTABLE ProjectYouTube` — the space makes it visually distinct from the source project in Finder.

## How to use at the destination

1. Unzip `PORTABLE ProjectYouTube.zip`
2. Double-click `runproject.command`
3. Dashboard opens in Chrome via `localhost:8000`
