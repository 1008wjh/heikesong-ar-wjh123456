# Download a local ASR model from hf-mirror.
#
#   powershell -ExecutionPolicy Bypass -File tools\get_asr_model.ps1                 # default: sense-voice (better zh/en, punctuation)
#   powershell -ExecutionPolicy Bypass -File tools\get_asr_model.ps1 -Kind paraformer # smaller/faster, no punctuation
#
# Models land in models\asr\<name>\ ; llm.py prefers sense-voice when it exists.
param(
  [ValidateSet("sensevoice", "paraformer")]
  [string]$Kind = "sensevoice"
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot

if ($Kind -eq "sensevoice") {
  $dir  = Join-Path $root "models\asr\sense-voice"
  $base = "https://hf-mirror.com/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main"
} else {
  $dir  = Join-Path $root "models\asr\paraformer-zh-small"
  $base = "https://hf-mirror.com/csukuangfj/sherpa-onnx-paraformer-zh-small-2024-03-09/resolve/main"
}

New-Item -ItemType Directory -Force -Path $dir | Out-Null

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
  & curl.exe -sSL --max-time 3600 -o $out $f.url
  if ($LASTEXITCODE -ne 0) { throw ("download failed: " + $f.url) }
}

# llm.py 靠这个标记文件判断"这个目录是 sense-voice"
if ($Kind -eq "sensevoice") {
  $mark = Join-Path $dir "sense-voice"
  if (-not (Test-Path $mark)) { New-Item -ItemType File -Path $mark | Out-Null }
}

Get-ChildItem $dir | ForEach-Object { Write-Host ("  " + $_.Name + "  " + $_.Length) }
Write-Host ""
Write-Host "done. restart the server; /api/agent/status should say asr=sense-voice (asrProvider=local)"
