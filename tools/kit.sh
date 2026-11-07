#!/usr/bin/env bash
# IrisView 工具链的 WSL 入口（Windows 上 `irisview new` 会崩在 ncrypto，只能走 WSL）
#
# 用法：
#   wsl -d Ubuntu bash /mnt/e/ar-memory/tools/kit.sh test
#   wsl -d Ubuntu bash /mnt/e/ar-memory/tools/kit.sh build
#   wsl -d Ubuntu bash /mnt/e/ar-memory/tools/kit.sh package
#   wsl -d Ubuntu bash /mnt/e/ar-memory/tools/kit.sh verify <包路径>
#
# 开发包位置：优先 $IRIS_KIT，其次几个常见路径，最后在 /mnt/c 下按 fusionui_sdk.py 反查。
set -u

find_kit() {
  if [[ -n "${IRIS_KIT:-}" && -f "$IRIS_KIT/sdk/fusionui-app-sdk/fusionui_sdk.py" ]]; then
    echo "$IRIS_KIT"; return
  fi
  local c
  for c in \
    /mnt/e/ar-memory/IrisView-Developer-Kit-v1.0.0-LLVISION-v2/IrisView-Developer-Kit-v1.0.0-LLVISION-v2/IrisView-Developer-Kit-v1.0.0 \
    "/mnt/c/Users/Lenovo/Downloads/IrisView-Developer-Kit-v1.0.0-LLVISION-v2/IrisView-Developer-Kit-v1.0.0-LLVISION-v2/IrisView-Developer-Kit-v1.0.0"; do
    if [[ -f "$c/sdk/fusionui-app-sdk/fusionui_sdk.py" ]]; then echo "$c"; return; fi
  done
  local hit
  hit=$(find "/mnt/e/ar-memory" "/mnt/c/Users/Lenovo/Downloads" -maxdepth 6 \
        -path '*/sdk/fusionui-app-sdk/fusionui_sdk.py' -print -quit 2>/dev/null || true)
  if [[ -n "$hit" ]]; then echo "$(cd "$(dirname "$hit")/../.." && pwd)"; fi
}

KIT="$(find_kit)"
if [[ -z "$KIT" ]]; then
  echo "找不到 IrisView 开发包。设 IRIS_KIT=/mnt/x/.../IrisView-Developer-Kit-v1.0.0 再试。" >&2
  exit 1
fi

NODE_DIR="$HOME/tools/node-v24.13.0-linux-x64/bin"
APP="/mnt/e/ar-memory/glasses"
SDK="$KIT/sdk/fusionui-app-sdk/fusionui_sdk.py"

export PATH="$NODE_DIR:$PATH"
export FUSIONUI_DEVELOPER_KIT_ROOT="$KIT"

echo "kit: $KIT"

cmd="${1:-test}"
shift || true

case "$cmd" in
  test)    python3 "$SDK" test "$APP" ;;
  build)   python3 "$SDK" build "$APP" ;;
  package) python3 "$SDK" package "$APP" --output /mnt/e/ar-memory/dist/memorylens.fuiapp --debug-unsigned ;;
  verify)  python3 "$SDK" verify "${1:-/mnt/e/ar-memory/dist/memorylens.fuiapp}" ;;
  doctor)  python3 "$SDK" doctor ;;
  *)       python3 "$SDK" "$cmd" "$@" ;;
esac
