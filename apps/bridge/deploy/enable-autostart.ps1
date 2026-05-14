# Brikko Bridge — install / remove auto-startup at user login.
#
# Creates a shortcut to start-bridge.bat in the per-user Startup folder
# (%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup). Windows runs
# everything there when the user logs in. No admin / scheduled task / NSSM
# service needed; the bridge only needs to live as long as the user is
# logged in anyway (claude.exe inherits the OAuth from the user session).
#
# Usage:
#   pwsh .\enable-autostart.ps1            # install
#   pwsh .\enable-autostart.ps1 -Remove    # remove

[CmdletBinding()]
param (
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

$bat = (Resolve-Path (Join-Path $PSScriptRoot "start-bridge.bat")).Path
$startup = [Environment]::GetFolderPath("Startup")
$lnkPath = Join-Path $startup "Brikko Bridge.lnk"

if ($Remove) {
    if (Test-Path $lnkPath) {
        Remove-Item $lnkPath -Force
        Write-Host "✓ Removed $lnkPath"
    }
    else {
        Write-Host "(nothing to remove — shortcut not present)"
    }
    return
}

if (-not (Test-Path $bat)) {
    throw "start-bridge.bat not found at $bat"
}

$WScriptShell = New-Object -ComObject WScript.Shell
$shortcut = $WScriptShell.CreateShortcut($lnkPath)
$shortcut.TargetPath = $bat
$shortcut.WorkingDirectory = (Split-Path $bat -Parent)
$shortcut.WindowStyle = 7  # 7 = Minimized — supervisor logs to file anyway
$shortcut.Description = "Brikko Bridge — Telegram → Claude Code"
# Hide the icon attribute so it doesn't clutter the desktop later if the
# user happens to peek inside the Startup folder.
$shortcut.IconLocation = "$bat,0"
$shortcut.Save()

Write-Host "✓ Installed: $lnkPath"
Write-Host "  Target:  $bat"
Write-Host ""
Write-Host "On next login the bridge supervisor will start automatically."
Write-Host "To disable: pwsh .\enable-autostart.ps1 -Remove"
