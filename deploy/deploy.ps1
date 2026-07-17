# MemePilot 部署引导脚本（Windows PowerShell）
#
# 从 GitHub 原始文件拉取运行时所需的三项：
#   - napcat/entrypoint.sh（同时创建 napcat/config、napcat/qq 空目录供卷挂载）
#   - docker-compose.yml
#   - .env.example（自动改名为 .env）
#
# 用法：
#   .\deploy.ps1 [-TargetDir <目录>]            # 默认当前目录
#   $env:REPO_REF="v1.0.0"; .\deploy.ps1        # 指定仓库引用（默认 main）
#
# 依赖：Windows PowerShell 5.1+（系统自带 Invoke-WebRequest）
# 幂等：已存在的文件一律跳过（.env 永不覆盖），可安全重复执行。

#Requires -Version 5.1

[CmdletBinding()]
param(
    [string]$TargetDir = "."
)

$ErrorActionPreference = "Stop"

$Repo = "northhalf/meme-pilot"
$Ref = if ($env:REPO_REF) { $env:REPO_REF } else { "main" }
$RawBase = "https://raw.githubusercontent.com/$Repo/$Ref"

# GitHub 要求 TLS 1.2（Windows PowerShell 5.1 默认可能更低）
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch {
    Write-Host "[memepilot] 警告：无法设置 TLS 1.2，下载可能失败" -ForegroundColor Yellow
}

if (-not (Test-Path -LiteralPath $TargetDir)) {
    New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
}
# 全程使用绝对路径：Set-Location 只改 PowerShell 驱动器位置，不会同步
# [Environment]::CurrentDirectory，导致 [System.IO.File] 等相对路径解析到错误位置。
$Root = (Resolve-Path -LiteralPath $TargetDir).Path

Write-Host "[memepilot] 目标目录: $Root"
Write-Host "[memepilot] 仓库引用: $Repo @ $Ref"

function Invoke-RawDownload {
    # 下载原始文件；失败时清理空文件并返回 $false
    param(
        [string]$Url,
        [string]$OutFile
    )
    try {
        Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing -TimeoutSec 60 -ErrorAction Stop | Out-Null
        return $true
    } catch {
        if (Test-Path -LiteralPath $OutFile) { Remove-Item -LiteralPath $OutFile -Force }
        return $false
    }
}

function ConvertTo-Lf {
    # 将行尾规范为 LF，避免 Windows 下载引入 CRLF 破坏容器内 /bin/sh 执行 entrypoint.sh
    # 入参必须是绝对路径，绕开 .NET CurrentDirectory 与 PowerShell 位置不一致的坑。
    param([Parameter(Mandatory)][string]$Path)
    $content = [System.IO.File]::ReadAllText($Path)
    $normalized = $content -replace "`r`n", "`n"
    [System.IO.File]::WriteAllText($Path, $normalized)
}

# 1. napcat/entrypoint.sh（并确保 config、qq 目录存在以供卷挂载）
New-Item -ItemType Directory -Force -Path (Join-Path $Root "napcat/config"), (Join-Path $Root "napcat/qq") | Out-Null
$entrypoint = Join-Path $Root "napcat/entrypoint.sh"
if (Test-Path -LiteralPath $entrypoint) {
    Write-Host "[memepilot] napcat/entrypoint.sh 已存在，跳过"
} else {
    if (Invoke-RawDownload -Url "$RawBase/napcat/entrypoint.sh" -OutFile $entrypoint) {
        ConvertTo-Lf -Path $entrypoint
        Write-Host "[memepilot] napcat/entrypoint.sh 已拉取"
    } else {
        Write-Host "[memepilot] 拉取 napcat/entrypoint.sh 失败（检查网络或 REPO_REF=$Ref）" -ForegroundColor Red
        exit 1
    }
}

# 2. docker-compose.yml
$compose = Join-Path $Root "docker-compose.yml"
if (Test-Path -LiteralPath $compose) {
    Write-Host "[memepilot] docker-compose.yml 已存在，跳过"
} else {
    if (Invoke-RawDownload -Url "$RawBase/docker-compose.yml" -OutFile $compose) {
        Write-Host "[memepilot] docker-compose.yml 已拉取"
    } else {
        Write-Host "[memepilot] 拉取 docker-compose.yml 失败" -ForegroundColor Red
        exit 1
    }
}

# 3. .env（由 .env.example 改名而来；已存在则保留不动）
$envFile = Join-Path $Root ".env"
if (Test-Path -LiteralPath $envFile) {
    Write-Host "[memepilot] .env 已存在，跳过（保留本地配置）"
} else {
    if (Invoke-RawDownload -Url "$RawBase/.env.example" -OutFile $envFile) {
        Write-Host "[memepilot] .env 已生成（源自 .env.example）"
    } else {
        Write-Host "[memepilot] 拉取 .env.example 失败" -ForegroundColor Red
        exit 1
    }
}

Write-Host "[memepilot] 拉取完成"
