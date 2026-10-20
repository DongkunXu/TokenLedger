# Shared environment for the Windows (PowerShell) scripts. Dot-source it, do not run it:
#   . (Join-Path $PSScriptRoot "env.ps1")
# Keeps uv's and npm's caches, Python and temp files inside the project, like env.sh.
# Works in Windows PowerShell 5.1 and PowerShell 7.

$Root = [IO.Path]::GetFullPath([IO.Path]::Combine($PSScriptRoot, ".."))
if (-not (Test-Path ([IO.Path]::Combine($Root, "backend", "pyproject.toml")))) {
  throw "scripts/env.ps1: could not locate the TokenLedger root (got $Root)"
}
$Build = [IO.Path]::Combine($Root, ".build")

$env:TOKENLEDGER_ROOT = $Root
$env:UV_CACHE_DIR = [IO.Path]::Combine($Build, "uv-cache")
$env:UV_PYTHON_INSTALL_DIR = [IO.Path]::Combine($Build, "uv-python")  # if uv has to fetch Python 3.13
$env:UV_PROJECT_ENVIRONMENT = [IO.Path]::Combine($Root, "backend", ".venv")
$env:PYTHONPYCACHEPREFIX = [IO.Path]::Combine($Build, "pycache")
$env:npm_config_cache = [IO.Path]::Combine($Build, "npm-cache")
$env:npm_config_update_notifier = "false"
$env:npm_config_fund = "false"
$env:npm_config_audit = "false"
$env:TMP = [IO.Path]::Combine($Build, "tmp")
$env:TEMP = $env:TMP

foreach ($dir in @($env:TMP, $env:UV_CACHE_DIR, $env:PYTHONPYCACHEPREFIX, $env:npm_config_cache)) {
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  throw "uv is not installed. Install it with: winget install --id=astral-sh.uv -e"
}

function Invoke-Checked {
  # Run a native command and stop the script when it fails.
  param([Parameter(Mandatory = $true)][string]$Exe, [Parameter(ValueFromRemainingArguments = $true)]$Arguments)
  & $Exe @Arguments
  if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}
