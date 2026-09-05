$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$packageRoot = Join-Path $projectRoot 'build/final_alignment_validation/packaging-smoke'
New-Item -ItemType Directory -Force -Path $packageRoot | Out-Null
$pythonExe = (Get-Command python).Source
$pythonDir = Split-Path -Parent $pythonExe
$originalPath = $env:PATH
$originalQtPlatform = $env:QT_QPA_PLATFORM
$originalDataRoot = $env:SILVERSTAR_GSHC_DATA_ROOT
$smokeProcess = $null
Push-Location -LiteralPath $projectRoot
try {
    $env:PATH = "$pythonDir;$pythonDir\Scripts;$env:WINDIR\System32;$env:WINDIR"
    # Windows PowerShell wraps native stderr as ErrorRecord, including INFO logs.
    # Check the native exit code instead of treating every log line as an error.
    $ErrorActionPreference = 'Continue'
    & $pythonExe -m PyInstaller --noconfirm --distpath "$packageRoot/dist" --workpath "$packageRoot/work" SilverStar_GSHC.spec *> "$packageRoot/build.log"
    $buildExitCode = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($buildExitCode -ne 0) { throw "PyInstaller failed; see $packageRoot/build.log" }
    $env:QT_QPA_PLATFORM = 'offscreen'
    $env:SILVERSTAR_GSHC_DATA_ROOT = Join-Path $packageRoot 'runtime-data'
    $exe = Join-Path $packageRoot 'dist/SilverStar_GSHC/SilverStar_GSHC.exe'
    $stdoutPath = Join-Path $packageRoot 'stdout.log'
    $stderrPath = Join-Path $packageRoot 'stderr.log'
    $smokeProcess = Start-Process -FilePath $exe -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    Start-Sleep -Seconds 8
    $smokeProcess.Refresh()
    $alive = -not $smokeProcess.HasExited
    $windowTitle = $smokeProcess.MainWindowTitle
    # Stop only the child created for this smoke, then read flushed error output.
    if ($alive) {
        Stop-Process -Id $smokeProcess.Id
        $smokeProcess.WaitForExit()
    }
    $errorText = [string](Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue)
    $passed = $alive -and $windowTitle -notmatch 'Unhandled exception' -and $errorText -notmatch 'Traceback|ImportError|DLL load failed|Failed to execute script'
    $report = [ordered]@{
        passed = [bool]$passed
        checked_at = (Get-Date).ToString('o')
        exe = $exe
        alive_after_seconds = 8
        alive = $alive
        window_title = $windowTitle
        stderr_bytes = (Get-Item -LiteralPath $stderrPath).Length
        stderr = $errorText
        stopped_own_child = $alive
    }
    $report | ConvertTo-Json | Set-Content -LiteralPath "$packageRoot/startup.json" -Encoding UTF8
    if (-not $passed) { throw "Packaged startup failed; see $packageRoot/startup.json" }
    Write-Output "Packaging and startup smoke passed: $exe"
} finally {
    if ($null -ne $smokeProcess -and -not $smokeProcess.HasExited) {
        Stop-Process -Id $smokeProcess.Id
    }
    $env:PATH = $originalPath
    $env:QT_QPA_PLATFORM = $originalQtPlatform
    $env:SILVERSTAR_GSHC_DATA_ROOT = $originalDataRoot
    Pop-Location
}
