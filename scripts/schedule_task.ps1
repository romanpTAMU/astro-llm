# PowerShell script to schedule the daily portfolio generation task
# Run this script as Administrator

$batPath = Join-Path $PSScriptRoot "schedule_daily.bat"
$workingDir = Split-Path $PSScriptRoot -Parent

Write-Host "Scheduling daily portfolio generation task..."
Write-Host "Batch file: $batPath"
Write-Host "Working directory: $workingDir"
Write-Host ""

# Create the scheduled task action
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$batPath`"" `
    -WorkingDirectory $workingDir

# Create the trigger (daily at 7:30 AM)
$trigger = New-ScheduledTaskTrigger -Daily -At "7:30AM"

# Create task settings
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

# Register the task
try {
    Register-ScheduledTask `
        -TaskName "ASTRO Daily Portfolio Generation" `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description "Runs daily portfolio generation at 7:30 AM (only on trading days, submits to MAYS AI, sends email report)" `
        -User $env:USERNAME `
        -RunLevel Highest
    
    Write-Host "[SUCCESS] Task scheduled successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Task Name: ASTRO Daily Portfolio Generation"
    Write-Host "Schedule: Daily at 7:30 AM"
    Write-Host ""
    Write-Host "To view the task, run: Get-ScheduledTask -TaskName 'ASTRO Daily Portfolio Generation'"
    Write-Host "To test the task, run: Start-ScheduledTask -TaskName 'ASTRO Daily Portfolio Generation'"
    Write-Host "To remove the task, run: Unregister-ScheduledTask -TaskName 'ASTRO Daily Portfolio Generation' -Confirm:`$false"
} catch {
    Write-Host "[ERROR] Failed to schedule task: $_" -ForegroundColor Red
    Write-Host ""
    Write-Host "Make sure you're running PowerShell as Administrator!"
}

