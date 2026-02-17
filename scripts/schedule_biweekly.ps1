# PowerShell script to schedule the bi-weekly portfolio run (Sundays, every 2 weeks)
# Run this script as Administrator

$batPath = Join-Path $PSScriptRoot "schedule_biweekly.bat"
$workingDir = Split-Path $PSScriptRoot -Parent

Write-Host "Scheduling bi-weekly portfolio run (Sundays, every 2 weeks)..."
Write-Host "Batch file: $batPath"
Write-Host "Working directory: $workingDir"
Write-Host ""

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$batPath`"" `
    -WorkingDirectory $workingDir

# Every 2 weeks on Monday at 8:00 AM
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -WeeksInterval 2 -At "8:00AM"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

try {
    Register-ScheduledTask `
        -TaskName "ASTRO Biweekly Portfolio Run" `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description "Runs bi-weekly portfolio at 8:00 AM every 2nd Sunday. Saves to data/runs_biweekly, writes trades CSV, tracks performance separately. Does NOT submit to MAYS." `
        -User $env:USERNAME `
        -RunLevel Highest

    Write-Host "[SUCCESS] Bi-weekly task scheduled successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Task Name: ASTRO Biweekly Portfolio Run"
    Write-Host "Schedule: Every 2 weeks on Sunday at 8:00 AM"
    Write-Host ""
    Write-Host "To view: Get-ScheduledTask -TaskName 'ASTRO Biweekly Portfolio Run'"
    Write-Host "To test: Start-ScheduledTask -TaskName 'ASTRO Biweekly Portfolio Run'"
    Write-Host "To remove: Unregister-ScheduledTask -TaskName 'ASTRO Biweekly Portfolio Run' -Confirm:`$false"
} catch {
    Write-Host "[ERROR] Failed to schedule task: $_" -ForegroundColor Red
    Write-Host "Run PowerShell as Administrator."
}
