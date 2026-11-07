<#
.SYNOPSIS
    一键打开 IrisView 模拟器运行 MemoryLens（Windows PowerShell）。

.DESCRIPTION
    做四件事：
      1. 检查 WSL 发行版 / WSLg 图形环境 / 模拟器 Kit / 安装包；
      2. 取 WSL 访问 Windows 的网关 IP；
      3. 可选：在本机启动后端（监听 0.0.0.0，供 WSL 访问）；
      4. 在 WSLg 里启动 IrisView Client + Simulator，并把日志留在 repo 的 data/sim/ 下。

    默认是“前台”运行：关掉脚本所在窗口或按 Ctrl+C，就等于结束 Client / 模拟器。
    想让它自己跑在后台，加 -Background。

.PARAMETER StartBackend
    在 8765 端口启动 python src\server.py（新窗口），并等待 /api/health 就绪。
    不传的话只检查后端是否已经在跑。

.PARAMETER CheckOnly
    只做检查，不启动模拟器。

.PARAMETER Background
    用后台进程启动 WSL Client，不占用当前终端（日志仍写入 data/sim/）。

.PARAMETER Diagnostic
    用 diagnostic/lite 模拟器配置，日志更详细（默认 release）。

.PARAMETER Stereo
    双目模式（默认单目 mono）。

.PARAMETER Silent
    关闭模拟器音乐输出（--audio-mode silent），默认 desktop。

.PARAMETER Stop
    结束当前 WSL 里正在跑的 IrisView Client / Simulator。

.EXAMPLE
    .\tools\open_sim.ps1 -CheckOnly
    .\tools\open_sim.ps1 -StartBackend
    .\tools\open_sim.ps1 -StartBackend -Background
    .\tools\open_sim.ps1 -RestartWsl -StartBackend
    .\tools\open_sim.ps1 -Stop
#>
[CmdletBinding()]
param(
    [string]$Distro = "IrisView",
    [string]$Kit = "/home/iris/IrisView/Developer-Kit",
    [string]$Package = "",
    [int]$BackendPort = 8765,
    [int]$SessionPort = 28045,
    [ValidateSet("mono", "stereo")][string]$DisplayMode = "mono",
    [ValidateSet("desktop", "silent")][string]$AudioMode = "desktop",
    [switch]$StartBackend,
    [switch]$CheckOnly,
    [switch]$Background,
    [switch]$Diagnostic,
    [switch]$RestartWsl,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Package) { $Package = Join-Path $Root "dist\memorylens.fuiapp" }
$LogDir = Join-Path $Root "data\sim"
$LogFile = Join-Path $LogDir ("sim-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")

# WSL 输出用 UTF-8，别让中文/日志变乱码
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}
$env:WSL_UTF8 = "1"

function Write-Step([string]$Text) { Write-Host ("==> " + $Text) -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host ("    OK  " + $Text) -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host ("    !   " + $Text) -ForegroundColor Yellow }
function Fail([string]$Text) { Write-Host ("    ERR " + $Text) -ForegroundColor Red; exit 1 }

function Invoke-Wsl([string[]]$WslArgs, [switch]$Quiet) {
    if (-not $Quiet) { Write-Host ("    wsl -d $Distro " + ($WslArgs -join " ")) -ForegroundColor DarkGray }
    $out = & wsl.exe -d $Distro @WslArgs 2>&1
    $code = $LASTEXITCODE
    return [pscustomobject]@{ Code = $code; Text = (($out | Out-String).Trim()) }
}

function Get-HostIp {
    $r = Invoke-Wsl -WslArgs @("-e", "bash", "-lc", "ip route show default") -Quiet
    if ($r.Code -ne 0 -or -not $r.Text) { return "" }
    $parts = $r.Text -split "\s+"
    if ($parts.Count -ge 3) { return $parts[2] }
    return ""
}

function Test-Backend([string]$HostName) {
    try {
        $resp = Invoke-WebRequest -Uri ("http://" + $HostName + ":" + $BackendPort + "/api/health") `
            -TimeoutSec 4 -UseBasicParsing
        if ($resp.StatusCode -eq 200) { return $true }
    } catch { }
    return $false
}

function Test-WslBackend([string]$Ip) {
    $cmd = 'curl -fsS -m 4 http://' + $Ip + ':' + $BackendPort + '/api/health'
    $r = Invoke-Wsl -WslArgs @("-e", "bash", "-lc", $cmd) -Quiet
    return ($r.Code -eq 0 -and $r.Text -match '"ok"')
}

# ------------------------------------------------------------------ Stop
if ($Stop) {
    Write-Step "结束 WSL 里的 IrisView Client / Simulator"
    $r = Invoke-Wsl -WslArgs @("-e", "bash", "-lc",
        "pkill -f 'fusionui-glasses-client' 2>/dev/null; pkill -f 'fusionui-glasses-simulator' 2>/dev/null; pkill -f 'fusionui_sdk.py run' 2>/dev/null; sleep 1; pgrep -af 'fusionui' || true") -Quiet
    Write-Host $r.Text
    exit 0
}

# ------------------------------------------------------------------ 可选：重启 WSL（WSLg 卡死时用）
if ($RestartWsl) {
    Write-Step "重启 WSL（会结束 WSL 里的进程；用于修复 WSLg 无窗口/无声音）"
    & wsl.exe --shutdown
    Start-Sleep -Seconds 6
}

# ------------------------------------------------------------------ 检查
Write-Step "检查 WSL 发行版（$Distro）"
$distros = (& wsl.exe -l -q) 2>&1 | ForEach-Object { ($_ -replace "`0", "").Trim() } | Where-Object { $_ }
if ($distros -notcontains $Distro) {
    Fail ("没找到 WSL 发行版 " + $Distro + "。现有：" + ($distros -join ", ") + "。先跑 wsl -l -v 看看。")
}
$probe = Invoke-Wsl -WslArgs @("-e", "bash", "-lc", "echo ok") -Quiet
if ($probe.Code -ne 0) { Fail "WSL $Distro 起不来：$($probe.Text)" }
Write-Ok "WSL $Distro 可用"

Write-Step "检查模拟器 Kit（$Kit）"
$kitCheck = Invoke-Wsl -WslArgs @("-e", "bash", "-lc",
    "test -x '$Kit/bin/irisview' && test -d '$Kit/simulators' && echo kit-ok") -Quiet
if ($kitCheck.Code -ne 0 -or $kitCheck.Text -notmatch "kit-ok") {
    Fail "Kit 不完整或路径不对：$Kit。确认 /home/iris/IrisView/Developer-Kit 存在，且 bin/irisview 可执行。"
}
Write-Ok "Kit 完整"

Write-Step "检查 WSLg 图形/音频"
$wslgCmd = 'test -S /mnt/wslg/runtime-dir/wayland-0 && echo wayland-ok; test -S /mnt/wslg/.X11-unix/X0 && echo x11-ok; test -S /mnt/wslg/PulseServer && echo pulse-ok'
$wslg = Invoke-Wsl -WslArgs @("-e", "bash", "-lc", $wslgCmd) -Quiet
if ($wslg.Text -notmatch "wayland-ok" -or $wslg.Text -notmatch "x11-ok") {
    Write-Warn "WSLg 没起来（wayland-0 / X0 socket 不存在）。先跑 wsl --shutdown 再重试，或加 -RestartWsl。"
} else {
    Write-Ok "WSLg 图形通道正常"
}
if ($wslg.Text -match "pulse-ok") {
    Write-Ok "WSLg 音频通道正常"
    $mic = Invoke-Wsl -WslArgs @("-e", "bash", "-lc", "pactl list short sources 2>/dev/null | head -5") -Quiet
    if ($mic.Text -match "RDPSource") { Write-Ok "WSLg 麦克风 RDPSource 可用（可做真实语音）" }
    else { Write-Warn "没看到 RDPSource：模拟器里可能采不到麦克风。" }
} else {
    Write-Warn "没看到 /mnt/wslg/PulseServer：模拟器可能没有声音/麦克风；可用 -RestartWsl 修复。"
}

Write-Step "检查安装包（$Package）"
if (-not (Test-Path -LiteralPath $Package)) {
    Fail "找不到安装包：$Package。先在 repo 根目录跑打包（tools/kit-docker.ps1 package 或 WSL 内 bin/irisview package）。"
}
$pkgWsl = (Invoke-Wsl -WslArgs @("-e", "wslpath", "-u", "`"$Package`"") -Quiet).Text
if (-not $pkgWsl) { Fail "wslpath 转换失败：$Package" }
Write-Ok "安装包 $pkgWsl"

Write-Step "取 WSL 访问 Windows 的地址"
$hostIp = Get-HostIp
if (-not $hostIp) { Fail ("读不到默认网关。先跑 wsl -d " + $Distro + " -e bash -lc 'ip route show default' 看看。") }
Write-Ok "Windows 侧地址：$hostIp（后端 http://$hostIp`:$BackendPort）"

Write-Step "检查后端 http://127.0.0.1:$BackendPort"
$backendUp = Test-Backend "127.0.0.1"
if (-not $backendUp -and $StartBackend) {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $backendLog = Join-Path $LogDir ("backend-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")
    $token = ($env:AR_API_TOKEN + "").Trim()
    $inner = "`$env:AR_HOST='0.0.0.0'; `$env:AR_LOG_PII='0';"
    if ($token) { $inner += "`$env:AR_API_TOKEN='$token';" }
    if ($env:AR_LLM_STUB) { $inner += "`$env:AR_LLM_STUB='$($env:AR_LLM_STUB)';" }
    $inner += "Set-Location '$Root'; python src\server.py 2>&1 | Tee-Object -FilePath '$backendLog'"
    Write-Ok "启动后端（新窗口，日志 $backendLog）"
    Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoExit", "-Command", $inner) -WindowStyle Minimized
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Backend "127.0.0.1") { $backendUp = $true; break }
    }
    if (-not $backendUp) {
        Write-Warn "后端 30 秒没起来，看一眼 $backendLog。"
    }
}
if ($backendUp) {
    Write-Ok "后端在线"
} else {
    Write-Warn "后端没在 http://127.0.0.1:$BackendPort 上。模拟器会显示离线；要一起起，加 -StartBackend。"
}

if ($backendUp) {
    Write-Step "检查 WSL → Windows 后端网络"
    if (Test-WslBackend $hostIp) {
        Write-Ok "WSL 能访问后端"
    } else {
        Write-Warn "WSL 访问不到 http://$hostIp`:$BackendPort。"
        Write-Warn "若后端不是 AR_HOST=0.0.0.0 启动的，或 Windows 防火墙拦了，按提示放行："
        Write-Host ("      New-NetFirewallRule -DisplayName 'MemoryLens 8765' -Direction Inbound -LocalPort $BackendPort -Protocol TCP -Action Allow") -ForegroundColor DarkGray
    }
}

if ($CheckOnly) {
    Write-Host ""
    Write-Host "检查结束（-CheckOnly，不启动模拟器）。" -ForegroundColor Green
    exit 0
}

# ------------------------------------------------------------------ 启动
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Write-Step "启动 IrisView Client + Simulator（$DisplayMode，$AudioMode，profile $(if ($Diagnostic) {'diagnostic'} else {'release'})）"

$profileArg = if ($Diagnostic) { "diagnostic" } else { "release" }
# 用户设了 AR_API_TOKEN 时，眼镜端要用同一个 MEMORYLENS_TOKEN
$token = ($env:AR_API_TOKEN + "").Trim()

# 把启动命令写成一个 LF 换行的 bash 脚本：避免 Start-Process / wsl.exe 的多层引号问题
$shWin = Join-Path $LogDir ("run-sim-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".sh")
$shWsl = (Invoke-Wsl -WslArgs @("-e", "wslpath", "-u", "`"$shWin`"") -Quiet).Text
if (-not $shWsl) { Fail "wslpath 转换失败：$shWin" }

# 后台模式让 bash 自己把输出写日志，避免 wsl.exe 继承父进程管道导致脚本不返回
$logWsl = ""
if ($Background) {
    $logWsl = (Invoke-Wsl -WslArgs @("-e", "wslpath", "-u", "`"$LogFile`"") -Quiet).Text
}

$shLines = @(
    "#!/bin/bash",
    "set -euo pipefail",
    "export DISPLAY=:0",
    "export WAYLAND_DISPLAY=wayland-0",
    "export XDG_RUNTIME_DIR=/mnt/wslg/runtime-dir",
    "export PULSE_SERVER=unix:/mnt/wslg/PulseServer",
    "export MEMORYLENS_BACKEND=http://$hostIp`:$BackendPort"
)
if ($token) { $shLines += "export MEMORYLENS_TOKEN=$token" }
$shLines += "cd '$Kit'"
$execLine = "exec ./bin/irisview run '$pkgWsl' --allow-debug-unsigned --display-mode $DisplayMode --audio-mode $AudioMode --profile $profileArg --port $SessionPort"
if ($Background -and $logWsl) { $execLine += " >> '$logWsl' 2>&1" }
$shLines += $execLine
$sh = ($shLines -join "`n") + "`n"
[System.IO.File]::WriteAllText($shWin, $sh, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "    启动脚本：$shWin" -ForegroundColor DarkGray

Write-Host "    （首次启动会加载 Kit，几秒后出现 Client 和 Simulator 两个窗口）" -ForegroundColor DarkGray
$start = Get-Date
if ($Background) {
    # 用 cmd start 开新控制台，避免 wsl.exe 继承当前脚本的 stdout 管道把调用方挂住
    $cmdLine = '/c start "" /min wsl.exe -d ' + $Distro + ' -e bash ' + $shWsl
    $proc = Start-Process -FilePath "cmd.exe" -ArgumentList $cmdLine -WindowStyle Hidden -PassThru
    Write-Ok "后台已启动（cmd PID $($proc.Id)），日志：$LogFile"
    Write-Host "    停止：.\tools\open_sim.ps1 -Stop" -ForegroundColor DarkGray
} else {
    & wsl.exe -d $Distro -e bash $shWsl 2>&1 | Tee-Object -FilePath $LogFile
    $code = $LASTEXITCODE
    Write-Host ""
    if ($code -eq 0) {
        Write-Ok ("Client 正常退出，日志：$LogFile")
    } else {
        Write-Warn "Client 退出码 $code，日志：$LogFile"
        Write-Host "    模拟器日志：" -ForegroundColor DarkGray
        $simLog = "/home/iris/IrisView-Developer-Kit-Data/commercial/v1.0.0/shared/profiles/release-off/logs/simulator-release-off.log"
        if ($Diagnostic) { $simLog = "/home/iris/IrisView-Developer-Kit-Data/commercial/v1.0.0/shared/profiles/diagnostic-lite/logs/simulator-diagnostic-lite.log" }
        Write-Host ("    " + $simLog) -ForegroundColor DarkGray
        Write-Host "    最近 20 行：" -ForegroundColor DarkGray
        Invoke-Wsl -WslArgs @("-e", "bash", "-lc", "tail -20 '$simLog' 2>/dev/null") -Quiet | ForEach-Object { $_.Text } | Write-Host
    }
}
Write-Host ("    用时 " + [int]((Get-Date) - $start).TotalSeconds + "s") -ForegroundColor DarkGray
