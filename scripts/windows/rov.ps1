# Windows equivalent of the Makefile:  .\rov.cmd <command>
#   setup          Python venv + packages (no admin, no system packages)
#   test           protocol, mixer and end-to-end sim tests
#   run            the app (Link menu: TETHER = FXTI, WI-FI = bench)
#   run-sim        whole system on this PC, no hardware
#   gamepad-test   show raw axis/button numbers
#   rc-setup       calibrate an RC radio (RadioMaster, ...) connected by USB
#   network        FXTI adapter -> 192.168.2.1/24 + firewall rule (run as Administrator)
#   update         get the latest version (git pull + packages) - for copies installed from source
#   build          packaged app: dist/ROV-Control-windows.zip (no Python needed to run it)
#   shortcut       "ROV Control" icon on the Desktop
param([Parameter(Position = 0)][string]$Command = "help", [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Venv = Join-Path $Root ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$env:PYTHONNOUSERSITE = "1"          # same behaviour on every PC: ignore user-site packages
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue

function Say($m) { Write-Host "[rov] $m" -ForegroundColor Cyan }
function Die($m) { Write-Host "[rov] $m" -ForegroundColor Red; exit 1 }
function Need-Venv { if (-not (Test-Path $Py)) { Die "Not set up yet. Run:  .\rov.cmd setup" } }
function Cfg($key) { (& $Py (Join-Path $Root "config\cfg.py") $key).Trim() }

# Python 3.10-3.13 (PySide6 6.8.3 / pygame 2.6.1 wheels). Prefers the py launcher, then python on PATH.
function Find-Python {
    $cands = @()
    foreach ($v in "3.13", "3.12", "3.11", "3.10") { $cands += ,@("py", "-$v") }
    $cands += ,@("python")
    foreach ($c in $cands) {
        $exe = $c[0]; $a = @(); if ($c.Count -gt 1) { $a = $c[1..($c.Count - 1)] }
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $ver = (& $exe @a -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null)
            if ($LASTEXITCODE -eq 0 -and $ver -match '^3\.(10|11|12|13)$') { return ,(@($exe) + $a) }
        } catch { }
    }
    return $null
}

function Is-Admin {
    ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

switch ($Command) {
    "setup" {
        if (-not (Test-Path $Py)) {
            $p = Find-Python
            if (-not $p) { Die "Python 3.10-3.13 not found. Install from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') or: winget install Python.Python.3.12" }
            Say "Creating .venv with $($p -join ' ')"
            $exe = $p[0]; $a = @(); if ($p.Count -gt 1) { $a = $p[1..($p.Count - 1)] }
            & $exe @a -m venv $Venv
            if ($LASTEXITCODE -ne 0) { Die "venv creation failed" }
        }
        Say "Installing packages (PySide6 is ~200 MB, first time takes a few minutes)"
        & $Py -m pip install --upgrade pip
        & $Py -m pip install -r (Join-Path $Root "requirements.txt")
        if ($LASTEXITCODE -ne 0) { Die "pip install failed" }
        & $Py -c "import PySide6, pygame, av, serial, yaml; print('OK: PySide6', PySide6.__version__, '| pygame', pygame.version.ver, '| PyAV', av.__version__)"
        if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
            Write-Host "[rov] ssh not found: needed only for the Reset / Flash / Restart-camera buttons. Settings > Apps > Optional features > OpenSSH Client." -ForegroundColor Yellow
        }
        Say "Ready. Try:  .\rov.cmd test   then   .\rov.cmd run-sim"
    }
    "test" {
        Need-Venv
        & $Py -m pytest -q (Join-Path $Root "protocol") (Join-Path $Root "topside") (Join-Path $Root "sim") (Join-Path $Root "firmware") @Rest
        exit $LASTEXITCODE
    }
    "run" { Need-Venv; & $Py (Join-Path $Root "topside\app.py") @Rest; exit $LASTEXITCODE }
    "run-sim" { Need-Venv; & $Py (Join-Path $Root "sim\run_sim.py") @Rest; exit $LASTEXITCODE }
    "gamepad-test" { Need-Venv; & $Py (Join-Path $Root "scripts\gamepad_test.py") @Rest; exit $LASTEXITCODE }
    "rc-setup" { Need-Venv; & $Py (Join-Path $Root "topside\app.py") --rc-setup; exit $LASTEXITCODE }
    "update" {
        # Source installs only. The packaged app updates itself (button in the app).
        Need-Venv
        Set-Location $Root
        if (-not (Test-Path (Join-Path $Root ".git"))) {
            Die "This copy was not cloned with git, so it can't pull updates. Download the newest ZIP (or the packaged app) from the project's Releases page, or: git clone <repo-url> and run Install.bat there."
        }
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Die "git not found. Install it: winget install Git.Git" }
        git pull --ff-only
        if ($LASTEXITCODE -ne 0) { Die "git pull failed (you have local changes? run 'git status'). Nothing was changed." }
        & $Py -m pip install -r (Join-Path $Root "requirements.txt")
        if ($LASTEXITCODE -ne 0) { Die "pip install failed" }
        $v = & $Py -c "import sys; sys.path.insert(0, 'topside'); import version; print(version.__version__)"
        Say "Updated to v$v"
    }
    "network" {
        Need-Venv
        if (-not (Is-Admin)) { Die "Run this in a PowerShell / Terminal opened with 'Run as administrator':  .\rov.cmd network" }
        $ip = Cfg "network.laptop_ip"; $cidr = Cfg "network.laptop_cidr"; $pi = Cfg "network.pi_ip"
        $prefix = [int]($cidr.Split("/")[1])
        $iface = if ($Rest.Count -gt 0) { $Rest[0] } else { "" }
        if (-not $iface) {
            Say "Network adapters (the FXTI shows up as an Ethernet / USB Ethernet adapter):"
            Get-NetAdapter -Physical | Sort-Object Status | Format-Table Name, InterfaceDescription, Status, LinkSpeed -AutoSize
            $iface = Read-Host "Adapter name for the tether"
        }
        if (-not $iface) { Die "no adapter given" }
        if (-not (Get-NetAdapter -Name $iface -ErrorAction SilentlyContinue)) { Die "no adapter called '$iface'" }
        # static address, NO gateway/DNS: Wi-Fi keeps the internet
        Get-NetIPAddress -InterfaceAlias $iface -AddressFamily IPv4 -ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false
        Set-NetIPInterface -InterfaceAlias $iface -Dhcp Disabled
        New-NetIPAddress -InterfaceAlias $iface -IPAddress $ip -PrefixLength $prefix | Out-Null
        Say "$iface = $ip/$prefix"
        # The tether adapter is usually classed as a 'Public' network, where Windows drops incoming UDP
        # (telemetry + video would never arrive). Allow just those ports.
        $ports = @((Cfg "network.telemetry_port"), (Cfg "network.video_port"))
        Get-NetFirewallRule -DisplayName "ROV Control" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        New-NetFirewallRule -DisplayName "ROV Control" -Direction Inbound -Protocol UDP -LocalPort $ports -Action Allow -Profile Any | Out-Null
        Say "Firewall: inbound UDP $($ports -join ', ') allowed ('ROV Control' rule)"
        Say "Pinging the Pi at $pi..."
        ping -n 3 -w 1000 $pi
        if ($LASTEXITCODE -ne 0) { Write-Host "[rov] No reply yet: is the Pi powered and set to $pi? (see README)" -ForegroundColor Yellow }
    }
    "build" {
        # One-folder app: dist\ROV-Control\ROV Control.exe + a zip. Members need no Python.
        Need-Venv
        Say "Installing the packager (PyInstaller)"
        & $Py -m pip install -r (Join-Path $Root "requirements-build.txt")
        if ($LASTEXITCODE -ne 0) { Die "pip install failed" }
        Set-Location $Root
        Remove-Item -Recurse -Force (Join-Path $Root "build"), (Join-Path $Root "dist") -ErrorAction SilentlyContinue
        Say "Building (a few minutes)"
        & $Py -m PyInstaller --noconfirm --clean --windowed --name "ROV Control" `
            --paths protocol --paths config --paths topside `
            --add-data "config\system.yaml;config" `
            --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets `
            --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQml `
            --exclude-module tkinter --exclude-module pytest `
            topside\app.py
        if ($LASTEXITCODE -ne 0) { Die "PyInstaller failed" }
        $out = Join-Path $Root "dist\ROV Control"
        New-Item -ItemType Directory -Force (Join-Path $out "firmware\prebuilt") | Out-Null
        Copy-Item (Join-Path $Root "firmware\prebuilt\*.bin") (Join-Path $out "firmware\prebuilt")
        Copy-Item (Join-Path $Root "README.md"), (Join-Path $Root "protocol\PROTOCOL.md") $out
        $zip = Join-Path $Root "dist\ROV-Control-windows.zip"
        Compress-Archive -Path $out -DestinationPath $zip -Force
        Say "Done: $zip  ($([math]::Round((Get-Item $zip).Length / 1MB)) MB). Unzip anywhere and run 'ROV Control.exe'."
    }
    "shortcut" {
        $lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "ROV Control.lnk"
        $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
        $s.TargetPath = Join-Path $Root "rov.cmd"; $s.Arguments = "run"; $s.WorkingDirectory = $Root
        $s.WindowStyle = 7; $s.Description = "ROV Control"; $s.Save()
        Say "Created $lnk"
    }
    default {
        Get-Content $PSCommandPath -TotalCount 11 | Select-Object -Skip 1 | ForEach-Object { $_ -replace '^# ?', '' }
    }
}
