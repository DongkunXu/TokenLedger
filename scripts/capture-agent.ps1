# Entry point for the scheduled capture on Windows (install-agent.ps1). Keeps only the last run's output.
. (Join-Path $PSScriptRoot "env.ps1")
$logs = [IO.Path]::Combine($Root, ".build", "logs")
New-Item -ItemType Directory -Force -Path $logs | Out-Null
& (Join-Path $PSScriptRoot "tokenledger.ps1") capture *> ([IO.Path]::Combine($logs, "capture-last.log"))
