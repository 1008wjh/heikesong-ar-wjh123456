#!/usr/bin/env bash
# 在 WSL 的 IrisView 开发者包里构建 + 打包眼镜端应用（不依赖 Docker）。
# 由 tools/start-sim.ps1 调用；也可以：
#   wsl -d IrisView bash /mnt/c/.../tools/build-wsl.sh
set -euo pipefail

KIT="${SIM_KIT:-/home/iris/IrisView/Developer-Kit}"
PROJ="${SIM_PROJ:-/mnt/c/Users/lenovo/Desktop/heikesong-ar-wjh123456}"
SDK="$KIT/sdk/fusionui-app-sdk/fusionui_sdk.py"

if [[ ! -f "$SDK" ]]; then
  echo "[build] 找不到 SDK：$SDK" >&2
  exit 2
fi

cd "$PROJ"
export FUSIONUI_DEVELOPER_KIT_ROOT="$KIT"
echo "[build] kit=$KIT proj=$PROJ"
python3 "$SDK" build glasses
python3 "$SDK" package glasses --output dist/memorylens.fuiapp --debug-unsigned
echo "[build] done: $PROJ/dist/memorylens.fuiapp"
