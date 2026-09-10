param([int]$ApiPort = 8000, [int]$WebPort = 4173)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$workRoot = (Resolve-Path (Join-Path $projectRoot '../..')).Path
$runtimeRoot = Join-Path $workRoot 'private/runtime'
foreach ($port in @($ApiPort, $WebPort)) {
    if ($port -lt 1024 -or $port -gt 65535) { throw 'Use a port between 1024 and 65535.' }
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use; existing services were not changed."
    }
}
if ($ApiPort -eq $WebPort) { throw 'API and web ports must differ.' }
$apiPython = Join-Path $projectRoot 'services/api/.venv/Scripts/python.exe'
$viteEntry = Join-Path $projectRoot 'apps/web/node_modules/vite/bin/vite.js'
if (!(Test-Path -LiteralPath $apiPython) -or !(Test-Path -LiteralPath $viteEntry) -or
    !(Test-Path -LiteralPath (Join-Path $projectRoot 'apps/web/dist/index.html'))) {
    throw 'Install dependencies and build the platform before starting it.'
}
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$runLabel = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + ([guid]::NewGuid().ToString('N').Substring(0,8))
$priorDb = $env:TWIN_DB_PATH
$priorImports = $env:TWIN_IMPORT_ROOT
$priorPort = $env:ASTRA_LOCAL_API_PORT
$apiProcess = $null
$webProcess = $null
try {
    $env:TWIN_DB_PATH = Join-Path $runtimeRoot 'twin.db'
    $env:TWIN_IMPORT_ROOT = Join-Path $runtimeRoot 'imports'
    $env:ASTRA_LOCAL_API_PORT = [string]$ApiPort
    $apiProcess = Start-Process -FilePath $apiPython -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port',"$ApiPort") `
        -WorkingDirectory (Join-Path $projectRoot 'services/api') -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeRoot "$runLabel-api.log") `
        -RedirectStandardError (Join-Path $runtimeRoot "$runLabel-api-error.log")
    $nodePath = (Get-Command node -ErrorAction Stop).Source
    $webProcess = Start-Process -FilePath $nodePath -ArgumentList @($viteEntry,'preview','--host','127.0.0.1','--port',"$WebPort",'--strictPort') `
        -WorkingDirectory (Join-Path $projectRoot 'apps/web') -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeRoot "$runLabel-web.log") `
        -RedirectStandardError (Join-Path $runtimeRoot "$runLabel-web-error.log")
    Start-Sleep -Seconds 2
    if ($apiProcess.HasExited -or $webProcess.HasExited) {
        throw "A service exited during startup; see $runtimeRoot."
    }
    $record = @{ apiPid = $apiProcess.Id; webPid = $webProcess.Id; apiPort = $ApiPort; webPort = $WebPort;
        startedAt = (Get-Date).ToString('o'); project = $projectRoot; runtime = $runtimeRoot }
    $record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimeRoot "$runLabel-processes.json") -Encoding utf8
    $record | ConvertTo-Json
} catch {
    if ($null -ne $apiProcess -and !$apiProcess.HasExited) { $apiProcess.Kill() }
    if ($null -ne $webProcess -and !$webProcess.HasExited) { $webProcess.Kill() }
    throw
} finally {
    $env:TWIN_DB_PATH = $priorDb
    $env:TWIN_IMPORT_ROOT = $priorImports
    $env:ASTRA_LOCAL_API_PORT = $priorPort
}
