---
name: efficient-json-fetch-pattern
description: Pattern for projects that download large lists from an API into a local JSON file, with fast incremental updates and clear terminal feedback. Apply when building any fetch pipeline that syncs remote data to local JSON.
---

# Efficient JSON Fetch Pattern

A pattern for projects that download large lists from an API into a local JSON file, where you want fast incremental updates and clear terminal feedback.

---

## Core idea

Split every run into two questions:
1. **What changed on the remote since my last fetch?**
2. **Of what changed, what do I actually care about?**

Only download what's needed to answer question 2.

---

## 1. Check before you fetch

Most APIs offer a `modifiedsince` (or equivalent) parameter. Use it.

On every run after the first:
- Pass `modifiedsince = last_fetch_date + 1 day` to the API
- If the response is empty → print "Nothing changed" and exit immediately
- If items came back → only process those

The `+1 day` offset avoids re-fetching items that were last modified on the same calendar day as your previous fetch (most APIs use inclusive date comparisons).

**First run:** no date to compare against, so fetch everything. This seeds the local JSON and the fetch log.

---

## 2. Keep a lightweight fetch log

Don't re-parse your full JSON to detect changes. Instead, maintain a small `fetch_log.json` alongside your main output file. It stores only the fields you want to track per item — not the full data.

```json
{
  "fetched_at": "2026-03-14T10:22:00+00:00",
  "counts": { "total_items": 87, "enriched": 82 },
  "items": {
    "item_id_123": {
      "name": "Item Name",
      "tracked_field": "value"
    }
  }
}
```

On the next run:
- Load the log → show previous state before fetching
- After fetching → compare new data against log → show changes
- Save updated log

---

## 3. Track only what you control

Only include fields in the fetch log that **you** modify — not fields that change due to other users' activity (view counts, global ratings, rankings, etc.). This keeps the change report meaningful.

Examples of fields worth tracking:
- Status / category (owned, wishlist, etc.)
- Your personal rating or note
- Membership (which playlist/collection an item belongs to)
- Acquisition or creation date

---

## 4. Merge, don't replace

When only a subset of items changed, don't overwrite your full JSON with just those items. Instead:
1. Load the existing full JSON into an in-memory index (`id → item`)
2. Parse the changed items from the API response
3. For each changed item, update its entry in the index
4. Write the full merged dataset back out

This keeps your full local copy intact while only touching what actually changed.

---

## 5. Terminal output structure

Every run should print in this order:

```
[Previous state]          ← from fetch log: counts at last run, timestamp

[1/N] Checking for changes since YYYY-MM-DD...
  ✓ X item(s) changed     ← or: "Nothing changed" in green → exit

[2/N] Parsing changed items...
[3/N] Fetching detail data for X items...   ← only if needed
[4/N] Writing output...

[Changes since last fetch]   ← diff between old log and new data
[Current summary]            ← counts and totals after this run
```

---

## 6. Colors

Use ANSI codes to make the output scannable at a glance:

```python
YELLOW = "\033[33m"   # changes and updates
GREEN  = "\033[32m"   # no changes / success / nothing to do
RESET  = "\033[0m"
```

- **Green** → nothing changed, everything up to date
- **Yellow** → something changed (new items, updated fields, removed items)
- Default → progress steps, counts, file paths

---

## 7. Two-tier fetching (fast + enrichment)

For APIs where full detail is expensive, split into two tiers:

| Tier | Speed | Data | When to run |
|------|-------|------|-------------|
| **Fast sync** | 1 call per collection | titles, IDs, counts, membership | Daily / automated |
| **Enrichment** | 1 call per item | descriptions, dates, tags, metadata | Manual / on-demand |

Mark each item with `"enriched": true/false` so you can track which items still need the detailed pass. Fast sync should never overwrite enrichment data.
