$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$manifestRepo = Join-Path $projectRoot "third_party\ludusavi-manifest"

if (-not (Test-Path -LiteralPath (Join-Path $manifestRepo ".git"))) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $manifestRepo) -Force | Out-Null
    git clone --depth 1 https://github.com/mtkennerly/ludusavi-manifest.git $manifestRepo
} else {
    git -C $manifestRepo pull --ff-only
}
if ($LASTEXITCODE -ne 0) {
    throw "Failed to update Ludusavi manifest"
}

$revision = git -C $manifestRepo rev-parse --short HEAD
Write-Output "Ludusavi manifest updated: $revision"
