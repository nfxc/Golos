# Собираем папку с exe и кладём модели рядом, чтобы они не раздували архив.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Get-Process Golos -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

if (!(Test-Path "models\gigaam-v3-e2e-ctc\v3_e2e_ctc.int8.onnx") -or !(Test-Path "models\faster-whisper-small\model.bin")) {
    python fetch_models.py
}

if (!(Test-Path "models\gigaam-v3-e2e-ctc\v3_e2e_ctc.int8.onnx") -or !(Test-Path "models\faster-whisper-small\model.bin")) {
    throw "Не скачались модели"
}

if (Test-Path "dist\Golos") {
    Remove-Item "dist\Golos" -Recurse -Force
}

cmd /c "python -m PyInstaller --noconfirm --clean Golos.spec 2>&1"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed"
}

Copy-Item "models" "dist\Golos\models" -Recurse -Force

Write-Host ""
Write-Host "Done: dist\Golos\Golos.exe"
