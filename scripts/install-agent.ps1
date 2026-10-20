# Scheduled capture on Windows: Task Scheduler runs a capture at 09:00 and 21:00 (and as
# soon as possible after a missed run). On a node, every capture also pushes to the hub.
#   .\scripts\install-agent.ps1            install (or update)
#   .\scripts\install-agent.ps1 -Remove    remove; the ledger in data\ is left untouched
param([switch]$Remove)
$ErrorActionPreference = "Stop"
$Name = "TokenLedger capture"

if ($Remove) {
  Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
  Write-Output "removed scheduled task '$Name'"
  exit 0
}

$shell = (Get-Process -Id $PID).Path  # the PowerShell running this script
$script = Join-Path $PSScriptRoot "capture-agent.ps1"
$action = New-ScheduledTaskAction -Execute $shell -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$triggers = @((New-ScheduledTaskTrigger -Daily -At "09:00"), (New-ScheduledTaskTrigger -Daily -At "21:00"))
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName $Name -Action $action -Trigger $triggers -Settings $settings `
  -Description "TokenLedger: capture AI usage (and push to the hub on a node)" -Force | Out-Null
Write-Output "installed scheduled task '$Name' (09:00 and 21:00)"
