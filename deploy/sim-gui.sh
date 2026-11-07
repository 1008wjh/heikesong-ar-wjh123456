#!/usr/bin/env bash
# 在 WSLg 上开一个真窗口跑模拟器（可以自己点按钮）。
# 由 tools/kit-docker.ps1 gui 调用；也可以：
#   wsl -d Ubuntu docker run --rm -e DISPLAY=:0 -v /tmp/.X11-unix:/tmp/.X11-unix \
#       -v /mnt/wslg:/mnt/wslg -v /mnt/e/ar-memory:/work ... memorylens-irisview bash /work/deploy/sim-gui.sh
set -uo pipefail

PORT="${1:-28045}"
KIT_SRC="${FUSIONUI_DEVELOPER_KIT_ROOT:-/kit}"
PKG=/work/dist/memorylens.fuiapp

if [[ ! -f "$PKG" ]]; then
  echo "先打包：powershell -File tools/kit-docker.ps1 package" >&2
  exit 2
fi

# Windows 绑定挂载是 0777，运行时要求打包资产 0644/0600 —— 拷进容器再摆正权限
STAGE=/opt/kit
if [[ ! -d "$STAGE/runtime" ]]; then
  echo "== 拷开发包到容器内（约 10 秒）=="
  mkdir -p "$STAGE"
  cp -a "$KIT_SRC"/. "$STAGE"/
  find "$STAGE" -type d -exec chmod 0755 {} +
  find "$STAGE" -type f -exec chmod 0755 {} +
  find "$STAGE/runtime/runtime/@irisview/application-api" -type f -exec chmod 0644 {} +
fi
export FUSIONUI_DEVELOPER_KIT_ROOT="$STAGE"
SDK="$STAGE/sdk/fusionui-app-sdk/fusionui_sdk.py"
export PATH="$STAGE/bin:$PATH"

echo "DISPLAY=$DISPLAY  backend=${MEMORYLENS_BACKEND:-http://127.0.0.1:8765}"
echo "窗口应该在 Windows 桌面上直接出现；关掉窗口或 Ctrl+C 结束。"

exec python3 "$SDK" run "$PKG" --port "$PORT" --allow-debug-unsigned \
  --display-mode mono --client-mode normal
