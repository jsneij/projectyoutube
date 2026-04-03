#!/usr/bin/env python3
"""
YouTube Playlist Fetcher — Incremental
======================================
Modes
-----
  python3 fetch_youtube_playlists.py             # full sync + enrichment (default)
  python3 fetch_youtube_playlists.py --fast      # fast sync only (flat metadata, no enrichment)
  python3 fetch_youtube_playlists.py --structural # titles + membership only, write-if-changed
  python3 fetch_youtube_playlists.py --enrich    # same as default (force enrichment)
  python3 fetch_youtube_playlists.py --transcript-ids ID1,ID2  # fetch transcripts only
  python3 fetch_youtube_playlists.py --enrich-only --transcript-ids ID1,ID2  # enrich + transcripts

Fast sync (default)
-------------------
Uses yt-dlp --flat-playlist: ONE call per playlist, never times out.
Gets: title · url · video_id · duration · view_count · channel · thumbnail

Incremental logic (every run)
------------------------------
  • New playlist     → full flat fetch
  • Deleted playlist → marked deleted (kept for historical analysis)
  • Renamed playlist → title updated, no re-fetch
  • New videos       → flat-fetch only the new ones
  • Deleted videos   → removed from list
  • Unchanged        → skipped entirely (0 extra network calls)

Structural sync (--structural)
-------------------------------
Checks only structural changes; does NOT update view_count, duration, thumbnail,
or channel info for existing videos.
  • Playlist title changed  → updated
  • Per-video title changed → updated
  • Videos deleted/private  → removed
  • New videos added        → added (flat data, enriched=False)
  • No changes found        → JSON not rewritten (mtime preserved)

Enrichment mode (--enrich)
--------------------------
Adds per-video: upload_date · description · like_count · comment_count · tags · categories
Fetches 10 videos at a time. Safe to interrupt — already-enriched videos are skipped.

Output
------
  data/youtube_playlists.json         formatted, full dataset
  data/youtube_playlists_compact.json minified, for dashboards
  data/fetch_log.json                 lightweight change-tracking log
  data/last_run_summary.json          what changed in the last run
"""

import json
import subprocess
import sys
import time
import os
import re
import hashlib
import http.cookiejar
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ── Config ────────────────────────────────────────────────────────────────────
YTDLP              = os.environ.get("YTDLP_PATH", "/opt/homebrew/bin/yt-dlp")
BROWSER            = "chrome"
COOKIES_FILE       = os.environ.get("YTDLP_COOKIES_FILE", "")  # path to Netscape cookies.txt
YT_API_KEY         = os.environ.get("YT_API_KEY", "")        # YouTube Data API v3 key
OUTPUT_DIR         = Path(__file__).parent.parent / "data"
TRANSCRIPTS_DIR    = Path(__file__).parent.parent / "TRANSCRIPTS"
OUTPUT_FILE        = OUTPUT_DIR / "youtube_playlists.json"
OUTPUT_COMPACT     = OUTPUT_DIR / "youtube_playlists_compact.json"
FETCH_LOG_FILE     = OUTPUT_DIR / "fetch_log.json"
SUMMARY_FILE       = OUTPUT_DIR / "last_run_summary.json"
PLAYLISTS_FEED     = "https://www.youtube.com/feed/playlists"
DELAY_PLAYLIST     = 1.0   # seconds between playlist flat-fetches
DELAY_ENRICH_BATCH = 2.0   # seconds between enrichment batches
ENRICH_BATCH_SIZE  = 10    # videos per enrichment batch
ENRICH_TIMEOUT     = 90    # seconds per enrichment batch
FLAT_TIMEOUT       = 90    # seconds for any flat fetch
ENV_DIR            = Path(__file__).parent.parent / ".env"
YT_CLIENT_SECRETS  = ENV_DIR / "yt_client_secrets.json"
YT_TOKEN_FILE      = ENV_DIR / "yt_token.json"
YT_API_SCOPES      = ["https://www.googleapis.com/auth/youtube.readonly"]
# ─────────────────────────────────────────────────────────────────────────────

# ── Colors ────────────────────────────────────────────────────────────────────
YELLOW = "\033[33m"
GREEN  = "\033[32m"
RESET  = "\033[0m"
# ─────────────────────────────────────────────────────────────────────────────


# ── yt-dlp runner ─────────────────────────────────────────────────────────────

def _run(args: list[str], timeout: int = 90) -> list[dict]:
    if COOKIES_FILE:
        cookie_args = ["--cookies", COOKIES_FILE]
    else:
        cookie_args = ["--cookies-from-browser", BROWSER]
    cmd = [YTDLP] + cookie_args + ["--no-warnings", "--ignore-errors"] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return [json.loads(l) for l in r.stdout.splitlines()
                if l.strip().startswith("{")]
    except subprocess.TimeoutExpired:
        print(f"  [warn] yt-dlp timed out ({timeout}s)", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  [warn] yt-dlp error: {e}", file=sys.stderr)
        return []


# ── Fetch helpers ─────────────────────────────────────────────────────────────

def fetch_playlist_stubs() -> list[dict]:
    entries = _run(["--flat-playlist", "--dump-json", PLAYLISTS_FEED],
                   timeout=FLAT_TIMEOUT)
    stubs = []
    for e in entries:
        pid   = e.get("id") or e.get("playlist_id") or ""
        title = e.get("title") or e.get("playlist_title") or "Untitled"
        url   = (e.get("url") or e.get("webpage_url") or
                 (f"https://www.youtube.com/playlist?list={pid}" if pid else ""))
        stubs.append({"playlist_id": pid, "title": title, "url": url})
    return stubs


def flat_fetch_videos(playlist_url: str) -> list[dict]:
    """One yt-dlp call. Returns raw flat entries (one per video, in order)."""
    entries = _run(["--flat-playlist", "--dump-json", playlist_url],
                   timeout=FLAT_TIMEOUT)
    UNAVAILABLE = {"[Deleted video]", "[Private video]"}
    return [e for e in entries
            if e.get("_type") not in ("playlist", "multi_video")
            and e.get("entries") is None
            and (e.get("id") or e.get("video_id"))
            and e.get("title") not in UNAVAILABLE]


def enrich_videos_by_positions(playlist_url: str,
                                positions: list[int]) -> dict[str, dict]:
    """Full metadata for specific video positions. Batched to avoid throttle."""
    result: dict[str, dict] = {}
    batches = [positions[i:i + ENRICH_BATCH_SIZE]
               for i in range(0, len(positions), ENRICH_BATCH_SIZE)]
    for bi, batch in enumerate(batches, 1):
        entries = _run(
            ["--dump-json", "--skip-download", "--ignore-no-formats-error",
             "--playlist-items", ",".join(str(p) for p in batch),
             playlist_url],
            timeout=ENRICH_TIMEOUT,
        )
        for e in entries:
            vid = e.get("id") or e.get("video_id") or ""
            if vid and e.get("_type") not in ("playlist", "multi_video"):
                result[vid] = e
        if bi < len(batches):
            time.sleep(DELAY_ENRICH_BATCH)
    return result


def enrich_videos_by_url(video_ids: list[str]) -> dict[str, dict]:
    """Fallback: fetch full metadata for individual videos by direct URL."""
    result: dict[str, dict] = {}
    for vid_id in video_ids:
        url = f"https://www.youtube.com/watch?v={vid_id}"
        entries = _run(["-j", "--skip-download", "--ignore-no-formats-error", url], timeout=ENRICH_TIMEOUT)
        for e in entries:
            eid = e.get("id") or e.get("video_id") or ""
            if eid:
                result[eid] = e
        time.sleep(DELAY_ENRICH_BATCH)
    return result


# ── YouTube Data API v3 (added_to_playlist dates) ─────────────────────────────

def _get_youtube_service():
    """Return authenticated YouTube Data API v3 service, or None if not set up.

    Setup (one-time):
      1. Google Cloud Console → create project → enable YouTube Data API v3
      2. Create OAuth 2.0 credentials (Desktop app) → download JSON
      3. Save it as:  .env/yt_client_secrets.json
      4. Run the script once — a browser window will open for auth, then
         .env/yt_token.json is saved for all future runs.
    """
    if not YT_CLIENT_SECRETS.exists():
        return None
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        print("  [yt-api] Run: pip install google-api-python-client google-auth-oauthlib",
              file=sys.stderr)
        return None

    creds = None
    if YT_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(YT_TOKEN_FILE), YT_API_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(YT_CLIENT_SECRETS), YT_API_SCOPES)
            creds = flow.run_local_server(port=8080)
        with open(YT_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def _fetch_playlist_added_dates(svc, playlist_id: str) -> dict[str, str | None]:
    """Fetch snippet.publishedAt for every item in a playlist (= date added).
    Returns {video_id: "YYYY-MM-DD"}.  Returns {} on error."""
    result: dict[str, str | None] = {}
    page_token = None
    try:
        while True:
            kwargs: dict = {"part": "snippet", "playlistId": playlist_id,
                            "maxResults": 50}
            if page_token:
                kwargs["pageToken"] = page_token
            resp = svc.playlistItems().list(**kwargs).execute()
            for item in resp.get("items", []):
                vid = item["snippet"]["resourceId"].get("videoId", "")
                pub = item["snippet"].get("publishedAt", "")
                if vid:
                    result[vid] = pub[:10] if pub else None
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except Exception as e:
        print(f"  [yt-api] {playlist_id}: {e}", file=sys.stderr)
    return result


def _parse_ago_to_date(text: str) -> str | None:
    """Parse 'X time_unit ago' text to approximate ISO date (YYYY-MM-DD).
    Returns None if not parseable."""
    m = re.search(r'(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago', text, re.I)
    if not m:
        m = re.search(r'Streamed\s+(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago', text, re.I)
    if not m:
        return None
    num = int(m.group(1))
    unit = m.group(2).lower()
    now = datetime.now(timezone.utc)
    if unit == 'second':   dt = now - timedelta(seconds=num)
    elif unit == 'minute': dt = now - timedelta(minutes=num)
    elif unit == 'hour':   dt = now - timedelta(hours=num)
    elif unit == 'day':    dt = now - timedelta(days=num)
    elif unit == 'week':   dt = now - timedelta(weeks=num)
    elif unit == 'month':  dt = now - timedelta(days=num * 30)
    elif unit == 'year':   dt = now - timedelta(days=num * 365)
    else:
        return None
    return dt.strftime('%Y-%m-%d')


def _fetch_wl_added_dates_innertube() -> dict[str, str]:
    """Fetch approximate 'added to playlist' dates for all Watch Later videos
    via HTML page scraping + innertube continuation.

    Returns {video_id: "YYYY-MM-DD"}.
    Requires Chrome cookies (via yt-dlp).
    """
    cookie_file = COOKIES_FILE or "/tmp/yt_wl_cookies.txt"
    result: dict[str, str] = {}

    # Step 1: Export cookies via yt-dlp (skip if using a pre-supplied cookies file)
    if not COOKIES_FILE:
        try:
            subprocess.run(
                [YTDLP, "--cookies-from-browser", BROWSER, "--cookies", cookie_file,
                 "--skip-download", "--flat-playlist", "--playlist-items", "1",
                 "https://www.youtube.com/playlist?list=WL"],
                capture_output=True, text=True, timeout=60,
            )
        except Exception as e:
            print(f"  [wl-dates] cookie export failed: {e}", file=sys.stderr)
            return result

    try:
        cookie_jar = http.cookiejar.MozillaCookieJar(cookie_file)
        cookie_jar.load()
    except Exception as e:
        print(f"  [wl-dates] cookie load failed: {e}", file=sys.stderr)
        return result

    cookie_dict = {c.name: c.value for c in cookie_jar if 'youtube' in c.domain}
    sapisid = cookie_dict.get('SAPISID', '')
    if not sapisid:
        print("  [wl-dates] SAPISID cookie not found — skipping", file=sys.stderr)
        return result

    cookie_header = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())

    def make_auth():
        ts = str(int(time.time()))
        h = hashlib.sha1(f"{ts} {sapisid} https://www.youtube.com".encode()).hexdigest()
        return f"SAPISIDHASH {ts}_{h}"

    # Step 2: Fetch Watch Later HTML page
    try:
        req = urllib.request.Request(
            "https://www.youtube.com/playlist?list=WL",
            headers={
                "Cookie": cookie_header,
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/131.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        html = urllib.request.urlopen(req).read().decode('utf-8')
    except Exception as e:
        print(f"  [wl-dates] HTML fetch failed: {e}", file=sys.stderr)
        return result

    # Step 3: Extract ytInitialData
    m = re.search(r'var ytInitialData\s*=\s*({.+?});\s*</script>', html)
    if not m:
        m = re.search(r'window\["ytInitialData"\]\s*=\s*({.+?});\s*</script>', html)
    if not m:
        print("  [wl-dates] ytInitialData not found in HTML", file=sys.stderr)
        return result

    try:
        yt_data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        print(f"  [wl-dates] JSON parse error: {e}", file=sys.stderr)
        return result

    # Step 4: Extract videos and continuation tokens from all pages
    def extract_videos_and_cont(obj):
        """Walk JSON tree, return ([(videoId, videoInfo_text), ...], continuation_token)."""
        videos = []
        cont = None

        def walk(o):
            nonlocal cont
            if isinstance(o, dict):
                if 'playlistVideoRenderer' in o:
                    pvr = o['playlistVideoRenderer']
                    vid = pvr.get('videoId', '')
                    vi = pvr.get('videoInfo', {})
                    vi_text = ''
                    if isinstance(vi, dict):
                        vi_text = vi.get('simpleText', '')
                        if not vi_text:
                            runs = vi.get('runs', [])
                            vi_text = ''.join(r.get('text', '') for r in runs)
                    if vid:
                        videos.append((vid, vi_text))
                if 'continuationCommand' in o:
                    token = o['continuationCommand'].get('token', '')
                    if token:
                        cont = token
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for item in o:
                    walk(item)

        walk(obj)
        return videos, cont

    page_videos, cont_token = extract_videos_and_cont(yt_data)
    all_videos = list(page_videos)
    page_num = 1

    # Step 5: Follow continuation tokens
    while cont_token:
        page_num += 1
        try:
            payload = json.dumps({
                "context": {"client": {"clientName": "WEB", "clientVersion": "2.20240101"}},
                "continuation": cont_token,
            }).encode()
            req = urllib.request.Request(
                "https://www.youtube.com/youtubei/v1/browse?prettyPrint=false",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": make_auth(),
                    "Cookie": cookie_header,
                    "Origin": "https://www.youtube.com",
                },
            )
            resp_data = json.loads(urllib.request.urlopen(req).read())
            page_videos, cont_token = extract_videos_and_cont(resp_data)
            all_videos.extend(page_videos)
        except Exception as e:
            print(f"  [wl-dates] page {page_num} error: {e}", file=sys.stderr)
            break

    # Step 6: Parse "X ago" dates
    ago_re = re.compile(r'\d+\s+(second|minute|hour|day|week|month|year)s?\s+ago', re.I)
    parsed = 0
    for vid, vi_text in all_videos:
        if ago_re.search(vi_text) or 'Streamed' in vi_text:
            date = _parse_ago_to_date(vi_text)
            if date:
                result[vid] = date
                parsed += 1

    print(f"  [wl-dates] {len(all_videos)} videos fetched across {page_num} pages, "
          f"{parsed} dates parsed")

    # Cleanup
    try:
        os.unlink(cookie_file)
    except OSError:
        pass

    return result


def _enrich_via_api(svc, video_ids: list[str]) -> dict[str, dict]:
    """Fetch video metadata via YouTube Data API v3 for videos yt-dlp can't access
    (e.g. members-only). Returns {video_id: enrichment_dict}."""
    result = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        try:
            resp = svc.videos().list(
                part="snippet,statistics,contentDetails",
                id=",".join(batch)
            ).execute()
        except Exception as e:
            print(f"  [yt-api] videos.list error: {e}", file=sys.stderr)
            continue
        for item in resp.get("items", []):
            vid = item["id"]
            snip = item.get("snippet", {})
            stats = item.get("statistics", {})
            pub = snip.get("publishedAt", "")          # "2026-03-13T..."
            raw = pub[:10].replace("-", "") if pub else None  # "20260313"
            iso = pub[:10] if pub else None             # "2026-03-13"
            result[vid] = {
                "upload_date":         iso,
                "upload_date_raw":     raw,
                "description":         snip.get("description"),
                "like_count":          int(stats["likeCount"]) if "likeCount" in stats else None,
                "comment_count":       int(stats["commentCount"]) if "commentCount" in stats else None,
                "tags":                snip.get("tags"),
                "categories":          [snip["categoryId"]] if snip.get("categoryId") else None,
                "age_limit":           None,
                "availability":        None,
                "live_status":         None,
                "was_live":            None,
                "subtitles_available": None,
                "language":            snip.get("defaultAudioLanguage") or snip.get("defaultLanguage"),
            }
    return result


def _fetch_playlist_added_dates_api_key(playlist_id: str) -> dict[str, str]:
    """Fetch added_to_playlist dates using simple YT_API_KEY (no OAuth needed).
    Uses playlistItems.list — works for public/unlisted playlists only.
    Returns {video_id: "YYYY-MM-DD"}.  Returns {} on error (e.g. private)."""
    result: dict[str, str] = {}
    page_token = ""
    while True:
        params: dict = {
            "part": "snippet",
            "playlistId": playlist_id,
            "maxResults": 50,
            "fields": "items(snippet/resourceId/videoId,snippet/publishedAt),nextPageToken",
            "key": YT_API_KEY,
        }
        if page_token:
            params["pageToken"] = page_token
        url = f"https://www.googleapis.com/youtube/v3/playlistItems?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                body = json.loads(resp.read())
        except Exception as e:
            # 404 = private playlist, don't spam stderr — handled by fallback
            if "404" not in str(e):
                print(f"  [yt-api] playlistItems error for {playlist_id}: {e}",
                      file=sys.stderr)
            break
        for item in body.get("items", []):
            vid = item.get("snippet", {}).get("resourceId", {}).get("videoId", "")
            pub = item.get("snippet", {}).get("publishedAt", "")
            if vid and pub:
                result[vid] = pub[:10]
        page_token = body.get("nextPageToken", "")
        if not page_token:
            break
    return result


def _fetch_added_dates_ytdlp(playlist_url: str) -> dict[str, str]:
    """Fetch added_to_playlist dates via yt-dlp full extraction (uses cookies).
    Works for private playlists.  Returns {video_id: "YYYY-MM-DD"}."""
    entries = _run(
        ["--dump-json", "--skip-download", "--ignore-no-formats-error",
         playlist_url],
        timeout=FLAT_TIMEOUT * 3,
    )
    result: dict[str, str] = {}
    for e in entries:
        vid = e.get("id") or e.get("video_id") or ""
        ts = e.get("timestamp")
        if vid and ts is not None:
            try:
                d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                result[vid] = d
            except (ValueError, OSError):
                pass
    return result


def run_added_dates_pass(data: dict) -> bool:
    """Backfill added_to_playlist for any video that is missing it.

    Tries in order:
      1. YT_API_KEY (playlistItems.list — public/unlisted playlists only)
      2. yt-dlp full extraction (uses cookies — works for private playlists)
      3. OAuth via google-api-python-client (local only)
    Watch Later uses innertube HTML scraping (API blocks WL).
    Modifies data in-place. Returns True if any dates were written.
    """
    playlists_needing = [
        p for p in data.get("playlists", [])
        if not p.get("deleted")
        and any(not v.get("added_to_playlist") for v in p.get("videos", []))
    ]
    if not playlists_needing:
        return False

    total_missing = sum(
        sum(1 for v in p["videos"] if not v.get("added_to_playlist"))
        for p in playlists_needing
    )
    print(f"\n[added dates] {total_missing} videos missing added_to_playlist "
          f"across {len(playlists_needing)} playlists...")

    any_written = False

    # ── Non-WL playlists ──────────────────────────────────────────────────
    non_wl = [p for p in playlists_needing
              if p["playlist_id"] != "WL" and not p["playlist_id"].startswith("LL")]

    # Pass 1: API key (fast, public/unlisted only)
    if non_wl and YT_API_KEY:
        for playlist in non_wl:
            dates = _fetch_playlist_added_dates_api_key(playlist["playlist_id"])
            if not dates:
                continue
            count = 0
            for v in playlist["videos"]:
                if not v.get("added_to_playlist"):
                    d = dates.get(v["video_id"])
                    if d:
                        v["added_to_playlist"] = d
                        count += 1
            if count:
                print(f"  {GREEN}{playlist['title']!r}{RESET}: {count} dates (api)")
                any_written = True
        non_wl = [p for p in non_wl
                   if any(not v.get("added_to_playlist") for v in p["videos"])]

    # Pass 2: yt-dlp full extraction (private playlists, uses cookies)
    if non_wl:
        for playlist in non_wl:
            url = playlist.get("url", "")
            if not url:
                continue
            missing_count = sum(1 for v in playlist["videos"]
                                if not v.get("added_to_playlist"))
            print(f"  [ytdlp] {playlist['title']!r}: fetching {missing_count} "
                  f"added dates via yt-dlp...")
            dates = _fetch_added_dates_ytdlp(url)
            if not dates:
                continue
            count = 0
            for v in playlist["videos"]:
                if not v.get("added_to_playlist"):
                    d = dates.get(v["video_id"])
                    if d:
                        v["added_to_playlist"] = d
                        count += 1
            if count:
                print(f"  {GREEN}{playlist['title']!r}{RESET}: {count} dates (ytdlp)")
                any_written = True
        non_wl = [p for p in non_wl
                   if any(not v.get("added_to_playlist") for v in p["videos"])]

    # Pass 3: OAuth fallback for any still-unresolved
    if non_wl:
        svc = _get_youtube_service()
        if svc is not None:
            for playlist in non_wl:
                dates = _fetch_playlist_added_dates(svc, playlist["playlist_id"])
                if not dates:
                    continue
                count = 0
                for v in playlist["videos"]:
                    if not v.get("added_to_playlist"):
                        d = dates.get(v["video_id"])
                        if d:
                            v["added_to_playlist"] = d
                            count += 1
                if count:
                    print(f"  {GREEN}{playlist['title']!r}{RESET}: {count} dates (oauth)")
                    any_written = True

    # Innertube fallback for Watch Later
    wl_playlists = [p for p in playlists_needing if p["playlist_id"] == "WL"]
    for playlist in wl_playlists:
        missing = sum(1 for v in playlist["videos"] if not v.get("added_to_playlist"))
        if not missing:
            continue
        print(f"  [wl-dates] {missing} Watch Later videos missing dates — "
              f"fetching via innertube...")
        wl_dates = _fetch_wl_added_dates_innertube()
        if not wl_dates:
            continue
        count = 0
        for v in playlist["videos"]:
            if not v.get("added_to_playlist"):
                d = wl_dates.get(v["video_id"])
                if d:
                    v["added_to_playlist"] = d
                    count += 1
        if count:
            print(f"  {GREEN}'Watch Later'{RESET}: {count} dates")
            any_written = True

    if any_written:
        print(f"  {GREEN}✓ added_to_playlist backfill complete{RESET}")
    return any_written


def run_upload_dates_pass(data: dict) -> bool:
    """Backfill upload_date for videos missing it, via YouTube Data API v3.

    Supports two modes (checked in order):
      1. YT_API_KEY env var — simple API key, no libraries needed (works in CI)
      2. .env/yt_client_secrets.json — OAuth (local, needs google-api-python-client)

    Lightweight: 1 API call per 50 videos (snippet only).
    Modifies data in-place. Returns True if any dates were written.
    """
    # Collect all video IDs missing upload_date across all playlists
    missing = []
    for p in data.get("playlists", []):
        if p.get("deleted"):
            continue
        for v in p.get("videos", []):
            if not v.get("upload_date") and v.get("video_id"):
                missing.append(v)

    if not missing:
        return False

    print(f"\n[upload dates] {len(missing)} videos missing upload_date — "
          f"fetching from YouTube API...", flush=True)

    # Batch fetch upload dates
    vid_map: dict[str, str] = {}
    ids = [v["video_id"] for v in missing]

    if YT_API_KEY:
        # ── Simple API key mode (no pip packages needed) ──────────────
        for i in range(0, len(ids), 50):
            batch = ids[i:i + 50]
            params = urllib.parse.urlencode({
                "part": "snippet",
                "id": ",".join(batch),
                "fields": "items(id,snippet/publishedAt)",
                "key": YT_API_KEY,
            })
            url = f"https://www.googleapis.com/youtube/v3/videos?{params}"
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    body = json.loads(resp.read())
            except Exception as e:
                print(f"  [yt-api] videos.list error: {e}", file=sys.stderr)
                continue
            for item in body.get("items", []):
                pub = item.get("snippet", {}).get("publishedAt", "")
                if pub:
                    vid_map[item["id"]] = pub[:10]  # "2026-03-15"
    else:
        # ── OAuth mode (needs google-api-python-client) ───────────────
        svc = _get_youtube_service()
        if svc is None:
            return False
        for i in range(0, len(ids), 50):
            batch = ids[i:i + 50]
            try:
                resp = svc.videos().list(
                    part="snippet",
                    id=",".join(batch),
                ).execute()
            except Exception as e:
                print(f"  [yt-api] videos.list error: {e}", file=sys.stderr)
                continue
            for item in resp.get("items", []):
                pub = item.get("snippet", {}).get("publishedAt", "")
                if pub:
                    vid_map[item["id"]] = pub[:10]  # "2026-03-15"

    if not vid_map:
        return False

    # Apply to videos
    count = 0
    for v in missing:
        d = vid_map.get(v["video_id"])
        if d:
            v["upload_date"] = d
            v["upload_date_raw"] = d.replace("-", "")
            count += 1

    if count:
        print(f"  {GREEN}✓ {count} upload dates filled{RESET}")
    return count > 0


def _fetch_playlist_metadata_api_key(playlist_ids: list[str]) -> dict[str, dict]:
    """Fetch playlist creator and privacy via simple YT_API_KEY (no OAuth).
    Returns {playlist_id: {"creator": str, "privacy": str}}."""
    pid_map: dict[str, dict] = {}
    for i in range(0, len(playlist_ids), 50):
        batch = playlist_ids[i:i + 50]
        params = urllib.parse.urlencode({
            "part": "snippet,status",
            "id": ",".join(batch),
            "fields": "items(id,snippet/channelTitle,status/privacyStatus)",
            "key": YT_API_KEY,
        })
        url = f"https://www.googleapis.com/youtube/v3/playlists?{params}"
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                body = json.loads(resp.read())
        except Exception as e:
            print(f"  [yt-api] playlists.list error: {e}", file=sys.stderr)
            continue
        for item in body.get("items", []):
            pid_map[item["id"]] = {
                "creator": item.get("snippet", {}).get("channelTitle", ""),
                "privacy": item.get("status", {}).get("privacyStatus", ""),
            }
    return pid_map


def run_playlist_metadata_pass(data: dict) -> bool:
    """Backfill playlist_creator and privacy_status via YouTube Data API v3.

    Supports two modes (checked in order):
      1. YT_API_KEY env var — simple API key, no libraries needed (works in CI)
      2. .env/yt_client_secrets.json — OAuth (local, needs google-api-python-client)
    WL and Liked Videos are auto-set (API blocks them).
    Modifies data in-place. Returns True if anything changed.
    """
    changed = False

    # Collect playlists missing metadata (skip WL/Liked for now)
    missing = []
    for p in data.get("playlists", []):
        if p.get("deleted"):
            continue
        pid = p.get("playlist_id", "")
        if pid in ("WL",) or pid.startswith("LL"):
            continue
        if not p.get("playlist_creator"):
            missing.append(p)

    if missing:
        print(f"\n[playlist metadata] {len(missing)} playlists missing creator — "
              f"fetching from YouTube API...")

        pid_map: dict[str, dict] = {}
        ids = [p["playlist_id"] for p in missing]

        # Try API key first
        if YT_API_KEY:
            pid_map = _fetch_playlist_metadata_api_key(ids)

        # OAuth fallback for any not resolved by API key
        if len(pid_map) < len(ids):
            svc = _get_youtube_service()
            if svc is not None:
                remaining = [pid for pid in ids if pid not in pid_map]
                for i in range(0, len(remaining), 50):
                    batch = remaining[i:i + 50]
                    try:
                        resp = svc.playlists().list(
                            part="snippet,status",
                            id=",".join(batch),
                        ).execute()
                    except Exception as e:
                        print(f"  [yt-api] playlists.list error: {e}", file=sys.stderr)
                        continue
                    for item in resp.get("items", []):
                        pid_map[item["id"]] = {
                            "creator": item.get("snippet", {}).get("channelTitle", ""),
                            "privacy": item.get("status", {}).get("privacyStatus", ""),
                        }

        # Apply
        count = 0
        for p in missing:
            info = pid_map.get(p["playlist_id"])
            if info:
                p["playlist_creator"] = info["creator"]
                p["privacy_status"] = info["privacy"]
                count += 1
                changed = True

        if count:
            print(f"  {GREEN}✓ {count} playlists updated{RESET}")

    # Discover channel name and store in metadata
    changed = _auto_set_wl_liked_creator(data, changed)
    return changed


def _auto_set_wl_liked_creator(data: dict, changed: bool) -> bool:
    """Set WL/Liked creator to match the user's channel name from other playlists."""
    # Find the user's channel name from playlists they created (most common creator)
    from collections import Counter
    creators = Counter()
    for p in data.get("playlists", []):
        if p.get("deleted"):
            continue
        cr = p.get("playlist_creator", "")
        pid = p.get("playlist_id", "")
        if cr and pid not in ("WL",) and not pid.startswith("LL"):
            creators[cr] += 1

    if not creators:
        return changed

    # The most frequent creator is the user's channel
    channel_name = creators.most_common(1)[0][0]
    data.setdefault("metadata", {})["youtube_channel"] = channel_name

    # Apply to WL and Liked Videos
    for p in data.get("playlists", []):
        if p.get("deleted"):
            continue
        pid = p.get("playlist_id", "")
        if pid in ("WL",) or pid.startswith("LL"):
            if p.get("playlist_creator") != channel_name:
                p["playlist_creator"] = channel_name
                p["privacy_status"] = "private"
                changed = True

    return changed


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_video(flat: dict, full: dict | None, position: int) -> dict:
    e   = flat
    enr = full

    vid_id = e.get("id") or e.get("video_id") or ""
    url    = (e.get("url") or e.get("webpage_url") or
              (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else ""))

    dur = e.get("duration")
    dur_str = None
    if dur is not None:
        try:
            d = int(dur)
            dur_str = (f"{d//3600:02}:{(d%3600)//60:02}:{d%60:02}"
                       if d >= 3600 else f"{d//60:02}:{d%60:02}")
        except (ValueError, TypeError):
            pass

    raw_date = (enr.get("upload_date") if enr else None) or e.get("upload_date")
    iso_date = (f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                if raw_date and len(raw_date) == 8 else None)

    thumbnails = e.get("thumbnails") or []
    thumb = (e.get("thumbnail") or
             (thumbnails[-1].get("url") if thumbnails else None))

    ts = e.get("timestamp")
    added_to_playlist = (datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                         if ts is not None else None)

    return {
        # ── Always available (flat) ───────────────────────────────────────
        "position":            position,
        "video_id":            vid_id,
        "title":               e.get("title"),
        "url":                 url,
        "channel":             e.get("channel") or e.get("uploader"),
        "channel_id":          e.get("channel_id") or e.get("uploader_id"),
        "channel_url":         e.get("channel_url") or e.get("uploader_url"),
        "duration_seconds":    dur,
        "duration":            dur_str,
        "view_count":          e.get("view_count"),
        "thumbnail":           thumb,
        "added_to_playlist":   added_to_playlist,
        # ── Only available after --enrich ─────────────────────────────────
        "upload_date":         iso_date,
        "upload_date_raw":     raw_date,
        "description":         enr.get("description") if enr else None,
        "like_count":          enr.get("like_count") if enr else None,
        "comment_count":       enr.get("comment_count") if enr else None,
        "age_limit":           enr.get("age_limit") if enr else None,
        "availability":        enr.get("availability") if enr else None,
        "live_status":         enr.get("live_status") if enr else None,
        "was_live":            enr.get("was_live") if enr else None,
        "categories":          enr.get("categories") if enr else None,
        "tags":                enr.get("tags") if enr else None,
        "subtitles_available": bool(enr.get("subtitles") or enr.get("automatic_captions")) if enr else None,
        "language":            enr.get("language") if enr else None,
        # ── Meta ──────────────────────────────────────────────────────────
        "enriched":            enr is not None,
        "transcript":          False,
        "fetched_at":          datetime.now(timezone.utc).isoformat(),
    }


def _playlist_meta_from_entries(flat_entries: list[dict]) -> dict:
    for e in flat_entries:
        if e.get("playlist_channel") or e.get("playlist_uploader"):
            creator = e.get("playlist_channel") or e.get("playlist_uploader")
            return {
                "description":      e.get("playlist_description"),
                "channel":          creator,
                "channel_id":       (e.get("playlist_channel_id") or
                                     e.get("playlist_uploader_id")),
                "playlist_creator": creator,
                "thumbnail":        None,
                "view_count":       None,
                "modified_date":    None,
            }
    return {}


# ── Playlist build / update ───────────────────────────────────────────────────

def build_new_playlist(stub: dict) -> dict:
    flat_entries = flat_fetch_videos(stub["url"])
    n = len(flat_entries)
    videos = [parse_video(e, None, i + 1) for i, e in enumerate(flat_entries)]
    pmeta  = _playlist_meta_from_entries(flat_entries)
    return {
        "playlist_id":   stub["playlist_id"],
        "title":         stub["title"],
        "url":           stub["url"],
        **pmeta,
        "video_count":   n,
        "videos":        videos,
        "first_fetched": datetime.now(timezone.utc).isoformat(),
        "last_updated":  datetime.now(timezone.utc).isoformat(),
        "deleted":       False,
    }


def incremental_update(existing: dict, new_title: str) -> tuple[dict, dict]:
    """Diff existing playlist. Returns (updated_record, changes_dict)."""
    pid   = existing["playlist_id"]
    url   = existing["url"]
    title = existing["title"]
    changes: dict = {"playlist_id": pid, "title": title}

    if new_title != title:
        existing["title"] = new_title
        changes["renamed"] = {"from": title, "to": new_title}

    flat_entries = flat_fetch_videos(url)
    current_ids  = [e.get("id") or e.get("video_id", "") for e in flat_entries]
    flat_map     = {(e.get("id") or e.get("video_id", "")): e for e in flat_entries}

    existing_ids = [v["video_id"] for v in existing.get("videos", [])
                    if v.get("video_id")]
    added_ids   = [v for v in current_ids  if v not in set(existing_ids)]
    removed_ids = [v for v in existing_ids if v not in set(current_ids)]
    changes["videos_added"]   = added_ids
    changes["videos_removed"] = removed_ids

    # Backfill missing channel info from flat entries (even if no adds/removes)
    backfilled = 0
    for v in existing.get("videos", []):
        vid_id = v.get("video_id")
        if not v.get("channel") and vid_id and vid_id in flat_map:
            ch = flat_map[vid_id].get("channel") or flat_map[vid_id].get("uploader")
            if ch:
                v["channel"] = ch
                v["channel_id"] = flat_map[vid_id].get("channel_id") or flat_map[vid_id].get("uploader_id")
                v["channel_url"] = flat_map[vid_id].get("channel_url") or flat_map[vid_id].get("uploader_url")
                backfilled += 1
    if backfilled:
        changes["channels_backfilled"] = backfilled

    if not added_ids and not removed_ids and "renamed" not in changes and not backfilled:
        changes["no_changes"] = True
        return existing, changes

    if not added_ids and not removed_ids and not backfilled:
        existing["last_updated"] = datetime.now(timezone.utc).isoformat()
        return existing, changes

    existing_map = {v["video_id"]: v for v in existing.get("videos", [])
                    if v.get("video_id")}
    new_videos   = {vid: parse_video(flat_map[vid], None, 0)
                    for vid in added_ids if vid in flat_map}

    # For Watch Later: newly detected videos were just added by the user.
    # Set added_to_playlist = today (innertube gives wrong dates for recent adds).
    if pid == "WL" and new_videos:
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        for v in new_videos.values():
            v["added_to_playlist"] = today

    videos = []
    for pos, vid_id in enumerate(current_ids, 1):
        if vid_id in new_videos:
            v = new_videos[vid_id]
            v["position"] = pos
            videos.append(v)
        elif vid_id in existing_map:
            v = existing_map[vid_id]
            v["position"] = pos
            videos.append(v)

    existing["videos"]       = videos
    existing["video_count"]  = len(videos)
    existing["last_updated"] = datetime.now(timezone.utc).isoformat()
    return existing, changes


def structural_update(existing: dict, new_title: str) -> tuple[dict, dict]:
    """
    Structural-only diff. Writes back ONLY:
      - playlist title (if renamed)
      - per-video title (if video was renamed by creator)
      - removes deleted/private videos
      - adds new videos (flat data, enriched=False)
    Does NOT touch view_count, duration, thumbnail, channel for existing videos.
    Crash-safe: if interrupted, JSON is unchanged (no intermediate saves in this mode).
    """
    pid   = existing["playlist_id"]
    url   = existing["url"]
    title = existing["title"]
    changes: dict = {"playlist_id": pid, "title": title}

    if new_title != title:
        existing["title"] = new_title
        changes["renamed"] = {"from": title, "to": new_title}

    flat_entries = flat_fetch_videos(url)
    current_ids  = [e.get("id") or e.get("video_id", "") for e in flat_entries]
    flat_map     = {(e.get("id") or e.get("video_id", "")): e for e in flat_entries}

    existing_ids = [v["video_id"] for v in existing.get("videos", [])
                    if v.get("video_id")]
    added_ids    = [v for v in current_ids if v not in set(existing_ids)]
    removed_ids  = [v for v in existing_ids if v not in set(current_ids)]
    changes["videos_added"]   = added_ids
    changes["videos_removed"] = removed_ids

    # Detect per-video title changes
    existing_map  = {v["video_id"]: v for v in existing.get("videos", [])
                     if v.get("video_id")}
    title_changes = [vid for vid, flat in flat_map.items()
                     if vid in existing_map
                     and flat.get("title")
                     and flat.get("title") != existing_map[vid].get("title")]
    changes["video_titles_changed"] = title_changes

    if (not added_ids and not removed_ids
            and not title_changes and "renamed" not in changes):
        changes["no_changes"] = True
        return existing, changes

    # For Watch Later: newly detected videos were just added by the user.
    wl_today = (datetime.now(timezone.utc).strftime('%Y-%m-%d')
                if pid == "WL" else None)

    videos = []
    for pos, vid_id in enumerate(current_ids, 1):
        if vid_id in existing_map:
            v = existing_map[vid_id]
            v["position"] = pos
            if vid_id in title_changes:
                v["title"] = flat_map[vid_id].get("title")
            videos.append(v)
        elif vid_id in added_ids and vid_id in flat_map:
            v = parse_video(flat_map[vid_id], None, pos)
            v["enriched"] = False
            if wl_today:
                v["added_to_playlist"] = wl_today
            videos.append(v)

    existing["videos"]       = videos
    existing["video_count"]  = len(videos)
    existing["last_updated"] = datetime.now(timezone.utc).isoformat()
    return existing, changes


# ── Transcript fetch ──────────────────────────────────────────────────────────

def _safe_title(title: str) -> str:
    """Sanitize a video title for use in a filename."""
    s = re.sub(r'[/\\:*?"<>|]', '', title)
    s = s.strip('. ')
    return s or 'untitled'


def _transcript_filename(video_id: str, title: str, channel: str = "") -> str:
    """Build transcript filename: '{channel} - {video_id} {title}.txt'."""
    ch = _safe_title(channel) if channel else ""
    ti = _safe_title(title)
    if ch:
        return f"{ch} - {video_id} {ti}.txt"
    return f"{video_id} {ti}.txt"


def _srt_to_text(content: str) -> str:
    """Convert SRT/VTT subtitle content to plain text (strip timestamps, tags)."""
    lines = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r'^\d+$', line):              # sequence number
            continue
        if re.match(r'\d{2}:\d{2}:\d{2}', line):  # timestamp line
            continue
        if line.startswith('WEBVTT'):              # VTT header
            continue
        if re.match(r'^(Kind|Language):', line):   # VTT metadata
            continue
        line = re.sub(r'<[^>]+>', '', line)        # strip HTML-like tags
        if line:
            lines.append(line)
    return '\n'.join(lines)


def run_transcript_fetch(data: dict, video_ids: list[str]) -> None:
    """Download subtitles for the given video IDs and save as plain text."""
    # Build lookup: video_id -> video_dict
    vid_lookup: dict[str, dict] = {}
    for p in data.get("playlists", []):
        if p.get("deleted"):
            continue
        for v in p.get("videos", []):
            vid = v.get("video_id")
            if vid and vid not in vid_lookup:
                vid_lookup[vid] = v

    requested = [vid for vid in video_ids if vid in vid_lookup]
    if not requested:
        print("  No matching video IDs found in data.")
        return

    print(f"\n  Fetching transcripts for {len(requested)} videos...")
    fetched = 0

    for vid_id in requested:
        video = vid_lookup[vid_id]
        fname = _transcript_filename(vid_id, video.get("title", ""), video.get("channel", ""))
        txt_path = TRANSCRIPTS_DIR / fname

        # Also check for any existing file containing this video_id
        existing = list(TRANSCRIPTS_DIR.glob(f"*{vid_id}*.txt")) if TRANSCRIPTS_DIR.exists() else []
        if existing:
            print(f"    {vid_id}: already exists — skipping")
            video["transcript"] = True
            video["transcript_file"] = existing[0].name
            continue

        os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
        url = f"https://www.youtube.com/watch?v={vid_id}"
        cmd = [
            YTDLP,
            "--write-subs", "--write-auto-subs",
            "--sub-langs", "en.*",
            "--skip-download",
            "--convert-subs", "srt",
            "--ignore-no-formats-error",
            "-o", str(TRANSCRIPTS_DIR / "%(id)s"),
            url,
        ]
        if COOKIES_FILE:
            cmd += ["--cookies", COOKIES_FILE]
        else:
            cmd += ["--cookies-from-browser", BROWSER]

        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            print(f"    {vid_id}: timeout — skipping")
            continue
        except Exception as exc:
            print(f"    {vid_id}: error — {exc}")
            continue

        # Find downloaded subtitle file (.srt or .vtt)
        sub_files = (list(TRANSCRIPTS_DIR.glob(f"{vid_id}*.srt"))
                     or list(TRANSCRIPTS_DIR.glob(f"{vid_id}*.vtt")))
        if not sub_files:
            print(f"    {vid_id}: no subtitles found")
            continue

        sub_file = sub_files[0]
        sub_content = sub_file.read_text(encoding="utf-8", errors="replace")
        plain_text = _srt_to_text(sub_content)

        # Clean up ALL intermediate subtitle files regardless of outcome
        for f in TRANSCRIPTS_DIR.glob(f"{vid_id}*.srt"):
            f.unlink(missing_ok=True)
        for f in TRANSCRIPTS_DIR.glob(f"{vid_id}*.vtt"):
            f.unlink(missing_ok=True)

        if not plain_text.strip():
            print(f"    {vid_id}: empty subtitle — skipping")
            continue

        txt_path.write_text(plain_text, encoding="utf-8")

        video["transcript"] = True
        video["transcript_file"] = fname
        fetched += 1
        print(f"    {GREEN}✓{RESET} {vid_id}: {video.get('title', '')[:50]}")
        time.sleep(1.5)

    print(f"  {GREEN}✓ {fetched} transcripts fetched{RESET}")


def run_transcript_sync(data: dict) -> bool:
    """Sync transcript fields with TRANSCRIPTS/ folder on disk.

    Returns True if any changes were made.
    """
    if not TRANSCRIPTS_DIR.exists():
        return False

    # Collect all transcript filenames
    all_files = [f.name for f in TRANSCRIPTS_DIR.glob("*.txt")]

    # Collect all known video IDs
    all_vids: set[str] = set()
    for p in data.get("playlists", []):
        if p.get("deleted"):
            continue
        for v in p.get("videos", []):
            vid = v.get("video_id")
            if vid:
                all_vids.add(vid)

    # Build map: video_id -> filename by checking which vid appears in each filename
    file_by_vid: dict[str, str] = {}
    for fname in all_files:
        for vid in all_vids:
            if vid in fname:
                file_by_vid[vid] = fname
                break

    changes = 0
    for p in data.get("playlists", []):
        if p.get("deleted"):
            continue
        for v in p.get("videos", []):
            vid = v.get("video_id")
            if not vid:
                continue
            has_file = vid in file_by_vid
            has_field = v.get("transcript", False)

            if has_field and not has_file:
                v["transcript"] = False
                v.pop("transcript_file", None)
                changes += 1
                print(f"  sync: {vid} transcript=true but file missing → false")
            elif has_file and not has_field:
                v["transcript"] = True
                v["transcript_file"] = file_by_vid[vid]
                changes += 1
                print(f"  sync: {vid} file exists but transcript=false → true")

    if changes:
        print(f"  {GREEN}sync: {changes} transcript field(s) updated{RESET}")
    else:
        print("  sync: all transcript fields in sync")

    return changes > 0


# ── Enrichment pass ───────────────────────────────────────────────────────────

def run_enrichment(data: dict) -> dict:
    total_needed = sum(
        sum(1 for v in p.get("videos", []) if not v.get("enriched"))
        for p in data.get("playlists", []) if not p.get("deleted")
    )
    print(f"\n[3/3] Enriching {total_needed} videos "
          f"(upload_date, likes, description)...")
    total_done = 0
    api_svc = _get_youtube_service()

    for playlist in data.get("playlists", []):
        if playlist.get("deleted"):
            continue
        url    = playlist["url"]
        title  = playlist["title"]
        videos = playlist.get("videos", [])

        unenriched = [(i, v) for i, v in enumerate(videos)
                      if not v.get("enriched") and v.get("video_id")]
        if not unenriched:
            continue

        print(f"  {YELLOW}{title!r}{RESET}: {len(unenriched)} videos", flush=True)
        positions    = [v["position"] for _, v in unenriched if v.get("position")] \
                       or [i + 1 for i, _ in unenriched]
        enriched_map = enrich_videos_by_positions(url, positions)

        # Fallback: fetch missing videos directly by URL
        missing_ids = [v.get("video_id") for _, v in unenriched
                       if not enriched_map.get(v.get("video_id", ""))]
        if missing_ids:
            print(f"    [fallback] fetching {len(missing_ids)} by direct URL", flush=True)
            direct_map = enrich_videos_by_url(missing_ids)
            enriched_map.update(direct_map)

        missing_ids = [v.get("video_id") for _, v in unenriched
                       if not enriched_map.get(v.get("video_id", ""))]
        if api_svc and missing_ids:
            print(f"  [yt-api] fetching {len(missing_ids)} via API fallback "
                  f"(members-only / inaccessible)", flush=True)
        api_map = _enrich_via_api(api_svc, missing_ids) if api_svc and missing_ids else {}

        for idx, video in unenriched:
            full     = enriched_map.get(video.get("video_id", ""))
            api_data = api_map.get(video.get("video_id", "")) if not full else None
            if not full and not api_data:
                continue
            if full:
                raw_date = full.get("upload_date")
                updates = {
                    "upload_date":         (f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                                            if raw_date and len(raw_date) == 8 else None),
                    "upload_date_raw":     raw_date,
                    "description":         full.get("description"),
                    "like_count":          full.get("like_count"),
                    "comment_count":       full.get("comment_count"),
                    "age_limit":           full.get("age_limit"),
                    "availability":        full.get("availability"),
                    "live_status":         full.get("live_status"),
                    "was_live":            full.get("was_live"),
                    "categories":          full.get("categories"),
                    "tags":                full.get("tags"),
                    "subtitles_available": bool(full.get("subtitles") or full.get("automatic_captions")),
                    "language":            full.get("language"),
                    "enriched":            True,
                }
                # Backfill channel info if missing from original flat fetch
                if not video.get("channel"):
                    ch = full.get("channel") or full.get("uploader")
                    if ch:
                        updates["channel"] = ch
                        updates["channel_id"] = full.get("channel_id") or full.get("uploader_id")
                        updates["channel_url"] = full.get("channel_url") or full.get("uploader_url")
                video.update(updates)
            elif api_data:
                video.update({**api_data, "enriched": True, "members_only": True})
            videos[idx] = video
            total_done += 1

        playlist["videos"] = videos
        save_output(data)
        time.sleep(DELAY_PLAYLIST)

    print(f"  {GREEN}✓ {total_done} videos enriched{RESET}")

    # ── Backfill missing channel info via YouTube Data API ────────────────
    missing_channel = []
    for playlist in data.get("playlists", []):
        if playlist.get("deleted"):
            continue
        for v in playlist.get("videos", []):
            if not v.get("channel") and v.get("video_id"):
                missing_channel.append(v)

    if missing_channel and api_svc:
        print(f"\n[channel backfill] {len(missing_channel)} videos missing channel — "
              f"fetching via API...", flush=True)
        vid_ids = [v["video_id"] for v in missing_channel]
        channel_map: dict[str, dict] = {}
        for i in range(0, len(vid_ids), 50):
            batch = vid_ids[i:i + 50]
            try:
                resp = api_svc.videos().list(
                    part="snippet", id=",".join(batch)
                ).execute()
            except Exception as e:
                print(f"  [yt-api] videos.list error: {e}", file=sys.stderr)
                continue
            for item in resp.get("items", []):
                snip = item.get("snippet", {})
                channel_map[item["id"]] = {
                    "channel":     snip.get("channelTitle"),
                    "channel_id":  snip.get("channelId"),
                    "channel_url": (f"https://www.youtube.com/channel/{snip['channelId']}"
                                    if snip.get("channelId") else None),
                }
        filled = 0
        for v in missing_channel:
            info = channel_map.get(v["video_id"])
            if info and info.get("channel"):
                v["channel"]     = info["channel"]
                v["channel_id"]  = info["channel_id"]
                v["channel_url"] = info["channel_url"]
                filled += 1
        if filled:
            save_output(data)
        print(f"  {GREEN}✓ {filled}/{len(missing_channel)} channels backfilled{RESET}")
    elif missing_channel:
        print(f"\n  {YELLOW}Note: {len(missing_channel)} videos missing channel info "
              f"(no YouTube API key configured){RESET}")

    return data


# ── Fetch log (lightweight change tracker) ────────────────────────────────────

def load_fetch_log() -> dict | None:
    if not os.path.exists(FETCH_LOG_FILE):
        return None
    with open(FETCH_LOG_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_fetch_log(playlists: list[dict]):
    """
    Save a lightweight log of what we control: title, video_count, video_ids.
    Not tracking view_count or other externally-driven fields.
    """
    log = {
        "fetched_at":      datetime.now(timezone.utc).isoformat(),
        "total_playlists": len([p for p in playlists if not p.get("deleted")]),
        "total_videos":    sum(p.get("video_count", 0)
                               for p in playlists if not p.get("deleted")),
        "playlists": {
            p["playlist_id"]: {
                "title":       p["title"],
                "video_count": p.get("video_count", 0),
                "video_ids":   [v["video_id"] for v in p.get("videos", [])
                                 if v.get("video_id")],
            }
            for p in playlists if p.get("playlist_id") and not p.get("deleted")
        },
    }
    with open(FETCH_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


# ── I/O ───────────────────────────────────────────────────────────────────────

def load_existing() -> dict | None:
    if not os.path.exists(OUTPUT_FILE):
        return None
    with open(OUTPUT_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_output(data: dict):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    with open(OUTPUT_COMPACT, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), ensure_ascii=False)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Enrichment is ON by default (fetches upload_date, likes, description).
    # For a quick sync without enrichment, run:
    #   python3 fetch_youtube_playlists.py --fast
    # For titles + membership only (write-if-changed):
    #   python3 fetch_youtube_playlists.py --structural
    # --cookies-file <path>: use a Netscape cookies.txt instead of browser cookies
    global COOKIES_FILE
    if "--cookies-file" in sys.argv:
        idx = sys.argv.index("--cookies-file")
        if idx + 1 < len(sys.argv):
            COOKIES_FILE = sys.argv[idx + 1]

    enrich_mode     = "--fast" not in sys.argv and "--structural" not in sys.argv
    structural_mode = "--structural" in sys.argv
    enrich_only_mode = "--enrich-only" in sys.argv
    run_start   = datetime.now(timezone.utc)
    total_steps = 3 if enrich_mode else 2
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ── Parse --transcript-ids (can combine with any mode) ────────────────
    transcript_ids: list[str] = []
    if "--transcript-ids" in sys.argv:
        idx = sys.argv.index("--transcript-ids")
        if idx + 1 < len(sys.argv):
            transcript_ids = [v.strip() for v in sys.argv[idx + 1].split(",")
                              if v.strip()]

    # ── [--sync-transcripts] Reconcile JSON transcript fields with TRANSCRIPTS/
    if "--sync-transcripts" in sys.argv:
        data = load_existing()
        if data is None:
            print("ERROR: No existing JSON found.", file=sys.stderr)
            sys.exit(1)
        print("Syncing transcript fields with TRANSCRIPTS/ folder...")
        changed = run_transcript_sync(data)
        if changed:
            save_output(data)
            print("  JSON updated.")
        else:
            print("  No changes needed.")
        sys.exit(0)

    # ── [--transcript-ids only] No sync or enrichment, just fetch transcripts
    if transcript_ids and not enrich_only_mode and "--fast" not in sys.argv and "--structural" not in sys.argv and "--enrich" not in sys.argv:
        data = load_existing()
        if data is None:
            print("ERROR: No existing JSON found.", file=sys.stderr)
            sys.exit(1)
        run_transcript_fetch(data, transcript_ids)
        save_output(data)
        sys.exit(0)

    # ── [--enrich-only] Skip flat sync, just patch enriched=False videos ──
    if enrich_only_mode:
        data = load_existing()
        if data is None:
            print("ERROR: No existing JSON found. Run without --enrich-only first.",
                  file=sys.stderr)
            sys.exit(1)
        run_added_dates_pass(data)
        run_upload_dates_pass(data)
        run_playlist_metadata_pass(data)
        data = run_enrichment(data)
        if transcript_ids:
            run_transcript_fetch(data, transcript_ids)
        save_output(data)
        sys.exit(0)

    # ── [Previous state] ──────────────────────────────────────────────────
    fetch_log    = load_fetch_log()
    existing_data = load_existing()
    is_first_run  = existing_data is None

    if fetch_log:
        last_ts = fetch_log.get("fetched_at", "")[:19].replace("T", " ")
        print(f"Last fetch : {last_ts}  |  "
              f"{fetch_log['total_playlists']} playlists  |  "
              f"{fetch_log['total_videos']} videos")
        print()
    else:
        print("First run — no previous fetch log.\n")

    # ── [1/N] Scan for changes ─────────────────────────────────────────────
    print(f"[1/{total_steps}] Scanning YouTube for changes...")

    existing_map: dict[str, dict] = {}
    if existing_data:
        for p in existing_data.get("playlists", []):
            pid = p.get("playlist_id")
            if pid:
                existing_map[pid] = p

    current_stubs = fetch_playlist_stubs()
    if not current_stubs:
        print("ERROR: No playlists found. Check Chrome cookies / network.",
              file=sys.stderr)
        sys.exit(1)

    current_ids  = [s["playlist_id"] for s in current_stubs if s["playlist_id"]]
    current_set  = set(current_ids)
    existing_set = set(existing_map.keys())
    new_pids     = current_set - existing_set
    deleted_pids = existing_set - current_set

    print(f"  {len(current_stubs)} playlists on YouTube  |  "
          f"{len(new_pids)} new  |  {len(deleted_pids)} missing  |  "
          f"{len(current_set & existing_set)} to check")

    # ── [2/N] Process changes ──────────────────────────────────────────────
    print(f"\n[2/{total_steps}] Checking each playlist"
          + (" for structural changes..." if structural_mode else "..."))

    summary = {
        "run_at":              run_start.isoformat(),
        "is_first_run":        is_first_run,
        "enrich_mode":         enrich_mode,
        "playlists_new":       [],
        "playlists_deleted":   [],
        "playlists_updated":   [],
        "playlists_unchanged": [],
    }

    # Check "missing" playlists: truly deleted or just empty?
    # Exclude already-deleted playlists from the check
    deleted_pids = {pid for pid in deleted_pids
                    if not existing_map[pid].get("deleted")}

    if deleted_pids:
        # Ask YouTube API if they still exist
        still_alive: set[str] = set()
        svc = _get_youtube_service()
        if svc:
            pids_list = list(deleted_pids)
            for i in range(0, len(pids_list), 50):
                batch = pids_list[i:i + 50]
                try:
                    resp = svc.playlists().list(
                        part="snippet",
                        id=",".join(batch),
                    ).execute()
                    for item in resp.get("items", []):
                        still_alive.add(item["id"])
                except Exception as e:
                    print(f"  [yt-api] playlists.list check error: {e}",
                          file=sys.stderr)

        # Empty playlists: still on YouTube but yt-dlp can't see them
        for pid in still_alive:
            p = existing_map[pid]
            p["video_count"] = 0
            p["videos"] = []
            p["last_updated"] = run_start.isoformat()
            summary["playlists_updated"].append({
                "playlist_id": pid, "title": p["title"],
                "emptied": True,
            })
            print(f"  {YELLOW}~ {p['title']!r}{RESET}  emptied (0 videos)")

        # Truly deleted playlists
        truly_deleted = deleted_pids - still_alive
        for pid in truly_deleted:
            p = existing_map[pid]
            p["deleted"]    = True
            p["deleted_at"] = run_start.isoformat()
            summary["playlists_deleted"].append({"playlist_id": pid, "title": p["title"]})

    total = len(current_stubs)
    for i, stub in enumerate(current_stubs, 1):
        pid   = stub["playlist_id"]
        title = stub["title"]

        if pid in new_pids:
            print(f"  [{i}/{total}] {YELLOW}[NEW]{RESET} {title!r}", end=" ", flush=True)
            playlist = build_new_playlist(stub)
            print(f"→ {playlist['video_count']} videos")
            existing_map[pid] = playlist
            summary["playlists_new"].append({
                "playlist_id": pid, "title": title,
                "video_count": playlist["video_count"],
            })
        else:
            # Un-delete if playlist reappeared on YouTube
            if existing_map[pid].get("deleted"):
                existing_map[pid]["deleted"] = False
                existing_map[pid].pop("deleted_at", None)
                print(f"  [{i}/{total}] {YELLOW}[RESTORED]{RESET} {title!r}", end=" ", flush=True)
            else:
                print(f"  [{i}/{total}] {title!r}", end=" ", flush=True)
            if structural_mode:
                playlist, changes = structural_update(existing_map[pid], title)
            else:
                playlist, changes = incremental_update(existing_map[pid], title)
            existing_map[pid] = playlist

            if changes.get("no_changes"):
                print(f"{GREEN}✓{RESET}")
                summary["playlists_unchanged"].append({"playlist_id": pid, "title": title})
            else:
                parts = []
                if "renamed" in changes:
                    parts.append(f"renamed → {changes['renamed']['to']!r}")
                if changes.get("videos_added"):
                    parts.append(f"{YELLOW}+{len(changes['videos_added'])} videos{RESET}")
                if changes.get("videos_removed"):
                    parts.append(f"{YELLOW}-{len(changes['videos_removed'])} videos{RESET}")
                print("  " + "  ".join(parts))
                summary["playlists_updated"].append(changes)

        # Save after every playlist (crash-safe) — skip in structural mode
        if not structural_mode:
            active_so_far = [existing_map[s["playlist_id"]]
                             for s in current_stubs[:i]
                             if s["playlist_id"] in existing_map]
            deleted_list  = [existing_map[p] for p in deleted_pids]
            save_output({
                "metadata": {
                    "last_updated":      run_start.isoformat(),
                    "youtube_user":      "jsneij",
                    "total_playlists":   len(current_stubs),
                    "total_videos":      sum(p.get("video_count", 0) for p in active_so_far),
                    "deleted_playlists": len(deleted_pids),
                    "source":            "yt-dlp + Chrome cookies",
                    "partial_run":       i < total,
                    "enriched":          False,
                },
                "playlists": active_so_far + deleted_list,
            })

        if i < total:
            time.sleep(DELAY_PLAYLIST)

    # ── Final output ───────────────────────────────────────────────────────
    active       = [p for p in existing_map.values() if not p.get("deleted")]
    total_videos = sum(p.get("video_count", 0) for p in active)

    output = {
        "metadata": {
            "last_updated":      run_start.isoformat(),
            "youtube_user":      "jsneij",
            "total_playlists":   len(active),
            "total_videos":      total_videos,
            "deleted_playlists": len(deleted_pids),
            "source":            "yt-dlp + Chrome cookies",
            "enriched":          False,
        },
        "playlists": ([existing_map[s["playlist_id"]] for s in current_stubs
                       if s["playlist_id"] in existing_map]
                      + [existing_map[p] for p in deleted_pids]),
    }
    any_structural_change = bool(
        summary["playlists_new"] or
        summary["playlists_deleted"] or
        summary["playlists_updated"]
    )

    # ── Pre-action preview ─────────────────────────────────────────────────
    if enrich_mode:
        preview = [
            (p["title"], sum(1 for v in p.get("videos", []) if not v.get("enriched")))
            for p in output["playlists"] if not p.get("deleted")
        ]
        preview = [(t, n) for t, n in preview if n > 0]
        total_to_enrich = sum(n for _, n in preview)
        if total_to_enrich:
            print(f"\nPlaylists to enrich")
            print("─" * 40)
            for title, count in preview:
                print(f"  {YELLOW}{title!r}{RESET}: {count} unenriched videos")
            print(f"  {'─' * 36}")
            print(f"  {len(preview)} playlists  ·  {total_to_enrich} videos  →  proceeding to [3/3] enrichment\n")
    elif structural_mode and any_structural_change:
        new_count = len(summary["playlists_new"])
        del_count = len(summary["playlists_deleted"])
        upd_count = len(summary["playlists_updated"])
        total_affected = (
            sum(p["video_count"] for p in summary["playlists_new"])
            + del_count
            + sum(len(p.get("videos_added", [])) + len(p.get("videos_removed", []))
                  for p in summary["playlists_updated"])
        )
        print(f"\nChanges to save")
        print("─" * 40)
        for p in summary["playlists_new"]:
            print(f"  {YELLOW}+ {p['title']!r}{RESET}   ({p['video_count']} videos)")
        for p in summary["playlists_deleted"]:
            print(f"  {YELLOW}✕ {p['title']!r}{RESET}   (deleted)")
        for p in summary["playlists_updated"]:
            parts = []
            if p.get("videos_added"):
                parts.append(f"+{len(p['videos_added'])} videos")
            if p.get("videos_removed"):
                parts.append(f"-{len(p['videos_removed'])} videos")
            if "renamed" in p:
                parts.append(f"renamed → {p['renamed']['to']!r}")
            detail = "  ".join(parts) if parts else "changed"
            print(f"  {YELLOW}~ {p['title']!r}{RESET}   {detail}")
        changed_count = new_count + del_count + upd_count
        print(f"  {'─' * 36}")
        print(f"  {changed_count} playlists  ·  {total_affected} videos affected  →  writing JSON\n")

    # ── Added dates + upload dates pass (YouTube Data API v3) ────────────
    dates_written = run_added_dates_pass(output)
    upload_written = run_upload_dates_pass(output)
    meta_written = run_playlist_metadata_pass(output)

    if not structural_mode or any_structural_change or dates_written or upload_written or meta_written:
        save_output(output)

        # ── Optional enrichment ────────────────────────────────────────────
        if enrich_mode:
            output["metadata"]["enriched"] = True
            output = run_enrichment(output)
            if transcript_ids:
                run_transcript_fetch(output, transcript_ids)
            save_output(output)

        # ── Save fetch log ─────────────────────────────────────────────────
        save_fetch_log(output["playlists"])

        if structural_mode and not any_structural_change and dates_written:
            print(f"  {GREEN}✓ No structural changes — added dates backfilled{RESET}")
    else:
        print(f"  {GREEN}✓ No structural changes — JSON not rewritten{RESET}")

    summary.update({
        "total_playlists":  len(active),
        "total_videos":     total_videos,
        "duration_seconds": round(
            (datetime.now(timezone.utc) - run_start).total_seconds(), 1),
    })
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # ── [Changes since last fetch] ─────────────────────────────────────────
    any_change = (summary["playlists_new"] or summary["playlists_deleted"]
                  or summary["playlists_updated"])
    print()
    print("Changes since last fetch")
    print("─" * 40)

    if not any_change:
        print(f"  {GREEN}✓ Nothing changed{RESET}")
    else:
        if summary["playlists_new"]:
            for p in summary["playlists_new"]:
                print(f"  {YELLOW}+ {p['title']!r}{RESET}  ({p['video_count']} videos)")
        if summary["playlists_deleted"]:
            for p in summary["playlists_deleted"]:
                print(f"  {YELLOW}✕ {p['title']!r}{RESET}  (deleted)")
        for p in summary["playlists_updated"]:
            title = p.get("title", "?")
            parts = []
            if "renamed" in p:
                parts.append(f"renamed → {p['renamed']['to']!r}")
            if p.get("videos_added"):
                parts.append(f"+{len(p['videos_added'])} videos")
            if p.get("videos_removed"):
                parts.append(f"-{len(p['videos_removed'])} videos")
            print(f"  {YELLOW}~ {title!r}{RESET}  {', '.join(parts)}")

    # ── [Current summary] ──────────────────────────────────────────────────
    print()
    print("Current summary")
    print("─" * 40)
    print(f"  Playlists : {len(active)}")
    print(f"  Videos    : {total_videos}")
    if summary["playlists_new"]:
        print(f"  New       : {len(summary['playlists_new'])} playlists")
    if summary["playlists_updated"]:
        print(f"  Updated   : {len(summary['playlists_updated'])} playlists")
    if summary["playlists_deleted"]:
        print(f"  Deleted   : {len(summary['playlists_deleted'])} playlists")
    print(f"  Duration  : {summary['duration_seconds']}s")
    print(f"  Output    : {OUTPUT_FILE}  "
          f"({os.path.getsize(OUTPUT_FILE)//1024} KB)")

    if not enrich_mode and not structural_mode:
        unenriched = sum(sum(1 for v in p.get("videos", []) if not v.get("enriched"))
                         for p in active)
        if unenriched:
            print(f"\n  Note: ran in --fast mode (no enrichment)")
            print(f"       {unenriched} videos without upload_date, likes, description")
            print(f"       Run without --fast to enrich them")

    if structural_mode:
        print(f"\n  Mode: --structural  (titles + membership only)")
        print(f"       view_count, duration, thumbnails NOT updated for existing videos")
        if not any_structural_change:
            print(f"       {GREEN}✓ JSON unchanged — no rewrite performed{RESET}")


if __name__ == "__main__":
    main()
