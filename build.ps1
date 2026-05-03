$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

pyinstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name "vk-helper" `
  --paths . `
  --collect-all PySide6 `
  app/main.py
