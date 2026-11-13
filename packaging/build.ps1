# Build MeshLens.exe and the installer locally.
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1 [-Version 0.2.0]
# Needs: the project venv (.venv) and Inno Setup 6 (https://jrsoftware.org/isinfo.php) for the installer step.
param([string]$Version = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

& $py -m pip install -q -e ".[build]"
if ($Version) { $v = & $py packaging\make_assets.py $Version } else { $v = & $py packaging\make_assets.py }
Write-Host "Building MeshLens $v"
& $py -m PyInstaller packaging\meshlens.spec --noconfirm --clean --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) { $iscc = Get-Item "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" -ErrorAction SilentlyContinue }
if (-not $iscc) { Write-Warning "Inno Setup not found: skipping the installer. dist\MeshLens\MeshLens.exe is ready."; exit 0 }
& $iscc.Source "/DAppVersion=$v" packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Write-Host "Installer: dist\MeshLens-$v-setup.exe"
