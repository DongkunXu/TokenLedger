# Install dependencies and build the dashboard into frontend\dist (Windows).
. (Join-Path $PSScriptRoot "env.ps1")
Push-Location ([IO.Path]::Combine($Root, "backend"))
try { Invoke-Checked uv sync --quiet } finally { Pop-Location }
Push-Location ([IO.Path]::Combine($Root, "frontend"))
try {
  if (-not (Test-Path "node_modules")) { Invoke-Checked npm ci --silent }
  Invoke-Checked npm run --silent build
} finally {
  Pop-Location
}
