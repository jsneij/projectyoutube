# ProjectYouTube — Claude Instructions

## Project
Fetches YouTube playlist metadata via yt-dlp and stores it as JSON. A local HTML dashboard visualises the data.

## Structure
```
ProjectYouTube/
├── CLAUDE.md
├── runproject.command           ← double-click to launch dashboard
├── scripts/
│   └── fetch_youtube_playlists.py
├── dashboard/
│   └── dshb_youtube.html        ← fetches ../data/youtube_playlists_compact.json
├── data/
│   ├── youtube_playlists.json
│   ├── youtube_playlists_compact.json   ← used by dashboard
│   ├── fetch_log.json
│   └── last_run_summary.json
├── skills/
│   ├── project_structure.md
│   ├── efficient_json_fetch_pattern.md
│   └── watch_later_add_date_investigation.md
└── next steps/
```

## Running the fetch script
```bash
python3 scripts/fetch_youtube_playlists.py               # full sync + enrichment
python3 scripts/fetch_youtube_playlists.py --fast        # flat metadata, no enrichment
python3 scripts/fetch_youtube_playlists.py --structural  # titles + membership only, write-if-changed
python3 scripts/fetch_youtube_playlists.py --enrich      # same as default (force enrichment)
```

## Launching the dashboard
Double-click `runproject.command`, or:
```bash
python3 -m http.server 8000
# open http://localhost:8000/dashboard/dshb_youtube.html
```

## Slash commands
- `/refresh_youtube` — fast structural check (foreground, write only if changed)
- `/refresh_youtube_complete_enrichment` — full enrichment via nohup background
- `/create_portable` — zip dashboard + compact data for offline USB transfer

## Path conventions
- Script paths use `Path(__file__).parent.parent / "data"` — safe to run from any directory
- Credentials live in `.env/` (hidden folder) — referenced via `Path(__file__).parent.parent / ".env"`
- Dashboard fetches data via relative path `../data/` — never absolute
