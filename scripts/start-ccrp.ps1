[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string] $Config,

    [string] $Python = "python",

    [switch] $NoDoctor
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $projectRoot

function Fail([string] $Message) {
    Write-Error $Message
    exit 1
}

$ccrpScript = Join-Path $projectRoot "ccrp.py"
if (-not (Test-Path -LiteralPath $ccrpScript -PathType Leaf)) {
    Fail "找不到 ccrp.py：$projectRoot"
}

if (-not $Config -and $env:CCRP_CONFIG) {
    $Config = $env:CCRP_CONFIG
}

if (-not $Config) {
    $defaultConfig = Join-Path $projectRoot "ccrp.config.json"
    if (Test-Path -LiteralPath $defaultConfig -PathType Leaf) {
        $Config = $defaultConfig
    } else {
        $candidates = @(
            Get-ChildItem -LiteralPath $projectRoot -Filter "ccrp*.json" -File |
                Sort-Object Name
        )

        if ($candidates.Count -eq 1) {
            $Config = $candidates[0].FullName
        } elseif ($candidates.Count -eq 0) {
            Fail "没有找到 ccrp 配置文件。请先运行：python .\ccrp.py init --out ccrp.config.json ..."
        } else {
            $names = ($candidates | ForEach-Object { $_.Name }) -join ", "
            Fail "找到多个配置文件（$names），为避免使用错误端口，必须使用 -Config 明确指定。"
        }
    }
}

$configPath = Resolve-Path -LiteralPath $Config -ErrorAction SilentlyContinue
if (-not $configPath) {
    Fail "配置文件不存在：$Config"
}
$configPath = $configPath.Path

$pythonCommand = Get-Command $Python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    Fail "找不到 Python 命令 '$Python'。请安装 Python 3，或使用 -Python 指定 python.exe 的完整路径。"
}
$pythonPath = $pythonCommand.Source

Write-Host "项目目录：$projectRoot" -ForegroundColor Cyan
Write-Host "配置文件：$configPath" -ForegroundColor Cyan
Write-Host "Python：$pythonPath" -ForegroundColor Cyan

if (-not $NoDoctor) {
    Write-Host "正在检查配置..." -ForegroundColor Yellow
    & $pythonPath $ccrpScript doctor -c $configPath
    if ($LASTEXITCODE -ne 0) {
        Fail "ccrp doctor 检查失败，已停止启动。"
    }
}

Write-Host "实际 SSH 反向转发命令：" -ForegroundColor Yellow
& $pythonPath $ccrpScript print-ssh -c $configPath
if ($LASTEXITCODE -ne 0) {
    Fail "无法生成 SSH 反向转发命令，已停止启动。"
}

Write-Host "正在启动 SSH 反向隧道。关闭此窗口会停止隧道。" -ForegroundColor Green
Write-Host "按 Ctrl+C 可停止。" -ForegroundColor DarkGray
Write-Host ""

& $pythonPath $ccrpScript up -c $configPath
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Host "ccrp 已退出，退出码：$exitCode" -ForegroundColor Red
    Write-Host "请检查上面的 SSH 输出、本地 cc-switch 端口和服务器端口。" -ForegroundColor Yellow
    exit $exitCode
}

Write-Host "ccrp 已正常退出。" -ForegroundColor Yellow