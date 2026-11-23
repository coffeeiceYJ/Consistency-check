function Find-LissaJava21 {
    param([string]$Preferred, [bool]$Strict=$false)
    $candidates=@($Preferred)
    if (-not $Strict) {
        $candidates+=@($env:JAVA_HOME)
        $javac=Get-Command javac -ErrorAction SilentlyContinue
        if ($javac) { $candidates+=Split-Path (Split-Path $javac.Source -Parent) -Parent }
        $candidates+=@('D:\PyCharm Community Edition 2025.1.2\jbr')
    }
    foreach ($candidate in $candidates) {
        if (-not $candidate -or -not (Test-Path -LiteralPath (Join-Path $candidate 'bin/javac.exe'))) { continue }
        $process=New-Object System.Diagnostics.Process
        $process.StartInfo.FileName=Join-Path $candidate 'bin/javac.exe'
        $process.StartInfo.Arguments='-version'
        $process.StartInfo.UseShellExecute=$false
        $process.StartInfo.CreateNoWindow=$true
        $process.StartInfo.RedirectStandardOutput=$true
        $process.StartInfo.RedirectStandardError=$true
        try {
            [void]$process.Start()
            $version=$process.StandardOutput.ReadToEnd()+$process.StandardError.ReadToEnd()
            $process.WaitForExit()
            if ($version -match 'javac\s+(\d+)' -and [int]$Matches[1] -ge 21) { return $candidate }
        } finally { $process.Dispose() }
    }
    throw 'LiSSA requires JDK 21+. Pass -JavaHome with a valid JDK path.'
}
