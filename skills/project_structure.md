---
name: project-structure
description: Standard folder structure, file naming conventions, and portability rules for data + dashboard projects. Apply this when organizing or reorganizing any project that combines Python scripts, local data files, and an HTML dashboard.
---

# Project Structure — Data + Dashboard Projects

## Directory Layout

```
ProjectRoot/
├── CLAUDE.md                        ← Claude Code instructions (always at root)
├── README.md                        ← project description for GitHub
├── index.html                       ← redirect to dashboard (for GitHub Pages)
├── .gitignore
├── .env/                            ← credentials (never committed)
├── scripts/                         ← all executable scripts (Python, bash, etc.)
├── dashboard/                       ← web dashboard files
│   └── dshb_<project>.html          ← single-file HTML dashboard
├── data/                            ← all data files (source of truth)
├── skills/                          ← reusable Claude skill files (including this one)
└── .github/
    └── workflows/                   ← GitHub Actions for automation
```

## Rules

### Portability — always relative paths
- The dashboard fetches data using **relative paths** (`../data/file.json`), never absolute
- Scripts reference data using paths **relative to `__file__`** with `Path(__file__).parent.parent / "data"` — safe to run from any directory
- The project folder can be zipped, uploaded to Drive, downloaded anywhere, and work immediately with `python3 -m http.server 8000`

### `data/` — not `output/`
- Named `data/` because it contains files that are both generated (fetched JSON) and hand-edited (scores, config)
- `output/` implies disposable/regeneratable — wrong when any file is manually curated

### `skills/` — reusable Claude instructions
- Each file has YAML frontmatter: `name`, `description`
- Describes *how to do something* — a pattern, a spec, a set of rules
- Portable: copy the `skills/` folder to another project and Claude picks them up

### `scripts/` — not at root
- Scripts live in `scripts/`, not scattered at the project root
- Keeps the root clean: only config files and top-level folders

---

## Dashboard Naming Convention

```
dashboard/dshb_<project_shortname>.html
```

- Prefix `dshb_` makes dashboard files instantly recognizable
- Single self-contained HTML file — no build step, no framework, no npm
- Fetches data via relative path `../data/`

---

## GitHub Pages Setup

For projects hosted on GitHub Pages:
- `index.html` at root redirects to `dashboard/dshb_<project>.html`
- Data files in `data/` are served directly
- Dashboard fetches `../data/` via relative path — works both locally and on Pages

---

## What Goes Where — Quick Reference

| File type | Location |
|-----------|----------|
| Claude project instructions | `CLAUDE.md` (root) |
| Python / bash scripts | `scripts/` |
| Generated or curated data | `data/` |
| HTML dashboard | `dashboard/dshb_<name>.html` |
| Reusable Claude skill files | `skills/` |
| GitHub Actions workflows | `.github/workflows/` |
| Credentials | `.env/` (root, never committed) |
| GitHub Pages entry point | `index.html` (root, redirects to dashboard) |
