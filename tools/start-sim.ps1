# 一键启动眼镜模拟器：查后端 -> 查包 -> 开窗口
# 由 sim.bat 调用；也可以直接 powershell -File tools\start-sim.ps1

param([int]$Port = 28045, [switch]$NoBackend, [switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Say($msg, $color = 'Gray') { Write-Host $msg -ForegroundColor $color }

# 出错时不要一闪而过：消息留在这里，由 sim.bat 负责暂停（直接跑本脚本时看终端）
function Fail($msg) {
    Say $msg 'Red'
    exit 1
}

function Test-Docker {
    # PS 5.1：$ErrorActionPreference=Stop 时，原生命令的 stderr 一旦被重定向就会抛异常，
    # 所以探测命令这里临时放开，只看退出码。
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $v = & docker info --format '{{.ServerVersion}}' 2>$null
        return ($LASTEXITCODE -eq 0 -and "$v".Trim().Length -gt 0)
    } catch { return $false } finally { $ErrorActionPreference = $old }
}
function Test-DockerWsl {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $v = & wsl -d Ubuntu -e docker info --format '{{.ServerVersion}}' 2>$null
        return ($LASTEXITCODE -eq 0 -and "$v".Trim().Length -gt 0)
    } catch { return $false } finally { $ErrorActionPreference = $old }
}

# ---------- 1. Docker 在不在（不在就尝试拉起 Docker Desktop） ----------
if (-not (Test-Docker)) {
    Say '[..] Docker Desktop 没在跑，正在尝试启动（最多等 2 分钟）' 'Yellow'
    $exe = @(
        (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),
        (Join-Path $env:LOCALAPPDATA 'Docker\Docker Desktop.exe')
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $exe) {
        Fail '[!!] 没找到 Docker Desktop：先安装 Docker Desktop（见 deploy\README.md），再双击 sim.bat'
    }
    Start-Process -FilePath $exe | Out-Null
    $up = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Docker) { $up = $true; break }
    }
    if (-not $up) {
        Fail '[!!] Docker 等了两分钟还没起来：手动打开 Docker Desktop，等左下角变绿后再双击 sim.bat'
    }
    Say '[ok] Docker 已就绪' 'Green'
}

# 模拟器是在 WSL 里用 docker 跑的，所以 Docker Desktop 里要勾上 WSL Integration
# 冷启动时 Docker Desktop 先起引擎、WSL 集成要再等几十秒，所以这里给 3 分钟
if (-not (Test-DockerWsl)) {
    Say '[..] Docker 引擎好了，等 WSL 集成就绪（最多 3 分钟）…' 'Yellow'
    $wslOk = $false
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 2
        if (Test-DockerWsl) { $wslOk = $true; break }
    }
    if (-not $wslOk) {
        Fail '[!!] WSL 里调不到 docker：Docker Desktop → Settings → Resources → WSL Integration，打开 Ubuntu 再重试'
    }
    Say '[ok] WSL 集成已就绪' 'Green'
}

# ---------- 模拟器镜像 ----------
$oldEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& docker image inspect memorylens-irisview *> $null
$imgOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $oldEap
if (-not $imgOk) {
    Say '[..] 模拟器镜像还没建，先 build（第一次 5-10 分钟，需要联网）' 'Yellow'
    & docker build -f deploy/Dockerfile.irisview -t memorylens-irisview .
    if ($LASTEXITCODE -ne 0) { Fail '[!!] 镜像 build 失败：看上面的报错（常见是拉基础镜像超时，重试一次）' }
    Say '[ok] 镜像已建好' 'Green'
}

# ---------- 2. 后端在不在（不在就拉起来） ----------
function Test-Backend {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8765/api/health" -TimeoutSec 2 -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

if (-not $NoBackend) {
    if (Test-Backend) {
        Say '[ok] 后端已经在 8765 上跑着' 'Green'
    } else {
        Say '[..] 后端没起，正在用 start.bat 拉起来' 'Yellow'
        Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'start.bat' -WorkingDirectory $root -WindowStyle Hidden
        $ok = $false
        for ($i = 0; $i -lt 25; $i++) {
            Start-Sleep -Seconds 2
            if (Test-Backend) { $ok = $true; break }
        }
        if ($ok) { Say '[ok] 后端起来了' 'Green' }
        else { Fail '[!!] 后端 30 秒还没起来，先单独跑 start.bat 看看报什么错' }
    }
}

# ---------- 3. 包在不在 / 是不是旧的 ----------
$pkg = Join-Path $root 'dist\memorylens.fuiapp'
$src = Join-Path $root 'glasses\src\index.js'
$needBuild = $true
if (Test-Path $pkg) {
    if ((Get-Item $pkg).LastWriteTime -ge (Get-Item $src).LastWriteTime) { $needBuild = $false }
}
if ($needBuild) {
    Say '[..] 包不存在或比源码旧，先 build + package（第一次要一两分钟）' 'Yellow'
    & powershell -NoProfile -File (Join-Path $PSScriptRoot 'kit-docker.ps1') build
    if ($LASTEXITCODE -ne 0) { Fail '[!!] build 失败：看上面的报错' }
    & powershell -NoProfile -File (Join-Path $PSScriptRoot 'kit-docker.ps1') package
    if ($LASTEXITCODE -ne 0) { Fail '[!!] package 失败：看上面的报错' }
    Say '[ok] 包已更新' 'Green'
}

if ($CheckOnly) {
    Say '[ok] 环境检查通过（Docker / WSL / 镜像 / 后端 / 包都就绪）' 'Green'
    exit 0
}

# ---------- 4. 开窗口 ----------
@'

  ┌────────────────────────────── 怎么玩 ──────────────────────────────┐
  │ 白色大窗 = Client 控制台，右侧「应用输入」那六个按钮就是眼镜的键   │
  │ 黑色小窗 = 镜片（640x480），只读                                   │
  │                                                                   │
  │  待机      点「确认」→ 出「单人 / 多人」→ 再点「确认」进单人      │
  │  在听      「确认」= 看详情（不打断）  「返回」= 结束并入库        │
  │  待机      「上」= 短语键盘   「下」= 三条建议                     │
  │  详情页    「确认」= 收起     「返回」= 清屏                       │
  │                                                                   │
  │ 模拟器没有麦克风：另开一个终端，喂一句话给镜片                     │
  │     python tools\say.py "王姐：报价单下周三之前发你"               │
  │     python tools\say.py --audio test\voice\huihui_b.wav            │
  └───────────────────────────────────────────────────────────────────┘
  关掉窗口或按 Ctrl+C 结束。

'@ | Write-Host

$guiT0 = Get-Date
& powershell -NoProfile -File (Join-Path $PSScriptRoot 'kit-docker.ps1') gui -Port $Port
$code = $LASTEXITCODE
if ($code -ne 0) {
    # 十几秒内就退出来 = 根本没起来；跑了很久再退 = 用户自己关的窗口
    if (((New-TimeSpan -Start $guiT0).TotalSeconds) -lt 15) {
        Fail ('[!!] 模拟器没起来（退出码 ' + $code + '）：看上面的报错')
    }
    Say '[ok] 模拟器已关闭' 'Gray'
}
exit 0
