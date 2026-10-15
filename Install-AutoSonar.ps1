# Installs AutoSonar for the current user. No administrator account is required.
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

Write-Host "Building AutoSonar..."
& $Python -m pip install --disable-pip-version-check -r (Join-Path $ProjectRoot "requirements.txt") pyinstaller | Out-Host
$Icon = Join-Path $ProjectRoot "autosonar\icon.ico"
& $Python -m PyInstaller --noconfirm --clean --windowed --name AutoSonar --icon $Icon --add-data "$Icon;." --paths $ProjectRoot --hidden-import hid --hidden-import imageio --hidden-import imageio_ffmpeg --hidden-import winrt.windows.foundation --hidden-import winrt.windows.foundation.collections --hidden-import winrt.windows.media.control --collect-all pystray --collect-all imageio_ffmpeg --collect-submodules PIL (Join-Path $ProjectRoot "autosonar\__main__.py")
if ($LASTEXITCODE -ne 0) {
    throw "The AutoSonar build failed."
}

$Built = Join-Path $ProjectRoot "dist\AutoSonar"
$ExeName = "AutoSonar.exe"
if (-not (Test-Path (Join-Path $Built $ExeName))) {
    throw "AutoSonar.exe was not created."
}

Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^(AutoSonar|pythonw|pythonw3)' -and $_.CommandLine -match 'autosonar|AutoSonar'
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1

$Destination = Join-Path $env:LOCALAPPDATA "Programs\AutoSonar"
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
Copy-Item -Path (Join-Path $Built "*") -Destination $Destination -Recurse -Force

$InstalledExe = Join-Path $Destination $ExeName

$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$ShortcutPath = Join-Path $StartMenu "AutoSonar.lnk"
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $InstalledExe
$Shortcut.WorkingDirectory = $Destination
$Shortcut.Description = "Switch SteelSeries Sonar presets when a game starts"
$Shortcut.Save()

$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
New-Item -Path $RunKey -Force | Out-Null
New-ItemProperty -Path $RunKey -Name "AutoSonar" -PropertyType String -Value "`"$InstalledExe`" --startup" -Force | Out-Null

& $Python -c @"
import json
from pathlib import Path
paths = [
    Path(r'$env:APPDATA') / 'AutoSonar' / 'mappings.json',
    Path.home() / 'AutoSonar' / 'mappings.json',
]
seen = set()
for path in paths:
    if not path.is_file() or path.resolve() in seen:
        continue
    seen.add(path.resolve())
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    data['start_with_windows'] = True
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
"@

Write-Host "Installed to $Destination"
Write-Host "AutoSonar will start with Windows and look for your games."
Start-Process -FilePath $InstalledExe -WorkingDirectory $Destination
