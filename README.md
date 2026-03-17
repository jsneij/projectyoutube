# ProjectYouTube

What happens when you try to learn Claude Code, GitHub, and VS Code all at the same time? You end up building something way more ambitious than you planned.

This started as "let me just see my YouTube playlists in one place" and turned into a full automated pipeline with a live dashboard, GitHub Actions, API integrations, and way too many commits that say "fix" followed by another "fix."

## What it actually does

- **Fetches** metadata from all your YouTube playlists using yt-dlp (titles, channels, durations, view counts, thumbnails — the works)
- **Enriches** videos with upload dates, descriptions, likes, tags and more
- **Displays** everything in a searchable, sortable dashboard that loads instantly
- **Syncs daily** via GitHub Actions while you sleep

## Dashboard

Live here: https://jsneij.github.io/projectyoutube/dashboard/dshb_youtube.html

Search across all playlists, sort by anything, filter by playlist. Stats at the top tell you exactly how many hours of video you've hoarded (it's more than you think).

## The automation rabbit hole

| Workflow                     | What it does |
|------------------------------|---|
| **Fetch&nbsp;YouTube&nbsp;Data**       | Runs daily at 4am — because why wake up to do it yourself |
| **Enrich&nbsp;Missing&nbsp;(max&nbsp;10)** | One-click enrichment for a few new videos |
| **Enrich&nbsp;Missing**             | For when you went on a YouTube binge and added 30 videos |
| **Complete&nbsp;Enrichment**         | The nuclear option — re-enriches everything. Requires typing a confirmation word so you don't hit it by accident |

## Built with

- **Claude Code** — the real MVP, wrote most of this with me
- **Python 3.12** + **yt-dlp** for YouTube data extraction
- **YouTube Data API v3** for batch upload-date lookups
- **GitHub Actions** for the automation magic
- **GitHub Pages** for hosting
- Vanilla HTML/CSS/JS — no frameworks, no build step, no npm install that downloads half the internet
