param(
    [Parameter(Mandatory=$true)][string]$InputManifest,
    [ValidateSet('rules','live')][string]$Mode='rules',
    [string]$OutputDirectory='',
    [string]$Endpoint=$env:REQCHECK_ENDPOINT,
    [string]$Model=$env:REQCHECK_MODEL,
    [string]$KeyEnv='DEEPSEEK_API_KEY',
    [string]$Python=''
)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
$env:PYTHONPATH=Join-Path $taskRoot 'src'
$env:PYTHONUTF8='1'
if (-not $Python) {
    $Python=Join-Path $taskRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $Python)) { $Python=Join-Path $taskRoot '.venv-mcet/Scripts/python.exe' }
    if (-not (Test-Path -LiteralPath $Python)) { $Python='python' }
}
$checkArgs=@('-m','reqcheck','--input',$InputManifest,'--mode',$Mode)
if ($OutputDirectory) { $checkArgs+=@('--output',$OutputDirectory) }
if ($Mode -eq 'live') { $checkArgs+=@('--endpoint',$Endpoint,'--model',$Model,'--key-env',$KeyEnv) }
& $Python @checkArgs
exit $LASTEXITCODE
