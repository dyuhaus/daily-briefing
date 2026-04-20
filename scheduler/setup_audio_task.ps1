# setup_audio_task.ps1
# Registers the DailyBriefing-Audio task in Windows Task Scheduler.
# Runs daily at 07:45 AM — after the briefing compiler completes at 07:30 AM.
#
# Run this script as Administrator:
#   Right-click PowerShell -> "Run as administrator"
#   Then: .\setup_audio_task.ps1

$TaskName = "DailyBriefing-Audio"
$XmlPath  = "<workspace>\DailyBriefing\scheduler\DailyBriefing-Audio.xml"

# Remove existing task if present (idempotent re-registration)
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing task: $TaskName"
}

# Register from XML
try {
    Register-ScheduledTask -TaskName $TaskName -Xml (Get-Content $XmlPath -Raw -Encoding Unicode)
    Write-Host "Successfully registered: $TaskName"
    Write-Host "Schedule: Daily at 07:45 AM"
    Write-Host "Command:  python run_pipeline.py audio"
    Write-Host "CWD:      <workspace>\DailyBriefing"
} catch {
    Write-Error "Failed to register task: $_"
    exit 1
}
