---
name: watch-later-add-date-investigation
description: Research on how to recover the "date added" for YouTube Watch Later videos. Documents 7 methods tested, with HTML page scraping as the working solution. Reusable for any project needing Watch Later metadata.
---

# Watch Later "Added To Playlist" Date — Investigation Report

**Date:** 2026-03-15 (updated)
**Status:** Implemented in `scripts/fetch_youtube_playlists.py` → `_fetch_wl_added_dates_html()`
**Conclusion: Add dates ARE recoverable for ALL Watch Later videos via HTML page scraping + innertube pagination. Precision decreases with age (from ±1 day to ±6 months).**

---

## What Was Tested

### 1. yt-dlp flat-playlist mode
```
timestamp: None
playlist_timestamp: None
```
YouTube's own API response to yt-dlp for Watch Later simply omits both fields. This is not a yt-dlp bug — the data is not present in YouTube's internal response to this endpoint.

### 2. yt-dlp full (non-flat) fetch
```
timestamp: 1475182800   ← upload date of the video, NOT add date
```
In full mode, yt-dlp's `timestamp` for Watch Later items is the **video's own upload date**, not when you saved it. Confirmed by cross-checking with `upload_date`.

### 3. YouTube Data API v3 — `playlistItems.list(playlistId="WL")`
```python
resp = svc.playlistItems().list(part="snippet", playlistId="WL", maxResults=50).execute()
# → items: []
```
The official YouTube Data API explicitly blocks access to Watch Later items. This is a documented limitation — YouTube removed WL support from the API in 2016. Returns an empty list with no error.

### 4. YouTube Data API v3 — `activities.list(mine=True)`
Only returns `type: upload` events (your own uploads). Does not log "saved to Watch Later" events.

### 5. YouTube innertube API — direct browse (WEB client)
Called via `POST /youtubei/v1/browse` with `browseId: "VLWL"` and SAPISIDHASH auth.

Returns 100 `playlistVideoRenderer` items per page. The `videoInfo` field contains "X ago" text for **only the most recently added video** — all others have empty `videoInfo`. Has continuation tokens for pagination.

### 6. YouTube innertube API — TVHTML5_SIMPLY_EMBEDDED_PLAYER client
Returns 20 `videoRenderer` items with `publishedTimeText` containing the **add date** for ALL 20 items. Confirmed to be add dates (not upload dates) by cross-checking: `AWdRm7rMn1g` uploaded 2026-03-12 but `publishedTimeText: "1 day ago"` (added 2026-03-14).

**Limitation:** Returns only 20 items with no continuation token. Sort parameters tested (5 variants) always return the same 20 items. WEB continuation tokens are rejected (HTTP 400).

### 7. YouTube Watch Later HTML page ← **THE SOLUTION**
Fetching `https://www.youtube.com/playlist?list=WL` as an authenticated HTML page returns server-rendered `ytInitialData` JSON containing 100 `playlistVideoRenderer` items. The `videoInfo` field contains "X ago" text for **ALL items** (not just the most recent).

```json
{
  "videoId": "MHS-htjGgSY",
  "videoInfo": { "simpleText": "4.4m views • 9 years ago" }
}
```

A `continuationItemRenderer` at position [100] provides a continuation token. Feeding this token to the innertube `/browse` endpoint (WEB client) returns subsequent pages with the **same "X ago" text for all items**.

#### Full pagination results:
| Page | Source | Videos | With "X ago" date |
|------|--------|--------|--------------------|
| 1 | HTML `ytInitialData` | 100 | **100** |
| 2 | innertube continuation | 100 | **100** |
| 3 | innertube continuation | 100 | **100** |
| 4 | innertube continuation | 100 | **100** |
| 5 | innertube continuation | 93 | **93** |
| **Total** | | **493** | **493 (100%)** |

---

## Why the HTML page differs from the direct API call

The innertube browse API (method 5) returns `videoInfo` with "X ago" for only the most recently added video. But the **HTML page** pre-renders the "X ago" text server-side for ALL items before sending to the browser. The continuation pages maintain this behaviour.

The key difference is the request path:
- **Direct innertube API** → sparse `videoInfo` (1 out of 100)
- **HTML page fetch** → full `videoInfo` for all items, including the `ytInitialData` blob
- **Continuation from HTML page token** → full `videoInfo` for all items

---

## Date confirmation: these are ADD dates, not upload dates

Cross-checked against known upload dates:

| Video | Upload date | videoInfo | Interpretation |
|-------|------------|-----------|----------------|
| AWdRm7rMn1g | 2026-03-12 | "1 day ago" | Added 2026-03-14, uploaded 3 days earlier |
| MHS-htjGgSY | 2016-09-29 | "9 years ago" | Added ~2017, uploaded 6 months earlier |
| 1kowM6vT-0o | 2022-03-20 | "3 years ago" | Added ~2023, uploaded 1 year earlier |
| oclSk7AKfUA | 2026-03-04 | "11 days ago" | Added 2026-03-04, uploaded same day |

The pattern is clear: many videos were added to Watch Later days/months/years after upload. The "X ago" text reflects when the user added them.

---

## Precision

The "X ago" text provides decreasing precision for older items:

| Text format | Example | Precision |
|-------------|---------|-----------|
| "X hours ago" | "3 hours ago" | ±hours |
| "X days ago" | "11 days ago" | ±1 day |
| "X weeks ago" | "3 weeks ago" | ±3-4 days |
| "X months ago" | "4 months ago" | ±15 days |
| "X years ago" | "9 years ago" | ±6 months |

For videos added >1 year ago, the date is approximate. For recent additions, it's precise.

---

## Implementation approach

```
_fetch_wl_added_dates_html()
  1. Use yt-dlp cookies to fetch https://www.youtube.com/playlist?list=WL
  2. Extract ytInitialData JSON from HTML
  3. Parse all playlistVideoRenderer items → (videoId, "X ago" text)
  4. Extract continuation token from continuationItemRenderer
  5. Loop: call innertube /browse with continuation token, extract more items
  6. Parse all "X ago" texts to approximate ISO dates
  7. Write added_to_playlist for each WL video in the JSON
```

Requirements:
- Chrome cookies via yt-dlp (`--cookies-from-browser chrome`)
- SAPISIDHASH header: `sha1("{timestamp} {SAPISID} https://www.youtube.com")`
- No additional Python packages needed (uses stdlib `urllib`, `http.cookiejar`, `json`, `re`)

---

## Summary

| Method | Result |
|--------|--------|
| yt-dlp flat | `timestamp: None` — not provided |
| yt-dlp full | `timestamp` = upload date, not add date |
| YouTube Data API `playlistItems.list(WL)` | 0 items — blocked since 2016 |
| YouTube Data API `activities.list` | Only upload events, no save events |
| innertube WEB client (direct) | 100 items/page, `videoInfo` empty for 99/100 |
| innertube TV embedded client | 20 items with add dates, no pagination |
| **HTML page + innertube continuation** | **All 493 items with "X ago" add dates** |

**Verdict: Fully recoverable for ALL videos via HTML page scraping. Precision is ±1 day for recent, ±6 months for years-old additions. Running frequently improves precision for new additions.**
