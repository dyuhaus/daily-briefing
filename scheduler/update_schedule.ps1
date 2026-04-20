# update_schedule.ps1
# Updates all DailyBriefing scheduled tasks to deliver by 5:00 AM ET.
#
# Schedule:
#   3:30 AM — All 6 scanners start in parallel
#   4:45 AM — Compile + email delivery
#   5:15 AM — Audio briefing via NotebookLM + Telegram
#
# Run as Administrator:
#   powershell -ExecutionPolicy Bypass -File DailyBriefing\scheduler\update_schedule.ps1

$ErrorActionPreference = "Stop"

Write-Host "=== DailyBriefing Schedule Update ===" -ForegroundColor Cyan
Write-Host "Target: briefing delivered by 5:00 AM ET"
Write-Host ""

# All scanners → 3:30 AM (parallel start)
$scanners = @(
    "DailyBriefing-YouTube",
    "DailyBriefing-AI-Industry",
    "DailyBriefing-Twitter",
    "DailyBriefing-MarketNews",
    "DailyBriefing-CompanyNews",
    "DailyBriefing-GeminiDocs"
)

foreach ($name in $scanners) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "  SKIP: $name (not found)" -ForegroundColor Yellow
        continue
    }
    $task.Triggers[0].StartBoundary = "2026-01-01T03:30:00-04:00"
    Set-ScheduledTask -InputObject $task | Out-Null
    Write-Host "  $name → 3:30 AM ET" -ForegroundColor Green
}

# Compile → 4:45 AM
$compile = Get-ScheduledTask -TaskName "DailyBriefing-Compile" -ErrorAction SilentlyContinue
if ($compile) {
    $compile.Triggers[0].StartBoundary = "2026-01-01T04:45:00-04:00"
    Set-ScheduledTask -InputObject $compile | Out-Null
    Write-Host "  DailyBriefing-Compile → 4:45 AM ET" -ForegroundColor Green
} else {
    Write-Host "  SKIP: DailyBriefing-Compile (not found)" -ForegroundColor Yellow
}

# Audio → 5:15 AM
$audio = Get-ScheduledTask -TaskName "DailyBriefing-Audio" -ErrorAction SilentlyContinue
if ($audio) {
    $audio.Triggers[0].StartBoundary = "2026-01-01T05:15:00-04:00"
    Set-ScheduledTask -InputObject $audio | Out-Null
    Write-Host "  DailyBriefing-Audio → 5:15 AM ET" -ForegroundColor Green
} else {
    Write-Host "  SKIP: DailyBriefing-Audio (not found)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Schedule Updated ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "New timeline:"
Write-Host "  3:30 AM  All scanners start (parallel)"
Write-Host "  4:45 AM  Compile + email"
Write-Host "  5:00 AM  Briefing in inbox"
Write-Host "  5:15 AM  Audio briefing to Telegram"
Write-Host ""

# Verify
Write-Host "Verification:" -ForegroundColor Cyan
Get-ScheduledTask -TaskName "DailyBriefing-*" | ForEach-Object {
    $info = Get-ScheduledTaskInfo -InputObject $_
    [PSCustomObject]@{
        Task = $_.TaskName
        NextRun = $info.NextRunTime
    }
} | Format-Table -AutoSize
