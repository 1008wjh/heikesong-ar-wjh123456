# 一键启动眼镜模拟器：查后端 -> 查包 -> 开窗口
# 由 sim.bat 调用；也可以直接 powershell -File tools\start-sim.ps1

param([int]$Port = 28045, [switch]$NoBackend)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Say($msg, $color = 'Gray') { Write-Host $msg -ForegroundColor $color }

# ---------- 1. Docker 在不在 ----------
try { docker info --format '{{.ServerVersion}}' | Out-Null } catch {
    Say 'Docker 没在跑。先打开 Docker Desktop，再双击 sim.bat。' 'Red'
    exit 1
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
        else { Say '[!!] 后端 30 秒还没起来，先单独跑 start.bat 看看报什么错' 'Red'; exit 1 }
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
    if ($LASTEXITCODE -ne 0) { Say '[!!] build 失败' 'Red'; exit 1 }
    & powershell -NoProfile -File (Join-Path $PSScriptRoot 'kit-docker.ps1') package
    if ($LASTEXITCODE -ne 0) { Say '[!!] package 失败' 'Red'; exit 1 }
    Say '[ok] 包已更新' 'Green'
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

& powershell -NoProfile -File (Join-Path $PSScriptRoot 'kit-docker.ps1') gui -Port $Port
exit $LASTEXITCODE
