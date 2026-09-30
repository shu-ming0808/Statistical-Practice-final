param(
    [string]$StartDate = '2026-07-15',
    [string]$EndDate = '2026-08-15',
    [int]$MaxScrolls = 200,
    [double]$MaxMinutes = 15,
    [int]$MaxPosts = 2000,
    [double]$IdleSeconds = 90,
    [switch]$ManualFilters
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Run uv sync --group notebook --extra social --extra web in the project first.'
}
$taskArgs = @('-X', 'utf8', (Join-Path $projectRoot 'src\threads_web_collect.py'),
    '--start-date', $StartDate, '--end-date', $EndDate, '--max-scrolls', $MaxScrolls,
    '--max-minutes', $MaxMinutes, '--max-posts', $MaxPosts, '--idle-seconds', $IdleSeconds,
    '--sort', 'top')
if ($ManualFilters) { $taskArgs += '--manual-filters' }
& $pythonPath @taskArgs
exit $LASTEXITCODE
