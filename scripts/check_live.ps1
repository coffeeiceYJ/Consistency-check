param(
    [Parameter(Mandatory=$true)][string]$InputManifest,
    [string]$OutputDirectory='',
    [string]$Model='deepseek-flash',
    [ValidateSet('','auto','classified','all')][string]$Routing='',
    [switch]$NoCache,
    [switch]$DebugArtifacts,
    [switch]$ExportCsv
)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
$oldKey=$env:DEEPSEEK_API_KEY
$secureKey=$null
$keyBuffer=[IntPtr]::Zero
try {
    if (-not $env:DEEPSEEK_API_KEY) {
        $secureKey=Read-Host 'Enter a NEW DeepSeek API key (hidden; used only in this process)' -AsSecureString
        $keyBuffer=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
        $env:DEEPSEEK_API_KEY=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyBuffer)
    }
    & (Join-Path $PSScriptRoot 'check.ps1') -InputManifest $InputManifest -Mode live -OutputDirectory $OutputDirectory -Endpoint 'https://api.deepseek.com/chat/completions' -Model $Model -Routing $Routing -NoCache:$NoCache -DebugArtifacts:$DebugArtifacts -ExportCsv:$ExportCsv
    $checkExit=$LASTEXITCODE
} finally {
    $env:DEEPSEEK_API_KEY=$oldKey
    if ($keyBuffer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyBuffer) }
    if ($secureKey) { $secureKey.Dispose() }
}
exit $checkExit
