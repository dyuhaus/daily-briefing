# DailyBriefing — Session Context
Updated: 2026-03-26

## Status Snapshot
- Pipeline: Operational (all 5 scanners + compiler running daily)
- Scheduled tasks: YouTube (6:00), AIIndustry (6:15), MarketNews (6:30), CompanyNews (7:00), Compile (7:30)
- LLM backend: Claude Code CLI (subscription, $0 cost)
- Email: Delivering to your-email@example.com

## Key Metrics
- Last run: 2026-03-26 (full pipeline — email delivered 07:32 AM, re-run 12:43 PM with company news)
- Active scanners: 4 (youtube, ai_industry, market_news, company_news)
- Newsletter sections: 8 (Kalshi Banner, AI Industry, Company News, Project Applicability, Market News, Claude Code Intel, What Changed, Project Status)

## Scanner Health
- YouTube: Working (yt-dlp, transcript budget: 3/day)
- AI Industry: Working (Gemini grounded search)
- Market News: Working (Gemini grounded search, Nitter all down)
- Company News: Working (Gemini grounded search — Anthropic/OpenAI/Google AI)
- Nitter: Down (403/503 on all instances — Gemini compensates for market news)

## Incident Log
- 2026-03-26: Company News sections empty in morning briefing. Root cause: DailyBriefing-CompanyNews task was registered after today's 7 AM trigger had already passed (task was new, NumberOfMissedRuns=0, LastRunTime=never). StartWhenAvailable does NOT fire for newly registered tasks, only for runs missed due to machine being off. Fix: ran scanner manually at 12:17 PM, recompiled and re-sent at 12:43 PM. Task will run automatically from 2026-03-27 onward (NextRunTime confirmed 7:00 AM).

## File Map
- Entry: run_pipeline.py (youtube|ai_industry|market_news|company_news|briefing|all|test|usage)
- Scanners: scanners/{youtube,ai_industry,market_news,company_news}_scanner.py
- Synthesis: briefing/synthesizer.py (cliff notes + Brain indexing)
- Status: briefing/status_reader.py (reads project states)
- Compile: briefing/compiler.py (Jinja2 render + email)
- Template: templates/newsletter.html
- Config: config/settings.json, config/llm.py, config/credentials.py
- Output: output/{youtube,ai_industry,market_news,anthropic,openai,google_ai,newsletters}/YYYY-MM-DD.*
- Scheduler XMLs: scheduler/DailyBriefing-CompanyNews.xml

## Scheduled Tasks (Windows Task Scheduler)
| Task | Time | Stage | Status |
|------|------|-------|--------|
| DailyBriefing-YouTube | 6:00 AM | youtube | Active |
| DailyBriefing-AI-Industry | 6:15 AM | ai_industry | Active |
| DailyBriefing-Twitter | 6:30 AM | twitter (market_news alias) | Legacy (still works) |
| DailyBriefing-MarketNews | 6:30 AM | market_news | Active (new) |
| DailyBriefing-GeminiDocs | 7:00 AM | gemini (deprecated noop) | Legacy (harmless) |
| DailyBriefing-CompanyNews | 7:00 AM | company_news | Active (new) |
| DailyBriefing-Compile | 7:30 AM | briefing | Active |

Note: DailyBriefing-Twitter and DailyBriefing-GeminiDocs cannot be deleted (Access Denied).
They are harmless: twitter→market_news alias works, gemini→noop.

## Active Issues
- Nitter instances all down — Gemini grounded search compensates
- DailyBriefing-GeminiDocs/Twitter tasks (legacy, admin-protected) — cannot delete, but harmless
- change_notes.json for KalshiTrader not yet created — compiler falls back to empty list (no banner change notes shown)
