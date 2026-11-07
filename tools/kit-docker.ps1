# Run the IrisView toolchain inside Docker (real Ubuntu 22.04, no host OS pain).
#
#   powershell -File tools\kit-docker.ps1 test
#   powershell -File tools\kit-docker.ps1 build
#   powershell -File tools\kit-docker.ps1 package
#   powershell -File tools\kit-docker.ps1 run      # simulator + real screenshot -> test/simulator.png
#   powershell -File tools\kit-docker.ps1 doctor
#
# Requires: Docker Desktop running, and the image built once:
#   docker build -f deploy/Dockerfile.irisview -t memorylens-irisview .
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads scripts as ANSI,
# so UTF-8 Chinese here breaks the parser.

param(
    [Parameter(Position = 0)][string]$Cmd = 'test',
    [int]$Port = 28045,
    [int]$WaitSec = 25,
    [string]$Backend = 'http://host.docker.internal:8765',
    [string]$Kit = '',
    [string]$StateDir = ''
)

$ErrorActionPreference = 'Stop'

# Kit location: -Kit, then $env:IRIS_KIT, then common spots, then a search under the project.
function Find-Kit {
    $candidates = @(
        $Kit,
        $env:IRIS_KIT,
        'E:\ar-memory\IrisView-Developer-Kit-v1.0.0-LLVISION-v2\IrisView-Developer-Kit-v1.0.0-LLVISION-v2\IrisView-Developer-Kit-v1.0.0',
        "$env:USERPROFILE\Downloads\IrisView-Developer-Kit-v1.0.0-LLVISION-v2\IrisView-Developer-Kit-v1.0.0-LLVISION-v2\IrisView-Developer-Kit-v1.0.0"
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path (Join-Path $c 'sdk\fusionui-app-sdk\fusionui_sdk.py'))) { return (Resolve-Path $c).Path }
    }
    $hit = Get-ChildItem -Path 'E:\ar-memory','C:\Users\Lenovo\Downloads' -Recurse -Filter 'fusionui_sdk.py' -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($hit) { return (Resolve-Path (Join-Path $hit.DirectoryName '..\..\..')).Path }
    throw "Developer Kit not found. Pass -Kit <path to IrisView-Developer-Kit-v1.0.0> or set `$env:IRIS_KIT."
}

$kit = Find-Kit
Write-Host "kit: $kit" -ForegroundColor DarkGray

$proj = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$image = 'memorylens-irisview'
$sdk = '/kit/sdk/fusionui-app-sdk/fusionui_sdk.py'

$argsList = @('run', '--rm', '-e', 'FUSIONUI_DEVELOPER_KIT_ROOT=/kit')
$argsList += @('-e', "MEMORYLENS_BACKEND=$Backend")
if ($StateDir) { $argsList += @('-e', "STATE_DIR=/work/$StateDir") }
$argsList += @('--add-host', 'host.docker.internal:host-gateway')
$argsList += @('-v', "${kit}:/kit", '-v', "${proj}:/work")
$argsList += @('-w', '/work', $image, 'bash', '-lc')

switch ($Cmd) {
    'doctor'  { $inner = "python3 $sdk doctor --runtime-only" }
    'test'    { $inner = "python3 $sdk test /work/glasses" }
    'build'   { $inner = "python3 $sdk build /work/glasses" }
    'package' { $inner = "python3 $sdk package /work/glasses --output /work/dist/memorylens.fuiapp --debug-unsigned" }
    'run'     { $inner = "bash /work/deploy/sim-shot.sh $Port $WaitSec" }
    default   { throw "unknown command: $Cmd (use test / build / package / run / doctor)" }
}

Write-Host "== docker $Cmd ==" -ForegroundColor Cyan
& docker @argsList $inner
exit $LASTEXITCODE
