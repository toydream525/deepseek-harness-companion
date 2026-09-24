param(
    [string]$Compiler = ""
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$releaseDir = Join-Path $projectRoot 'release'
$payload = Join-Path $releaseDir 'DSH-Companion'
$script = Join-Path $projectRoot 'installer\DSH-Companion.iss'
$output = Join-Path $releaseDir 'DSH-Companion-Setup-v0.1.0-windows-x64.exe'

if (-not (Test-Path -LiteralPath (Join-Path $payload 'DSH-Companion.exe'))) {
    throw '先运行 manager/build_exe.py 生成干净的 release/DSH-Companion。'
}

if (-not $Compiler) {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
        'C:\Program Files\Inno Setup 6\ISCC.exe'
    )
    $Compiler = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $Compiler -or -not (Test-Path -LiteralPath $Compiler)) {
    throw '找不到 Inno Setup 6 的 ISCC.exe。请从 https://jrsoftware.org/isdl.php 安装后重试。'
}

& $Compiler $script
if ($LASTEXITCODE -ne 0) { throw "安装器编译失败：$LASTEXITCODE" }
if (-not (Test-Path -LiteralPath $output)) { throw "安装器未生成：$output" }

$file = Get-Item -LiteralPath $output
$hash = Get-FileHash -LiteralPath $output -Algorithm SHA256
Write-Output "安装器：$($file.FullName)"
Write-Output "大小：$($file.Length) bytes"
Write-Output "SHA256：$($hash.Hash)"
