#!/usr/bin/env bash
# 在 WSL 里直接启动 IrisView 模拟器（WSLg 显示到 Windows 桌面），不依赖 Docker。
# 由 tools/start-sim.ps1 / sim.bat 调用；也可以：
#   wsl -d IrisView bash /mnt/c/.../heikesong-ar-wjh123456/tools/sim-wsl.sh
#
# 环境变量：
#   SIM_DISTRO    WSL 发行版（默认 IrisView）
#   SIM_KIT       WSL 里的 Developer Kit 路径（默认 /home/iris/IrisView/Developer-Kit）
#   MEMORYLENS_BACKEND  后端地址（默认 WSL 网关）
#   SIM_PORT      模拟器端口（默认 28045）
set -uo pipefail

DISTRO="${SIM_DISTRO:-IrisView}"
KIT="${SIM_KIT:-/home/iris/IrisView/Developer-Kit}"
PORT="${SIM_PORT:-28045}"
PROJ="${SIM_PROJ:-/mnt/c/Users/lenovo/Desktop/heikesong-ar-wjh123456}"
PKG="${1:-$PROJ/dist/memorylens.fuiapp}"

if [[ ! -f "$PKG" ]]; then
  echo "[sim] 包不存在：$PKG" >&2
  echo "      先打包：python3 $KIT/sdk/fusionui-app-sdk/fusionui_sdk.py package glasses --output dist/memorylens.fuiapp --debug-unsigned" >&2
  exit 2
fi

# WSLg：窗口、输入、音频都从 /mnt/wslg 走
export DISPLAY="${DISPLAY:-:0}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/mnt/wslg/runtime-dir}"
export PULSE_SERVER="${PULSE_SERVER:-unix:/mnt/wslg/PulseServer}"
export FUSIONUI_DEVELOPER_KIT_ROOT="$KIT"
export MEMORYLENS_BACKEND="${MEMORYLENS_BACKEND:-http://172.19.16.1:8765}"

cd "$PROJ" || exit 1
echo "[sim] distro=$DISTRO kit=$KIT backend=$MEMORYLENS_BACKEND pkg=$PKG"
exec "$KIT/bin/irisview" run "$PKG" \
  --allow-debug-unsigned --allow-debug-replace \
  --display-mode mono --audio-mode desktop --profile release --port "$PORT"
