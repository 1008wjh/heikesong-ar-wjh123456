# Download the local ASR model (paraformer-zh small, ~82MB) from hf-mirror.
# Usage:  powershell -ExecutionPolicy Bypass -File tools\get_asr_model.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$dir  = Join-Path $root "models\asr\paraformer-zh-small"
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$base = "https://hf-mirror.com/csukuangfj/sherpa-onnx-paraformer-zh-small-2024-03-09/resolve/main"
$files = @(
  @{ name = "model.int8.onnx"; url = "$base/model.int8.onnx" },
  @{ name = "tokens.txt";      url = "$base/tokens.txt" }
)

foreach ($f in $files) {
  $out = Join-Path $dir $f.name
  if ((Test-Path $out) -and ((Get-Item $out).Length -gt 1024)) {
    Write-Host ("skip  " + $f.name + " (already there)")
    continue
  }
  Write-Host ("get   " + $f.name)
  & curl.exe -sSL --max-time 1800 -o $out $f.url
  if ($LASTEXITCODE -ne 0) { throw ("download failed: " + $f.url) }
}

Get-ChildItem $dir | ForEach-Object { Write-Host ("  " + $_.Name + "  " + $_.Length) }
Write-Host ""
Write-Host "done. restart the server, /api/converse/llm should say asrProvider=local"
