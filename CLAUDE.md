# ProjectYouTube — Claude Instructions

## Project
Fetches YouTube playlist metadata via yt-dlp and stores it as JSON. An HTML dashboard hosted on GitHub Pages visualises the data. GitHub Actions automate daily syncing and on-demand enrichment.

## Structure
```
ProjectYouTube/
├── CLAUDE.md
├── README.md
├── index.html                       ← GitHub Pages redirect to dashboard
├── .gitignore
├── assets/
│   └── favicon.svg                  ← dark-mode YouTube-style favicon (SVG)
├── scripts/
│   └── fetch_youtube_playlists.py
├── dashboard/
│   └── dshb_youtube.html            ← fetches ../data/youtube_playlists_compact.json
├── data/
│   ├── youtube_playlists.json       ← full dataset (formatted)
│   ├── youtube_playlists_compact.json  ← minified, used by dashboard
│   ├── fetch_log.json
│   └── last_run_summary.json
├── TRANSCRIPTS/                     ← flat folder with .txt transcripts (one per video)
│   └── {Channel} - {video_id} {Title}.txt
├── skills/
│   ├── project_structure.md
│   ├── efficient_json_fetch_pattern.md
│   └── watch_later_add_date_investigation.md
└── .github/workflows/
    ├── fetch_youtube_data.yml       ← daily at 04:00 CET
    ├── enrich_missing_10.yml        ← manual, max 10 videos
    ├── enrich_missing.yml           ← manual, requires ENRICH-MISSING
    └── complete_enrichment.yml      ← manual, requires COMPLETE-ENRICH
```

## Running the fetch script
```bash
python3 scripts/fetch_youtube_playlists.py               # full sync + enrichment
python3 scripts/fetch_youtube_playlists.py --fast         # flat metadata, no enrichment
python3 scripts/fetch_youtube_playlists.py --structural   # titles + membership only, write-if-changed
python3 scripts/fetch_youtube_playlists.py --enrich       # same as default (force enrichment)
python3 scripts/fetch_youtube_playlists.py --enrich-only  # enrich unenriched videos only (no sync)
python3 scripts/fetch_youtube_playlists.py --transcript-ids ID1,ID2  # fetch transcripts only
python3 scripts/fetch_youtube_playlists.py --enrich-only --transcript-ids ID1,ID2  # enrich + transcripts
python3 scripts/fetch_youtube_playlists.py --sync-transcripts  # reconcile transcript fields with TRANSCRIPTS/ folder
```

## Environment variables
- `YTDLP_PATH` — path to yt-dlp binary (default: `/opt/homebrew/bin/yt-dlp`)
- `YTDLP_COOKIES_FILE` — path to Netscape cookies.txt (CI only; locally uses `--cookies-from-browser`)
- `YT_API_KEY` — YouTube Data API v3 key for batch upload_date lookups (no pip packages needed)

## GitHub Actions
Four workflows in `.github/workflows/`:
- **Fetch YouTube Data** (`fetch_youtube_data.yml`) — daily 04:00 CET, runs `--fast`; auto-enriches if ≤10 unenriched; syncs transcript fields
- **Enrich Missing (max 10)** (`enrich_missing_10.yml`) — manual, aborts if >10 items; optional `transcript_ids` input
- **Enrich Missing** (`enrich_missing.yml`) — manual, requires `ENRICH-MISSING`; optional `transcript_ids` input
- **Complete Enrichment** (`complete_enrichment.yml`) — manual, requires `COMPLETE-ENRICH`; optional `transcript_ids` input

GitHub Secrets: `YT_COOKIES` (Netscape cookies.txt), `YT_API_KEY`

## GitHub Pages
Dashboard live at: https://jsneij.github.io/projectyoutube/dashboard/dshb_youtube.html
Root `index.html` redirects to dashboard. Deploys automatically on push via GitHub Pages.

## Path conventions
- Script paths use `Path(__file__).parent.parent / "data"` — safe to run from any directory
- Credentials live in `.env/` (hidden folder, gitignored)
- Dashboard fetches data via relative path `../data/` — never absolute

## Key technical notes
- Enrichment in CI requires `--skip-download --ignore-no-formats-error` flags (yt-dlp 2025.11+ needs JS runtime for format resolution, which CI lacks)
- Upload dates during fast fetch come from YouTube Data API v3 via `YT_API_KEY` (not from yt-dlp flat-playlist, which doesn't return them)
- Cookies must be from Brave browser (clean, YouTube-only) — never full browser cookie exports
- Transcripts are fetched via yt-dlp subtitles (English, auto-generated fallback), converted from SRT to plain text, stored in `TRANSCRIPTS/{Channel} - {video_id} {Title}.txt`
- Dashboard transcript selections are stored in localStorage and copied to clipboard for pasting into GitHub Actions `transcript_ids` input
