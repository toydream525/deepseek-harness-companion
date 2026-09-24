param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Uncensored32K','UncensoredDirect32K','Original32K','Writing8K')]
    [string]$Mode,
    [string]$AppExe,
    [string]$EnginePath,
    [string]$ModelPath,
    [string]$TemplatePath,
    [switch]$DryRun,
    [switch]$OpenApp,
    [switch]$AllowUnverified
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-ExistingFile([string]$Value, [string]$Label) {
    if (-not $Value) { $Value = Read-Host $Label }
    if (-not $Value) { throw "$Label is required." }
    $Value = $Value.Trim().Trim('"')
    $item = Get-Item -LiteralPath $Value -ErrorAction Stop
    if (-not $item.PSIsContainer) { return $item.FullName }
    if ($Label -eq 'llama-server.exe or its folder') {
        $matches = @(Get-ChildItem -LiteralPath $item.FullName -Recurse -File -Filter 'llama-server.exe' -ErrorAction Stop | Select-Object -First 2)
        if ($matches.Count -eq 1) { return $matches[0].FullName }
    }
    throw "$Label must identify one file."
}

function Get-PinnedTemplate([string]$SourcePage) {
    $folder = Join-Path $env:LOCALAPPDATA 'DSH-Companion\deploy-assets\froggeric-855bffc'
    $target = Join-Path $folder 'chat_template.jinja'
    $expected = 'E57684BAE4156211A55473C5A63BE976A405A37AB5BE5AE0E5ABF1DF5349C4B2'
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -eq $expected) { return $target }
        throw ('Cached template hash mismatch; inspect ' + $target + ' ; source: ' + $SourcePage)
    }
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    $pending = Join-Path $folder ('template-' + [guid]::NewGuid().ToString('N') + '.download')
    try {
        $uri = 'https://huggingface.co/froggeric/Qwen-Fixed-Chat-Templates/resolve/855bffc/chat_template.jinja'
        Invoke-WebRequest -Uri $uri -OutFile $pending -ErrorAction Stop
        if ((Get-FileHash -LiteralPath $pending -Algorithm SHA256).Hash -ne $expected) {
            throw 'Downloaded template checksum mismatch.'
        }
        Move-Item -LiteralPath $pending -Destination $target -ErrorAction Stop
        return $target
    }
    catch {
        throw ('Could not fetch the pinned template. Download chat_template.jinja manually from ' + $SourcePage + ' and pass -TemplatePath. ' + $_.Exception.Message)
    }
    finally { Remove-Item -LiteralPath $pending -Force -ErrorAction SilentlyContinue }
}

$manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'modes.json') -Encoding UTF8 -Raw | ConvertFrom-Json
$selected = @($manifest.modes | Where-Object { $_.id -eq $Mode })
if ($selected.Count -ne 1) { throw 'Mode manifest is invalid.' }
$spec = $selected[0]
$requestFile = $null
$resultFile = $null

try {
    if ($Mode -eq 'Writing8K' -and -not $AllowUnverified) {
        $answer = Read-Host 'Writing8K is unverified and not recommended. Type Writing8K to continue'
        if ($answer -cne 'Writing8K') { throw 'Unverified mode was not approved.' }
        $AllowUnverified = $true
    }
    if ($Mode -ne 'Writing8K' -and $AllowUnverified) { throw 'AllowUnverified is only for Writing8K.' }

    if (-not $AppExe) {
        $beside = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\DSH-Companion.exe'))
        $installed = Join-Path $env:LOCALAPPDATA 'Programs\DSH-Companion\DSH-Companion.exe'
        if (Test-Path -LiteralPath $beside -PathType Leaf) { $AppExe = $beside }
        elseif (Test-Path -LiteralPath $installed -PathType Leaf) { $AppExe = $installed }
    }
    if (-not $AppExe) { throw 'DSH Companion EXE not found. Pass -AppExe with the installed or portable EXE path.' }
    $AppExe = Resolve-ExistingFile $AppExe 'DSH-Companion.exe'
    if ([IO.Path]::GetFileName($AppExe) -ine 'DSH-Companion.exe') { throw 'AppExe must be DSH-Companion.exe.' }

    if (-not $EnginePath) {
        Write-Host ('Reviewed engine: ' + $manifest.engine_page)
    }
    $EnginePath = Resolve-ExistingFile $EnginePath 'llama-server.exe or its folder'

    if (-not $ModelPath) {
        Write-Host ('Download manually: ' + $spec.model_filename)
        Write-Host ('Hugging Face: ' + $spec.model_page)
    }
    try { $ModelPath = Resolve-ExistingFile $ModelPath 'GGUF file path' }
    catch {
        Write-Host ('Required GGUF: ' + $spec.model_filename)
        Write-Host ('Hugging Face: ' + $spec.model_page)
        throw
    }
    if ([IO.Path]::GetFileName($ModelPath) -ine $spec.model_filename) {
        throw ('Wrong model file. Required: ' + $spec.model_filename + ' ; page: ' + $spec.model_page)
    }

    if (-not $TemplatePath) {
        if ($DryRun) {
            $cached = Join-Path $env:LOCALAPPDATA 'DSH-Companion\deploy-assets\froggeric-855bffc\chat_template.jinja'
            if (Test-Path -LiteralPath $cached -PathType Leaf) { $TemplatePath = $cached }
            else { throw ('Dry-run does not download files. Pass -TemplatePath or obtain the pinned template: ' + $manifest.template_page) }
        }
    }
    if (-not $TemplatePath) {
        Write-Host ('Fetching pinned chat_template.jinja from ' + $manifest.template_page)
        $TemplatePath = Get-PinnedTemplate $manifest.template_page
    }
    try { $TemplatePath = Resolve-ExistingFile $TemplatePath 'chat_template.jinja path' }
    catch {
        Write-Host ('Template page: ' + $manifest.template_page)
        throw
    }

    $request = [ordered]@{
        schema_version = 1
        mode = $Mode
        engine_path = $EnginePath
        model_path = $ModelPath
        template_path = $TemplatePath
        allow_unverified = [bool]$AllowUnverified
    }
    $requestFile = Join-Path ([IO.Path]::GetTempPath()) ('dshc-deploy-' + [guid]::NewGuid().ToString('N') + '.json')
    $resultFile = Join-Path ([IO.Path]::GetTempPath()) ('dshc-result-' + [guid]::NewGuid().ToString('N') + '.json')
    $request | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $requestFile -Encoding UTF8
    $argsLine = '--deploy-config "{0}" --result-json "{1}"' -f $requestFile,$resultFile
    if ($DryRun) { $argsLine += ' --dry-run' }
    $process = Start-Process -FilePath $AppExe -ArgumentList $argsLine -Wait -PassThru -WindowStyle Hidden
    if (-not (Test-Path -LiteralPath $resultFile -PathType Leaf)) {
        throw ('Deployment CLI returned no result file (exit ' + $process.ExitCode + '). Update the app to a version with deployment CLI.')
    }
    $result = Get-Content -LiteralPath $resultFile -Encoding UTF8 -Raw | ConvertFrom-Json
    Write-Host $result.message
    if (-not $result.success -or $process.ExitCode -ne 0) {
        foreach ($issue in @($result.errors)) { if ($issue) { Write-Warning $issue } }
        exit 1
    }
    Write-Host ('Mode: ' + $Mode + ' / preset: ' + $result.preset_id)
    if ($DryRun) { Write-Host 'Dry run only. No app configuration was changed.' }
    else {
        Write-Host 'Open Companion, select this model and preset, then start it and verify a real chat.'
        if ($OpenApp) { Start-Process -FilePath $AppExe }
    }
    exit 0
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
finally {
    if ($requestFile) { Remove-Item -LiteralPath $requestFile -Force -ErrorAction SilentlyContinue }
    if ($resultFile) { Remove-Item -LiteralPath $resultFile -Force -ErrorAction SilentlyContinue }
}
