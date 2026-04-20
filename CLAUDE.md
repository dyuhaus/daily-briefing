# DailyBriefing — Agent Instructions

## Identity
You are the Daily Briefing pipeline agent. You gather intelligence from web scanners, synthesize it into actionable cliff notes, pull live project status from background systems, and deliver a morning newsletter.

## Project Location
<workspace>\DailyBriefing

## Pipeline Stages

### Individual Scanners
| Stage | Command | Purpose |
|-------|---------|---------|
| AI Industry Scanner | `python run_pipeline.py ai_industry` | Gemini grounded search for broad AI news (startups, orgs, GitHub repos) |
| Company News Scanner | `python run_pipeline.py company_news` | Per-company Gemini grounded search for Anthropic, OpenAI, and Google AI news |
| Market News Scanner | `python run_pipeline.py market_news` | Gemini grounded search + Nitter for macro market news and algo trading |
| YouTube Scanner | `python run_pipeline.py youtube` | yt-dlp + transcript extraction + LLM analysis for Claude Code |
| Briefing Compile | `python run_pipeline.py briefing` | Synthesize + render + email + save HTML |
| Audio Briefing | `python run_pipeline.py audio` | Generate NotebookLM audio overview + send to Telegram |

### Audio Briefing Pipeline (NotebookLM + Telegram)
Generates a podcast-style audio summary of the daily briefing and sends it to Telegram for mobile listening.

**Flow:**
1. `audio_briefing.py` saves the plain text briefing to a local file
2. Agent uses NotebookLM MCP to add the text as a source to the DailyBriefing notebook (`your-notebooklm-notebook-id`)
3. Agent creates an audio overview via `studio_create(artifact_type="audio", audio_format="brief")`
4. Agent downloads the audio via `download_artifact(artifact_type="audio")`
5. `send_audio_telegram()` sends the MP4 to the configured Telegram chat

**NotebookLM Notebook ID:** `your-notebooklm-notebook-id` (Brain — DailyBriefing)

### Full Pipeline (Parallel Mode — Default)
| Command | Behavior |
|---------|----------|
| `python run_pipeline.py all` | Run all 4 scanners **in parallel** (ProcessPoolExecutor), then compile briefing — scanners: ai_industry, company_news, market_news, youtube |
| `python run_pipeline.py parallel` | Alias for `all` |
| `python run_pipeline.py all --sequential` | Fallback: run scanners one by one instead of in parallel |

**Parallel execution details:**
- Each scanner runs in its own **isolated process** via `ProcessPoolExecutor`
- Per-scanner timeout: **10 minutes** (600 seconds)
- If a scanner fails or times out, the others continue unaffected
- Briefing compiler runs only if **at least 2 scanners succeed**
- Status output: `[PARALLEL] Starting 4 scanners...` / `[DONE] ai_industry (42s)` / `[FAIL] company_news (timeout)`
- Total pipeline time and per-scanner durations logged at the end

## Newsletter Sections
1. **Kalshi Paper Trader** — live paper trading performance banner
2. **AI Industry News** — broad AI developments (startups, major org features, GitHub repos)
3. **Company News** — per-company updates: Anthropic (Claude/API), OpenAI (GPT/ChatGPT), Google AI (Gemini/DeepMind/Firebase)
4. **Project Applicability** — analysis of how today's AI news applies to current projects
5. **Market News** — macro market events (Fed, CPI, earnings) and algorithmic/quantitative trading methods
6. **Claude Code Intel** — workflows, techniques, ecosystem trends
7. **What Changed** — git commits from today
8. **Project Status** — live status of tracked projects

## File Authority
| Path | Access | Purpose |
|------|--------|---------|
| config/settings.json | READ-WRITE | Pipeline configuration |
| config/credentials.py | READ-ONLY | Credential loader |
| config/llm.py | READ-WRITE | LLM backend (Claude CLI or API) |
| scanners/ | READ-WRITE | Scanner implementations |
| briefing/ | READ-WRITE | Synthesizer, status reader, compiler |
| templates/ | READ-WRITE | Jinja2 HTML templates |
| output/ | WRITE | Daily outputs (scanner JSONs, newsletters, logs) |

## LLM Backend
Currently using Claude Code CLI (subscription, $0 cost). Can switch to Anthropic API by changing config/settings.json `llm.backend` to "api".

## Verification Criteria
- [ ] AI industry scanner collects >0 results from Gemini grounded search
- [ ] Company news scanner collects >0 results for each company (Anthropic, OpenAI, Google AI)
- [ ] Market news scanner collects >0 results from Gemini grounded search
- [ ] YouTube scanner fetches transcripts for >0 videos
- [ ] LLM synthesis produces cliff notes for all content sections
- [ ] HTML newsletter renders without template errors
- [ ] Email delivery succeeds (check SMTP credentials)
- [ ] HTML file saved to output/newsletters/YYYY-MM-DD.html
- [ ] Usage tracking logs all LLM calls to output/usage_log.jsonl
- [ ] Project status reader pulls live data from tracked projects
