# One-click IrisView simulator launcher (WSLg, no Docker required).
#   sim.bat                 -> start backend (if needed), build package (if stale), open simulator
#   powershell -File tools\start-sim.ps1 -CheckOnly
#   powershell -File tools\start-sim.ps1 -NoBackend -Port 28045
#   powershell -File tools\start-sim.ps1 -ForceBuild
#
# Why WSL instead of Docker: the stock Docker path assumes a distro named
# "Ubuntu"; on the competition machine the developer kit lives in a dedicated
# distro (IrisView) with WSLg + Node 24 already installed. This script finds
# whichever distro has the kit and runs the simulator there. Docker remains
# available via tools\kit-docker.ps1 for CI/containers.
#
# Keep this file ASCII-only: Windows PowerShell 5.1 reads no-BOM UTF-8 as ANSI.

param(
    [int]$Port = 28045,
    [switch]$NoBackend,
    [switch]$CheckOnly,
    [switch]$ForceBuild
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Say($msg, $color = 'Gray') { Write-Host $msg -ForegroundColor $color }
function Fail($msg) { Say $msg 'Red'; exit 1 }

function To-WslPath([string]$p) {
    $q = $p.Replace('\', '/')
    if ($q -match '^([A-Za-z]):(.*)$') {
        return '/mnt/' + $Matches[1].ToLower() + $Matches[2]
    }
    return $q
}

# ---------- 1. find a WSL distro that has the IrisView developer kit ----------
Say '[..] looking for the IrisView developer kit in WSL...' 'Yellow'
$distros = @()
try {
    $distros = @(& wsl -l -q 2>$null | ForEach-Object {
        ($_ -replace "`0", '').Trim()
    } | Where-Object { $_ })
} catch {
    Fail '[!!] WSL is not available. Install/enable WSL2 first.'
}
if (-not $distros.Count) { Fail '[!!] No WSL distro found. Open the IrisView installer first.' }

$kitCandidates = @(
    '/home/iris/IrisView/Developer-Kit',
    '/home/ubuntu/IrisView/Developer-Kit',
    '/opt/IrisView/Developer-Kit'
)
$distro = $null
$kit = $null
foreach ($d in $distros) {
    foreach ($k in $kitCandidates) {
        & wsl -d $d -e test -f "$k/sdk/fusionui-app-sdk/fusionui_sdk.py" 2>$null
        if ($LASTEXITCODE -eq 0) { $distro = $d; $kit = $k; break }
    }
    if ($distro) { break }
}
if (-not $distro) {
    Say '[..] no IrisView kit in WSL; trying Docker + WSLg fallback...' 'Yellow'

    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $dockerVer = (& docker info --format '{{.ServerVersion}}' 2>$null) -join ''
    $dockerOk = ($LASTEXITCODE -eq 0 -and "$dockerVer".Trim().Length -gt 0)
    $ErrorActionPreference = $oldEap
    if (-not $dockerOk) {
        Fail '[!!] Developer kit not found in WSL and Docker is not running. Start Docker Desktop, or install the kit into a WSL distro.'
    }

    function Test-BackendDocker {
        try {
            $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 2 -UseBasicParsing
            return ($r.StatusCode -eq 200)
        } catch { return $false }
    }

    if (-not $NoBackend) {
        if (Test-BackendDocker) {
            Say '[ok] backend is already listening on 8765' 'Green'
        } else {
            Say '[..] starting backend (start.bat)...' 'Yellow'
            $env:ML_NO_BROWSER = '1'
            Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'start.bat' -WorkingDirectory $root -WindowStyle Hidden
            $ok = $false
            for ($i = 0; $i -lt 30; $i++) {
                Start-Sleep -Seconds 2
                if (Test-BackendDocker) { $ok = $true; break }
            }
            if (-not $ok) { Fail '[!!] backend did not come up in 60s; run start.bat manually to see the error.' }
            Say '[ok] backend is up' 'Green'
        }
    }

    $pkg = Join-Path $root 'dist\memorylens.fuiapp'
    $src = Join-Path $root 'glasses\src\index.js'
    $needBuild = $ForceBuild -or (-not (Test-Path $pkg))
    if (-not $needBuild -and (Test-Path $src)) {
        if ((Get-Item $pkg).LastWriteTime -lt (Get-Item $src).LastWriteTime) { $needBuild = $true }
    }
    if ($needBuild) {
        Say '[..] rebuilding glasses package in Docker (30-60s)...' 'Yellow'
        & powershell -NoProfile -File (Join-Path $PSScriptRoot 'kit-docker.ps1') build
        if ($LASTEXITCODE -ne 0) { Fail '[!!] build failed; see the log above.' }
        & powershell -NoProfile -File (Join-Path $PSScriptRoot 'kit-docker.ps1') package
        if ($LASTEXITCODE -ne 0) { Fail '[!!] package failed; see the log above.' }
        Say '[ok] package rebuilt' 'Green'
    }

    if ($CheckOnly) {
        Say '[ok] environment check passed (Docker / image / backend / package)' 'Green'
        exit 0
    }

    Say 'Docker + WSLg mode: a white client window and a black lens window will open.' 'Gray'
    Say 'Close the window or press Ctrl+C to stop.' 'Gray'
    & powershell -NoProfile -File (Join-Path $PSScriptRoot 'kit-docker.ps1') gui -Port $Port
    $code = $LASTEXITCODE
    if ($code -ne 0) { Fail ('[!!] simulator failed to start (exit ' + $code + '); see the log above.') }
    exit 0
}
Say ("[ok] distro=" + $distro + " kit=" + $kit) 'Green'

# WSLg (windows/input/audio bridge) must exist for the GUI
& wsl -d $distro -e test -d /mnt/wslg/runtime-dir 2>$null
if ($LASTEXITCODE -ne 0) {
    Fail '[!!] WSLg is not available in this distro. Update WSL (wsl --update) and restart.'
}

# Node 24 is required by the SDK; the kit distro normally ships it.
$nodeVer = (& wsl -d $distro -e bash -lc 'node -v' 2>$null) -join ''
if ($nodeVer -notmatch '^v24\.') {
    Fail ('[!!] Node 24 not found in ' + $distro + ' (got: ' + $nodeVer + '). Install the kit distro again.')
}
Say ("[ok] node " + $nodeVer) 'Green'

# ---------- 2. backend ----------
function Test-Backend {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 2 -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

if (-not $NoBackend) {
    if (Test-Backend) {
        Say '[ok] backend is already listening on 8765' 'Green'
    } else {
        Say '[..] starting backend (start.bat)...' 'Yellow'
        $env:ML_NO_BROWSER = '1'
        Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'start.bat' -WorkingDirectory $root -WindowStyle Hidden
        $ok = $false
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 2
            if (Test-Backend) { $ok = $true; break }
        }
        if (-not $ok) { Fail '[!!] backend did not come up in 60s; run start.bat manually to see the error.' }
        Say '[ok] backend is up' 'Green'
    }
}

# ---------- 3. build the glasses package if the source is newer ----------
$pkg = Join-Path $root 'dist\memorylens.fuiapp'
$src = Join-Path $root 'glasses\src\index.js'
$needBuild = $ForceBuild -or (-not (Test-Path $pkg))
if (-not $needBuild -and (Test-Path $src)) {
    if ((Get-Item $pkg).LastWriteTime -lt (Get-Item $src).LastWriteTime) { $needBuild = $true }
}
$buildScript = To-WslPath (Join-Path $PSScriptRoot 'build-wsl.sh')
$simScript = To-WslPath (Join-Path $PSScriptRoot 'sim-wsl.sh')
$wslProj = To-WslPath $root

if ($needBuild) {
    Say '[..] rebuilding glasses package (about 20-40s)...' 'Yellow'
    & wsl -d $distro -e env SIM_KIT=$kit SIM_PROJ=$wslProj bash $buildScript
    if ($LASTEXITCODE -ne 0) { Fail '[!!] build failed; see the log above.' }
    Say '[ok] package rebuilt: dist\memorylens.fuiapp' 'Green'
} else {
    Say '[ok] package is up to date' 'Green'
}

if ($CheckOnly) {
    Say '[ok] check passed: WSLg / kit / node / backend / package are ready' 'Green'
    exit 0
}

# ---------- 4. WSL -> Windows backend address ----------
$gw = (& wsl -d $distro -e sh -c "ip route | awk '/default/ {print `$3; exit}'" 2>$null) -join ''
$gw = $gw.Trim()
if (-not $gw) { $gw = '172.19.16.1' }
$backend = 'http://' + $gw + ':8765'
Say ("[ok] backend for the simulator: " + $backend) 'Green'

# ---------- 5. run ----------
@'

  +---------------------------- how to use ----------------------------+
  | White window = Client console; the six buttons on the right are   |
  | the glasses keys (Up/Down/Left/Right/Confirm/Back).               |
  | Black window = the lens (640x480), mirror of the left eye.        |
  |                                                                  |
  | Single mode: Confirm -> choose "single" -> Confirm starts record  |
  | Meeting mode: Confirm -> Right -> Confirm enters "multi"           |
  |   Confirm = start / stop recording                                |
  |   Left    = ask AI (popup opens or refreshes)                     |
  |   Right   = close AI popup                                        |
  |   Up/Down = scroll AI popup (only while it is open)               |
  |   Back    = stop + save, exit to the mode picker                  |
  |                                                                  |
  | No microphone in the simulator: feed audio/text from another      |
  | terminal:  python tools\say.py "..."                              |
  +-------------------------------------------------------------------+

'@ | Write-Host
Say '[..] launching simulator... close the windows or press Ctrl+C to stop.' 'Cyan'
& wsl -d $distro -e env SIM_KIT=$kit SIM_PROJ=$wslProj SIM_PORT=$Port MEMORYLENS_BACKEND=$backend bash $simScript
$code = $LASTEXITCODE
if ($code -ne 0) {
    Fail ('[!!] simulator exited with code ' + $code + '; see the log above.')
}
exit 0
