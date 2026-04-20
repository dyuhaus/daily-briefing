# Daily Briefing — Windows Task Scheduler Setup
# Run this script as Administrator to create the scheduled tasks
# Schedule: YouTube 6:00 AM, AIIndustry 6:15 AM, MarketNews 6:30 AM, CompanyNews 7:00 AM, Briefing 7:30 AM (Eastern)
# Updated: 2026-03-26 (replaced deprecated 'twitter'/'gemini' stages with current stage names)

$PipelinePath = "<workspace>\DailyBriefing\run_pipeline.py"
$PythonPath = (Get-Command python).Source

$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable

# Task 1: YouTube Scanner at 6:00 AM
$Action1 = New-ScheduledTaskAction -Execute $PythonPath -Argument "$PipelinePath youtube" -WorkingDirectory "<workspace>\DailyBriefing"
$Trigger1 = New-ScheduledTaskTrigger -Daily -At "06:00AM"
Register-ScheduledTask -TaskName "DailyBriefing-YouTube" -Action $Action1 -Trigger $Trigger1 -Settings $Settings -Description "YouTube Claude Code Scanner (Daily Briefing Pipeline)" -Force

# Task 2: AI Industry News Scanner at 6:15 AM
$Action2 = New-ScheduledTaskAction -Execute $PythonPath -Argument "$PipelinePath ai_industry" -WorkingDirectory "<workspace>\DailyBriefing"
$Trigger2 = New-ScheduledTaskTrigger -Daily -At "06:15AM"
Register-ScheduledTask -TaskName "DailyBriefing-AIIndustry" -Action $Action2 -Trigger $Trigger2 -Settings $Settings -Description "AI Industry News Scanner — broad AI developments (Daily Briefing Pipeline)" -Force

# Task 3: Market News Scanner at 6:30 AM
$Action3 = New-ScheduledTaskAction -Execute $PythonPath -Argument "$PipelinePath market_news" -WorkingDirectory "<workspace>\DailyBriefing"
$Trigger3 = New-ScheduledTaskTrigger -Daily -At "06:30AM"
Register-ScheduledTask -TaskName "DailyBriefing-MarketNews" -Action $Action3 -Trigger $Trigger3 -Settings $Settings -Description "Market News Scanner — macro markets + algo trading (Daily Briefing Pipeline)" -Force

# Task 4: Company News Scanner at 7:00 AM (Anthropic, OpenAI, Google AI)
$Action4 = New-ScheduledTaskAction -Execute $PythonPath -Argument "$PipelinePath company_news" -WorkingDirectory "<workspace>\DailyBriefing"
$Trigger4 = New-ScheduledTaskTrigger -Daily -At "07:00AM"
Register-ScheduledTask -TaskName "DailyBriefing-CompanyNews" -Action $Action4 -Trigger $Trigger4 -Settings $Settings -Description "Company News Scanner — Anthropic, OpenAI, Google AI (Daily Briefing Pipeline)" -Force

# Task 5: Briefing Compiler at 7:30 AM
$Action5 = New-ScheduledTaskAction -Execute $PythonPath -Argument "$PipelinePath briefing" -WorkingDirectory "<workspace>\DailyBriefing"
$Trigger5 = New-ScheduledTaskTrigger -Daily -At "07:30AM"
Register-ScheduledTask -TaskName "DailyBriefing-Compile" -Action $Action5 -Trigger $Trigger5 -Settings $Settings -Description "Daily Briefing Newsletter Compiler & Delivery" -Force

# Remove deprecated tasks if they still exist
foreach ($oldTask in @("DailyBriefing-Twitter", "DailyBriefing-GeminiDocs")) {
    if (Get-ScheduledTask -TaskName $oldTask -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $oldTask -Confirm:$false
        Write-Host "Removed deprecated task: $oldTask" -ForegroundColor Yellow
    }
}

# Task 6: KalshiTrader Price Collector at 9:00 AM
$CollectorPath = "<workspace>\KalshiTrader\collector\price_collector.py"
$Action6 = New-ScheduledTaskAction -Execute $PythonPath -Argument "-m collector.price_collector" -WorkingDirectory "<workspace>\KalshiTrader"
$Trigger6 = New-ScheduledTaskTrigger -Daily -At "09:00AM"
$Settings6 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable
Register-ScheduledTask -TaskName "KalshiTrader-MorningCollect" -Action $Action6 -Trigger $Trigger6 -Settings $Settings6 -Description "KalshiTrader morning price collection seed" -Force

# Task 7: Sports Betting Scanner at 12:00 PM
$BettingScannerPath = "<workspace>\SportsBettingSwarm\production\daily_scanner.py"
$Action7 = New-ScheduledTaskAction -Execute $PythonPath -Argument "$BettingScannerPath" -WorkingDirectory "<workspace>\SportsBettingSwarm"
$Trigger7 = New-ScheduledTaskTrigger -Daily -At "12:00PM"
Register-ScheduledTask -TaskName "SportsBetting-DailyScan" -Action $Action7 -Trigger $Trigger7 -Settings $Settings -Description "Daily Kalshi sports betting scanner - scans markets and places favorable bets" -Force

Write-Host ""
Write-Host "Scheduled tasks created:" -ForegroundColor Green
Write-Host "  6:00 AM  - DailyBriefing-YouTube    (yt-dlp + transcript extraction)"
Write-Host "  6:15 AM  - DailyBriefing-AIIndustry (Gemini grounded search — broad AI)"
Write-Host "  6:30 AM  - DailyBriefing-MarketNews (Gemini grounded search — markets + algo trading)"
Write-Host "  7:00 AM  - DailyBriefing-CompanyNews (Anthropic, OpenAI, Google AI)"
Write-Host "  7:30 AM  - DailyBriefing-Compile     (aggregate + render + email + save HTML)"
Write-Host "  9:00 AM  - KalshiTrader-MorningCollect (seed price data for timing analysis)"
Write-Host "  12:00 PM - SportsBetting-DailyScan   (Kalshi market scan + bet placement)"
Write-Host ""
Write-Host "Newsletter delivered by 8:00 AM Eastern." -ForegroundColor Cyan
Write-Host "Betting scan runs daily at noon." -ForegroundColor Cyan
