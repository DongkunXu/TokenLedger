# Install dependencies and build the dashboard into frontend\dist (Windows).
. (Join-Path $PSScriptRoot "env.ps1")
Push-Location ([IO.Path]::Combine($Root, "backend"))
try { Invoke-Checked uv sync --quiet } finally { Pop-Location }
Push-Location ([IO.Path]::Combine($Root, "frontend"))
try {
  # Install when missing or when package-lock.json changed since (a pulled update)
  $marker = Join-Path "node_modules" ".package-lock.json"
  if (-not (Test-Path $marker) -or (Get-Item "package-lock.json").LastWriteTime -gt (Get-Item $marker).LastWriteTime) {
    Invoke-Checked npm ci --silent
  }
  Invoke-Checked npm run --silent build
} finally {
  Pop-Location
}
