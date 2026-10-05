# Build dist\cbapick4me.exe on Windows (PyInstaller cannot cross-compile from Linux).
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m venv .venv-win
.\.venv-win\Scripts\python -m pip install --upgrade pip
.\.venv-win\Scripts\pip install ".[desktop,dev]"

.\.venv-win\Scripts\nicegui-pack --onefile --windowed --name cbapick4me packaging\launch.py

Write-Host "Built dist\cbapick4me.exe"
