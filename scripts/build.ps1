# Builds dist\Arabic Relay\ with PyInstaller, checks it with the frozen --self-test, then builds
# dist\ArabicRelay-Setup-<version>.exe with Inno Setup. Used locally and by .github/workflows/release.yml.
#   -Smoke   silent install into a temp folder, installed --self-test, silent uninstall (reuses an existing setup).
#            Only where Arabic Relay isn't installed (CI): the smoke uninstall shares the real install's AppId.
param([switch]$Smoke)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$python = if (Test-Path .venv\Scripts\python.exe) { '.venv\Scripts\python.exe' } else { 'python' }
$version = & $python -c "import arabic_relay; print(arabic_relay.__version__)"
$setup = "dist\ArabicRelay-Setup-$version.exe"

function Invoke-SelfTest([string]$exe) {
    $data = Join-Path $env:TEMP "relay-selftest-$(Get-Random)"
    New-Item -ItemType Directory $data | Out-Null
    $env:ARABIC_RELAY_DATA_DIR = $data
    try {
        $run = Start-Process -FilePath $exe -ArgumentList '--self-test' -Wait -PassThru -NoNewWindow `
            -RedirectStandardOutput "$data\selftest.txt" -RedirectStandardError "$data\selftest-error.txt"
        Get-Content "$data\selftest.txt"
        if ($run.ExitCode -ne 0) { Get-Content "$data\selftest-error.txt"; throw "Self-test failed: $exe" }
    } finally {
        Remove-Item Env:ARABIC_RELAY_DATA_DIR
        Remove-Item $data -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if (-not ($Smoke -and (Test-Path $setup))) {
    & $python -m PyInstaller --noconfirm --clean installer\ArabicRelay.spec --distpath dist --workpath build\pyinstaller
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
    Invoke-SelfTest 'dist\Arabic Relay\Arabic Relay.exe'
    $megabytes = [math]::Round((Get-ChildItem 'dist\Arabic Relay' -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
    "Built Arabic Relay ${version}: $megabytes MB"

    $bootstrapper = 'build\MicrosoftEdgeWebview2Setup.exe'
    if (-not (Test-Path $bootstrapper)) {
        New-Item -ItemType Directory build -Force | Out-Null
        Invoke-WebRequest 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile $bootstrapper -UseBasicParsing
    }
    $iscc = @((Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source,
              "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $iscc) { throw 'Inno Setup 6 is not installed: winget install --id JRSoftware.InnoSetup -e --scope user' }
    & $iscc /Q "/DAppVersion=$version" installer\ArabicRelay.iss
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed' }
    $installer = [math]::Round((Get-Item $setup).Length / 1MB)
    "Built $setup`: $installer MB"
}

if ($Smoke) {
    if (Test-Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{B981CB91-79B0-4049-ABF8-C2A11A94560B}_is1') {
        throw 'Arabic Relay is installed here; the smoke uninstall would remove its shortcut and Apps entry. CI runs -Smoke.'
    }
    $folder = Join-Path $env:TEMP "relay-install-$(Get-Random)"
    $dir = Join-Path $folder 'Arabic Relay'
    $userData = Join-Path $env:LOCALAPPDATA 'Arabic Relay'
    if (-not (Test-Path $userData)) { New-Item -ItemType Directory $userData | Out-Null; Set-Content "$userData\smoke.txt" 'smoke' }
    $before = (Get-ChildItem $userData -Force | ForEach-Object Name) -join '|'
    $run = Start-Process $setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=`"$dir`"" -Wait -PassThru
    if ($run.ExitCode -ne 0) { throw "Silent install failed ($($run.ExitCode))" }
    Invoke-SelfTest "$dir\Arabic Relay.exe"
    $run = Start-Process "$dir\unins000.exe" -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
    if ($run.ExitCode -ne 0) { throw "Silent uninstall failed ($($run.ExitCode))" }
    for ($i = 0; $i -lt 60 -and (Test-Path $dir); $i++) { Start-Sleep 1 }  # the uninstaller finishes from a temp copy
    if (Test-Path $dir) { throw 'Uninstall left the install folder behind' }
    $after = (Get-ChildItem $userData -Force | ForEach-Object Name) -join '|'
    if ($after -ne $before) { throw "Silent uninstall changed the user's data folder" }
    Remove-Item $folder -Recurse -Force -ErrorAction SilentlyContinue
    'Smoke test passed: silent install, installed self-test, silent uninstall kept the data folder'
}
