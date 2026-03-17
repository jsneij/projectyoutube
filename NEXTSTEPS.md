# 🚀 What I've Learned & What's Next


## 🏠 Phase 1 — Local Project (Mac + Claude Code)

> *"Let me just see my YouTube playlists in one place"*

### What I built
- A full **Python fetch pipeline** with yt-dlp (flat sync, enrichment, incremental updates)
- A **vanilla HTML/CSS/JS dashboard** — no frameworks, no build step, just one file
- **YouTube innertube API reverse engineering** to recover Watch Later "added" dates
- **YouTube Data API v3** integration with OAuth for batch metadata lookups

### What I learned
| Skill | How |
|-------|-----|
| 🐍 **Python scripting** | subprocess, JSON processing, API calls, Path handling |
| 🌐 **HTML/CSS/JS** | DOM manipulation, fetch API, responsive tables, search/sort/filter |
| 🤖 **Claude Code** | CLAUDE.md, slash commands, skills, auto-memory, settings |
| 📁 **Project structure** | data/, scripts/, dashboard/, skills/ — portable and clean |
| 🔐 **Credentials** | .env/, OAuth tokens, cookie authentication |
| 🧩 **API reverse engineering** | innertube browse, SAPISIDHASH auth, continuation tokens |


## ☁️ Phase 2 — GitHub + Codespaces (This Project)

> *"Let me learn GitHub, VS Code, and CI/CD at the same time"*

### What I built
- **4 GitHub Actions workflows** — daily automation + on-demand enrichment with safety guards
- **GitHub Pages deployment** — live dashboard at jsneij.github.io
- **YouTube Data API key integration** — upload dates in fast fetch, no pip packages needed
- **Dashboard action buttons** — enrich + fetch links with smart conditional styling

### What I learned
| Skill | How |
|-------|-----|
| 🌿 **Git** | commit, push, pull, rebase, merge conflicts, stash |
| 🐙 **GitHub** | repos, secrets, settings, Pages, Actions tab |
| ⚡ **GitHub Actions** | cron schedules, workflow_dispatch, inputs, confirmation guards |
| 🔑 **Secrets management** | YT_COOKIES, YT_API_KEY — never in code, always in secrets |
| ☁️ **Google Cloud Console** | API key creation, restriction to YouTube Data API v3 |
| 🍪 **Cookie auth in CI** | Netscape format, printenv for special chars, Brave as clean source |
| 🐛 **CI debugging** | Reading action logs, yt-dlp JS runtime issues, --skip-download workaround |
| 🔄 **CI/CD concepts** | Automated pipelines, push conflicts, pull --rebase in workflows |


## 🔮 What's Next

### 🧠 Level 1 — Deeper Claude Code

| # | Topic | Why |
|---|-------|-----|
| 1 | **Hooks** | Auto-lint before commits, auto-test after file edits — the next layer of automation |
| 2 | **MCP Servers** | Build a custom MCP tool that wraps your fetch script — any Claude session can call it |
| 3 | **Claude Agent SDK** | Turn your interactive workflow into a headless agent that runs on a schedule |
| 4 | **Multi-project workflows** | Apply the same patterns (CLAUDE.md, skills/, memory/) to new projects |

### 🤖 Level 2 — AI / Broader Learning

| # | Topic | Why |
|---|-------|-----|
| 5 | **Claude API direct** | Call the Claude API from Python — e.g. a script that reads your playlist data and generates weekly "what to watch" recommendations |
| 6 | **Tool use & structured output** | Have Claude call your functions with typed JSON — the API equivalent of what Claude Code does |
| 7 | **RAG (Retrieval-Augmented Generation)** | Embed video titles/descriptions in a vector DB, search your library semantically ("find that video about protobuf I saved last month") |
| 8 | **Prompt engineering patterns** | System prompts, few-shot examples, chain-of-thought — formalize what you've been doing via CLAUDE.md and skills |

### 🌍 Level 3 — The Second Brain Vision

| # | Topic | Why |
|---|-------|-----|
| 9 | **Bookmarks project** | Same pipeline pattern: fetch → enrich → dashboard. Browser bookmarks as the data source |
| 10 | **Books project** | Track books read — metadata from OpenLibrary or Google Books API |
| 11 | **Unified dashboard** | One dashboard that combines YouTube, bookmarks, and books — search across all your knowledge |
| 12 | **Semantic search** | Use embeddings to connect content across sources — "show me everything I've saved about philosophy" |


> 💡 **The biggest leverage move is probably #3 (Agent SDK)** — you've already proven the pattern interactively, and turning it into a headless agent that maintains your YouTube library automatically would be a natural evolution of exactly what you built.


*Built with Claude Code — from zero to automated pipeline in two projects.*
