# Installs the prebuilt AutoSonar folder next to this script. No Python or administrator account is required.
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Built = Join-Path $Here "AutoSonar"
$ExeName = "AutoSonar.exe"
$SourceExe = Join-Path $Built $ExeName
if (-not (Test-Path $SourceExe)) {
    throw "AutoSonar.exe was not found. Extract the whole zip, then run Install.bat from that folder."
}

Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "AutoSonar.exe"
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

Write-Host "Installed to $Destination"
Write-Host "AutoSonar will start with Windows and look for your games."
Start-Process -FilePath $InstalledExe -WorkingDirectory $Destination
