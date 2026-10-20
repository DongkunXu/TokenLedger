# Keep the sync hub running on a Windows machine: Task Scheduler starts it at logon and
# restarts it if it stops. Run `tokenledger sync hub-init` first.
#   .\scripts\install-hub-task.ps1               install (or update)
#   .\scripts\install-hub-task.ps1 -Firewall     also allow the sync port through Windows Firewall
#                                                on private networks (needs an elevated PowerShell)
#   .\scripts\install-hub-task.ps1 -Remove       remove the task and the firewall rules
param([switch]$Remove, [switch]$Firewall, [int]$Port = 8766)
$ErrorActionPreference = "Stop"
$Name = "TokenLedger hub"
$RuleName = "TokenLedger hub"

if ($Remove) {
  Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
  Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
  Write-Output "removed scheduled task '$Name'"
  exit 0
}

$shell = (Get-Process -Id $PID).Path
$script = Join-Path $PSScriptRoot "tokenledger.ps1"
$action = New-ScheduledTaskAction -Execute $shell -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" hub serve"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings `
  -Description "TokenLedger: sync hub (accepts pushes from the other machines)" -Force | Out-Null
Start-ScheduledTask -TaskName $Name
Write-Output "installed and started scheduled task '$Name'"

if ($Firewall) {
  Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
  foreach ($protocol in @("TCP", "UDP")) {
    New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Protocol $protocol -LocalPort $Port `
      -Action Allow -Profile Private | Out-Null
  }
  Write-Output "allowed TCP and UDP port $Port on private networks"
}
