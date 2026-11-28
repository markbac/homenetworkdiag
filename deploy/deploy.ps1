<#
Deploy MeshLens to a Linux Docker host over SSH (default 10.0.11.195).
Copies the source, builds the image there and (re)starts it. Data in <RemoteDir>/deploy/data is kept.

    .\deploy\deploy.ps1 -Ref main        # deploy a branch, tag or commit (what is committed only)
    .\deploy\deploy.ps1                  # deploy the working tree, including uncommitted changes
    .\deploy\deploy.ps1 -Ref v0.9.4 -HostName 10.0.11.195 -RemoteDir meshlens
#>
param(
    [string]$Ref = "",
    [string]$HostName = "10.0.11.195",
    [string]$RemoteDir = "meshlens"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tgz = Join-Path ([IO.Path]::GetTempPath()) "meshlens-src.tgz"

$files = "Dockerfile", ".dockerignore", "pyproject.toml", "README.md", "LICENSE", "meshlens", "deploy"
Push-Location $root
try {
  if ($Ref) {
    git archive --format=tar.gz -o $tgz $Ref @files
    if ($LASTEXITCODE) { throw "git archive $Ref failed" }
    Write-Host "Deploying $Ref ($(git rev-parse --short $Ref))"
  } else {
    tar -czf $tgz --exclude=.git --exclude=.venv --exclude=build --exclude=dist --exclude=*.egg-info `
        --exclude=__pycache__ --exclude=.pytest_cache --exclude=deploy/data `
        Dockerfile .dockerignore pyproject.toml README.md LICENSE meshlens deploy
    if ($LASTEXITCODE) { throw "tar failed" }
  }
} finally { Pop-Location }

ssh $HostName "mkdir -p ~/$RemoteDir/deploy/data"
Push-Location (Split-Path $tgz)   # scp reads "C:\..." as a host name, so copy by relative path
try { scp -O (Split-Path -Leaf $tgz) "${HostName}:~/$RemoteDir/meshlens-src.tgz" } finally { Pop-Location }
if ($LASTEXITCODE) { throw "scp failed" }
ssh $HostName "cd ~/$RemoteDir && rm -rf meshlens && tar -xzf meshlens-src.tgz && rm meshlens-src.tgz && cd deploy && docker compose up -d --build && docker compose ps"
if ($LASTEXITCODE) { throw "remote build failed" }
Write-Host "MeshLens: http://${HostName}:8787/"
