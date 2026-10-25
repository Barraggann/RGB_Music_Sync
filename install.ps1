# Installs the Python packages and (optionally) makes the sync start with Windows.
# Run from this folder:  powershell -ExecutionPolicy Bypass -File install.ps1
param([switch]$NoAutostart)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Write-Error "Python not found. Install it from https://www.python.org/downloads/ (keep 'py launcher' checked)."
    exit 1
}
py -m pip install -r "$here\requirements.txt"
if (-not $?) { exit 1 }

if (-not (Test-Path "$env:ProgramFiles\OpenRGB\OpenRGB.exe")) {
    Write-Warning "OpenRGB not found. Install it from https://openrgb.org (or: winget install OpenRGB.OpenRGB)."
}

if (-not $NoAutostart) {
    $startup = [Environment]::GetFolderPath('Startup')
    $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut("$startup\MusicRGB.lnk")
    $lnk.TargetPath = "$here\start_music_rgb.vbs"
    $lnk.WorkingDirectory = $here
    $lnk.Save()
    Write-Host "Autostart enabled: $startup\MusicRGB.lnk (delete it to turn autostart off)."
}

Write-Host "Done. Start it now with:  wscript `"$here\start_music_rgb.vbs`""
