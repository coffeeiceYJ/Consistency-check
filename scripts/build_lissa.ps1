param([string]$JavaHome = $env:JAVA_HOME, [string]$Maven='')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$workspace = Split-Path $taskRoot -Parent
. (Join-Path $PSScriptRoot 'java21.ps1')
$JavaHome=Find-LissaJava21 -Preferred $JavaHome -Strict ($PSBoundParameters.ContainsKey('JavaHome'))
$env:JAVA_HOME = $JavaHome
if (-not $Maven) {
    $Maven=Join-Path $taskRoot 'tools/apache-maven-3.9.11/bin/mvn.cmd'
    if (-not (Test-Path -LiteralPath $Maven)) { $Maven=(Get-Command mvn -ErrorAction Stop).Source }
}
$repo = Join-Path $taskRoot 'tools/m2'
$source=Join-Path $taskRoot 'vendor/lissa'
if (-not (Test-Path -LiteralPath (Join-Path $source 'pom.xml'))) {
    $source=Join-Path $workspace 'references/lissa'
}
if (-not (Test-Path -LiteralPath (Join-Path $source 'pom.xml'))) {
    throw 'Initialize LiSSA first: python scripts/setup_lissa.py'
}
Push-Location $source
try {
    & $Maven -B "-Dmaven.repo.local=$repo" -DskipTests package
    if ($LASTEXITCODE -ne 0) { throw "Maven failed: $LASTEXITCODE" }
} finally { Pop-Location }
