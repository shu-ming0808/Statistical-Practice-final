param(
    [string]$StartDate = '2026-07-15',
    [string]$EndDate = '2026-08-15',
    [int]$MaxPages = 20,
    [int]$MaxArticles = 500,
    [double]$MaxMinutes = 15,
    [string[]]$Board,
    [string[]]$Query
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Run uv sync --group notebook --extra social --extra web in the project first.'
}
$taskArgs = @('-X', 'utf8', (Join-Path $projectRoot 'src\ptt_web_collect.py'),
    '--start-date', $StartDate, '--end-date', $EndDate, '--max-pages', $MaxPages,
    '--max-articles', $MaxArticles, '--max-minutes', $MaxMinutes)
foreach ($boardName in $Board) { $taskArgs += @('--board', $boardName) }
foreach ($queryText in $Query) { $taskArgs += @('--query', $queryText) }
& $pythonPath @taskArgs
exit $LASTEXITCODE
