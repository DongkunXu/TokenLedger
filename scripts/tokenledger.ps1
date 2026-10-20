# Run the tokenledger CLI inside the project environment (Windows):
#   .\scripts\tokenledger.ps1 <command> [...]      e.g. capture, status, sync login https://...
# If scripts are blocked: powershell -ExecutionPolicy Bypass -File scripts\tokenledger.ps1 capture
. (Join-Path $PSScriptRoot "env.ps1")
Push-Location ([IO.Path]::Combine($Root, "backend"))
try {
  uv sync --quiet --frozen
  if ($LASTEXITCODE -ne 0) { Invoke-Checked uv sync --quiet }
  uv run --quiet --no-sync tokenledger @args
  $code = $LASTEXITCODE
} finally {
  Pop-Location
}
exit $code
