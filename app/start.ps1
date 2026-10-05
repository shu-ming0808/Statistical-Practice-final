[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'
$appRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $appRoot '..'))
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$frontendPath = Join-Path $appRoot 'frontend'
$runtimePath = Join-Path $appRoot '.runtime'
$statePath = Join-Path $runtimePath 'server.json'
$siteUrl = 'http://127.0.0.1:8100'
$requiredCommand = '-m uvicorn app.backend.main:app --host 127.0.0.1 --port 8100'
$appDirArgument = '--app-dir "' + $projectRoot + '"'

function Get-BasePython {
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        throw 'Python environment is missing. From the project root run: uv sync --inexact --extra app'
    }
    $basePath = & $pythonPath -X utf8 -c 'import sys; print(sys._base_executable)'
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $basePath -PathType Leaf)) {
        throw 'Could not identify the Python runtime for this project.'
    }
    return $basePath.Trim()
}

function Get-ServerProcess([int]$ProcessId) {
    if ($ProcessId -le 0) { return $null }
    return Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
}

function Assert-OwnedProcess($State, $ServerProcess) {
    $allowedExecutables = @($pythonPath, (Get-BasePython))
    if ($State.project_root -ne $projectRoot -or
        $State.executable -notin $allowedExecutables -or
        $ServerProcess.ExecutablePath -ne $State.executable -or
        $ServerProcess.CommandLine -ne $State.command_line -or
        -not $ServerProcess.CommandLine.Contains($requiredCommand) -or
        -not $ServerProcess.CommandLine.Contains($appDirArgument) -or
        $ServerProcess.CreationDate.ToUniversalTime().Ticks.ToString() -ne $State.created_at_ticks) {
        throw 'Saved PID no longer identifies this app. No process was stopped. Inspect app/.runtime/server.json.'
    }
}

if ($Build -and $Stop) { throw 'Use -Build or -Stop, not both.' }

$state = $null
$serverProcess = $null
if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $serverProcess = Get-ServerProcess -ProcessId ([int]$state.process_id)
    if ($null -ne $serverProcess) {
        Assert-OwnedProcess -State $state -ServerProcess $serverProcess
    } else {
        Remove-Item -LiteralPath $statePath
    }
}

if ($Stop) {
    if ($null -eq $serverProcess) {
        Write-Host 'This app is not running through app/start.ps1.'
        exit 0
    }
    # Recheck identity immediately before stopping, including process creation time.
    $serverProcess = Get-ServerProcess -ProcessId ([int]$state.process_id)
    if ($null -ne $serverProcess) {
        Assert-OwnedProcess -State $state -ServerProcess $serverProcess
        Stop-Process -Id $serverProcess.ProcessId -ErrorAction Stop
    }
    Remove-Item -LiteralPath $statePath
    Write-Host 'Stopped this app. MySQL and Tailscale were left running.'
    exit 0
}

if ($null -ne $serverProcess) {
    if ($Build) { throw 'Stop the app with .\app\start.ps1 -Stop before rebuilding.' }
    Write-Host "This app is already running: $siteUrl"
    exit 0
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Python environment is missing. From the project root run: uv sync --inexact --extra app'
}
$dependenciesReady = $false
try {
    & $pythonPath -c 'import fastapi, uvicorn' 2>$null
    $dependenciesReady = $LASTEXITCODE -eq 0
} catch { $dependenciesReady = $false }
if (-not $dependenciesReady) {
    throw 'App dependencies are missing. From the project root run: uv sync --inexact --extra app'
}

if ($Build) {
    $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($null -eq $npmCommand) { throw 'Install Node.js 22.12+ (or a supported newer LTS) and reopen PowerShell.' }
    Push-Location -LiteralPath $frontendPath
    try {
        & $npmCommand.Source ci
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed; the app was not started.' }
        & $npmCommand.Source run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed; the app was not started.' }
    } finally {
        Pop-Location
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $frontendPath 'dist\index.html') -PathType Leaf)) {
    throw 'Frontend build is missing. Run .\app\start.ps1 -Build (downloads dependencies), or build app/frontend manually.'
}
if (@(Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue).Count -gt 0) {
    throw 'Port 8100 is in use. No existing process was stopped. Inspect the port owner before retrying.'
}

New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
$argumentLine = '-X utf8 -m uvicorn app.backend.main:app --host 127.0.0.1 --port 8100 --app-dir "' + $projectRoot + '"'
$startedProcess = Start-Process -FilePath $pythonPath -ArgumentList $argumentLine -WorkingDirectory $projectRoot `
    -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimePath 'server.out.log') `
    -RedirectStandardError (Join-Path $runtimePath 'server.err.log')

$serverProcess = Get-ServerProcess -ProcessId $startedProcess.Id
if ($null -eq $serverProcess) { throw 'Server exited before startup. Read app/.runtime/server.err.log.' }
# On Windows, the venv executable is a redirector. Save the actual Python child
# so stopping the app cannot leave an orphaned Uvicorn listener behind.
$basePython = Get-BasePython
if ($basePython -ne $pythonPath) {
    $childDeadline = [DateTime]::UtcNow.AddSeconds(5)
    $pythonChild = $null
    while ([DateTime]::UtcNow -lt $childDeadline -and $null -eq $pythonChild) {
        $pythonChild = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($startedProcess.Id)" |
            Where-Object {
                $_.ExecutablePath -eq $basePython -and
                $_.CommandLine.Contains($requiredCommand) -and
                $_.CommandLine.Contains($appDirArgument)
            } | Select-Object -First 1
        if ($null -eq $pythonChild) { Start-Sleep -Milliseconds 100 }
    }
    if ($null -eq $pythonChild) {
        throw 'Could not identify the app Python child. Inspect app/.runtime/server.err.log and port 8100.'
    }
    $serverProcess = $pythonChild
}
$state = [ordered]@{
    project_root = $projectRoot
    executable = $serverProcess.ExecutablePath
    process_id = $serverProcess.ProcessId
    created_at_ticks = $serverProcess.CreationDate.ToUniversalTime().Ticks.ToString()
    command_line = $serverProcess.CommandLine
}
$state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8

$ready = $false
$deadline = [DateTime]::UtcNow.AddSeconds(20)
while ([DateTime]::UtcNow -lt $deadline) {
    if ($null -eq (Get-ServerProcess -ProcessId ([int]$state.process_id))) {
        Remove-Item -LiteralPath $statePath
        throw 'Server exited during startup. Read app/.runtime/server.err.log.'
    }
    try {
        $page = Invoke-WebRequest -Uri $siteUrl -UseBasicParsing -TimeoutSec 2
        if ($page.StatusCode -eq 200) { $ready = $true; break }
    } catch {
        Start-Sleep -Milliseconds 300
    }
}
if (-not $ready) {
    throw 'The app did not become ready within 20 seconds. Read app/.runtime/server.err.log; use -Stop before retrying.'
}
Write-Host "Website: $siteUrl"
Write-Host "Database status: $siteUrl/api/health"
Write-Host 'Stop: .\app\start.ps1 -Stop'
Write-Host 'Logs: app/.runtime/server.out.log and server.err.log'
