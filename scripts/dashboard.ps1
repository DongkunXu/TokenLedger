# Manage the dashboard server (Windows). Same actions as scripts/dashboard.sh:
#   .\scripts\dashboard.ps1 start|open|stop|restart|status|logs|run [--port N]
param([Parameter(Position = 0)][string]$Action = "status", [Parameter(ValueFromRemainingArguments = $true)]$Rest)
. (Join-Path $PSScriptRoot "env.ps1")
if ($null -eq $Rest) { $Rest = @() }
if (@("start", "open", "restart", "run") -contains $Action) {
  if (-not (Test-Path ([IO.Path]::Combine($Root, "frontend", "dist", "index.html")))) {
    & (Join-Path $PSScriptRoot "build.ps1")
  }
}
$cli = Join-Path $PSScriptRoot "tokenledger.ps1"
if ($Action -eq "run") {
  & $cli serve --open @Rest
} else {
  & $cli dashboard $Action @Rest
}
exit $LASTEXITCODE
