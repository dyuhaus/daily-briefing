# DailyBriefing

Morning intelligence newsletter — scans web sources for AI industry news, company updates (Anthropic, OpenAI, Google AI), macro market events, and Claude Code workflow discoveries, then synthesizes it all into cliff notes, optionally generates an audio podcast summary via NotebookLM, and emails the result.

---

## Pipeline stages

| Stage | Command | Purpose |
|-------|---------|---------|
| AI Industry Scanner | `python run_pipeline.py ai_industry` | Gemini grounded search — AI news (startups, orgs, GitHub repos) |
| Company News Scanner | `python run_pipeline.py company_news` | Per-company Gemini search for Anthropic, OpenAI, Google AI |
| Market News Scanner | `python run_pipeline.py market_news` | Macro market news + algo trading via Gemini + Nitter |
| YouTube Scanner | `python run_pipeline.py youtube` | yt-dlp + transcripts + LLM analysis for Claude Code content |
| Briefing Compile | `python run_pipeline.py briefing` | Synthesize + render HTML + email + save |
| Audio Briefing | `python run_pipeline.py audio` | NotebookLM audio overview → Telegram |

### Full pipeline (parallel by default)

```bash
python run_pipeline.py all
# or: python run_pipeline.py parallel

# Fallback to sequential:
python run_pipeline.py all --sequential
```

- Each scanner runs in its own process via `ProcessPoolExecutor`
- Per-scanner timeout: 10 minutes
- Failed scanners don't block others
- Briefing compiles if at least 2 scanners succeed

---

## Newsletter sections

1. **AI Industry News** — broad AI developments
2. **Company News** — Anthropic / OpenAI / Google AI per-company updates
3. **Project Applicability** — how today's AI news applies to tracked projects
4. **Market News** — macro events (Fed, CPI, earnings) + quant methods
5. **Claude Code Intel** — workflows, techniques, ecosystem trends
6. **What Changed** — today's git commits
7. **Project Status** — live status of tracked projects

---

## Repository layout

```
daily-briefing/
├── run_pipeline.py        ← Main entrypoint (all pipeline stages)
├── scanners/              ← Per-source scanner implementations
│   ├── ai_industry.py
│   ├── company_news.py
│   ├── market_news.py
│   ├── youtube.py
│   └── ...
├── briefing/              ← Synthesizer, status reader, compiler, audio briefing
├── actions/               ← Action-item extraction from scanner output
├── templates/             ← Jinja2 HTML templates for the newsletter
├── config/
│   ├── credentials.py     ← Reads creds from .env / credentials.env into os.environ
│   ├── settings.json      ← Pipeline configuration (scanner list, LLM backend, etc.)
│   └── llm.py             ← LLM backend (Claude CLI or Anthropic API)
├── scheduler/             ← Windows Task Scheduler XML + setup_scheduler.ps1
├── output/                ← Per-run artifacts (scanner JSONs, newsletters, audio)
└── assets/                ← Static assets for the newsletter
```

## Setup

```bash
# 1. Copy env template and fill in values
cp .env.example .env

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure the scheduler (Windows)
powershell -File setup_scheduler.ps1

# 4. Run a one-off test
python run_pipeline.py all
```

## LLM backend

Default: **Claude Code CLI** (subscription, no per-call cost). Switch to Anthropic API by changing `config/settings.json` → `llm.backend` to `"api"` (requires `ANTHROPIC_API_KEY` in `.env`).

## Verification criteria

- [ ] AI industry scanner returns > 0 results
- [ ] Company news scanner returns > 0 results for each company
- [ ] Market news scanner returns > 0 results
- [ ] YouTube scanner fetches transcripts for > 0 videos
- [ ] LLM synthesis produces cliff notes for all sections
- [ ] HTML newsletter renders without template errors
- [ ] Email delivery succeeds
- [ ] HTML saved to `output/newsletters/YYYY-MM-DD.html`
- [ ] Usage tracking logs to `output/usage_log.jsonl`
- [ ] Project status reader pulls live data from tracked projects
