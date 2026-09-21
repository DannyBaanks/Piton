[CmdletBinding()]
param(
    [switch]$Publish,
    [switch]$FullSuite,
    [string]$Remote = "origin"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
Set-Location -LiteralPath $RepoRoot

$ResultsDir = Join-Path $RepoRoot "validation\windows"
New-Item -ItemType Directory -Path $ResultsDir -Force | Out-Null

$RunId = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$ArchiveStem = "windowsvalidate-$RunId"
$ArchiveJson = Join-Path $ResultsDir "$ArchiveStem.json"
$ArchiveMarkdown = Join-Path $ResultsDir "$ArchiveStem.md"
$ArchiveLog = Join-Path $ResultsDir "$ArchiveStem.log"
$LatestJson = Join-Path $ResultsDir "latest.json"
$LatestMarkdown = Join-Path $ResultsDir "latest.md"
$LatestLog = Join-Path $ResultsDir "latest.log"

$Steps = New-Object System.Collections.ArrayList
$LogLines = New-Object System.Collections.ArrayList
Set-Content -LiteralPath $ArchiveLog -Value "" -Encoding UTF8

function Add-RunLog {
    param([string]$Line)
    [void]$LogLines.Add($Line)
}

function Add-ManualStep {
    param(
        [string]$Name,
        [string]$Command,
        [string]$Status,
        [string]$Output
    )
    $step = [pscustomobject][ordered]@{
        name = $Name
        command = $Command
        exit_code = if ($Status -eq "PASS") { 0 } else { 1 }
        status = $Status
        duration_seconds = 0
        output = $Output
    }
    [void]$Steps.Add($step)
    Add-RunLog ("### {0} [{1}]`n{2}" -f $Name, $Status, $Output)
    return $step
}

function Invoke-ValidationStep {
    param(
        [string]$Name,
        [string]$Executable,
        [string[]]$ArgumentList
    )

    $started = [DateTime]::UtcNow
    $command = "$Executable $($ArgumentList -join ' ')"
    $outputLines = @()
    $exitCode = 1
    try {
        $outputLines = @(& $Executable @ArgumentList 2>&1 | ForEach-Object { $_.ToString() })
        $exitCode = [int]$LASTEXITCODE
    } catch {
        $outputLines += $_.Exception.Message
        $exitCode = 1
    }
    $duration = [math]::Round(([DateTime]::UtcNow - $started).TotalSeconds, 3)
    $status = if ($exitCode -eq 0) { "PASS" } else { "FAIL" }
    $output = $outputLines -join "`n"
    $step = [pscustomobject][ordered]@{
        name = $Name
        command = $command
        exit_code = $exitCode
        status = $status
        duration_seconds = $duration
        output = $output
    }
    [void]$Steps.Add($step)
    Add-RunLog ("### {0} [{1}] exit={2}`n{3}" -f $Name, $status, $exitCode, $output)
    return $step
}

$gitCommit = "unknown"
$worktreeStatus = "unknown"
try {
    $gitCommit = (& git rev-parse HEAD 2>$null).Trim()
    $worktreeStatus = ((& git status --porcelain 2>$null) -join "`n")
} catch {
    $worktreeStatus = $_.Exception.Message
}

$toolVersions = [ordered]@{}
foreach ($toolName in @("git", "nasm", "gcc")) {
    $tool = Get-Command $toolName -ErrorAction SilentlyContinue
    if ($null -eq $tool) {
        $toolVersions[$toolName] = "MISSING"
        [void](Add-ManualStep "tool-$toolName" $toolName "FAIL" "Required tool not found on PATH")
        continue
    }
    $versionArgs = if ($toolName -eq "nasm") { @("-v") } else { @("--version") }
    $versionOutput = @(& $tool.Source @versionArgs 2>&1 | Select-Object -First 1)
    $toolVersions[$toolName] = ($versionOutput -join " ").Trim()
}

if ($env:OS -ne "Windows_NT") {
    [void](Add-ManualStep "host-platform" "Windows_NT" "FAIL" "windowsvalidate.ps1 must run on Windows")
}

$pythonCommand = Get-Command py -ErrorAction SilentlyContinue
$pythonExecutable = $null
$pythonPrefix = @()
if ($null -ne $pythonCommand) {
    $pythonExecutable = $pythonCommand.Source
    $pythonPrefix = @("-3.12")
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $pythonCommand) {
        $pythonExecutable = $pythonCommand.Source
    }
}

if ($null -eq $pythonExecutable) {
    [void](Add-ManualStep "python" "py -3.12/python" "FAIL" "CPython 3.12 was not found")
} else {
    [void](Invoke-ValidationStep "python-version" $pythonExecutable ($pythonPrefix + @("--version")))
    [void](Invoke-ValidationStep "windows-focused-corpus" $pythonExecutable ($pythonPrefix + @("-m", "unittest", "tests.test_windows_validate", "tests.test_phase14", "-v")))
    if ($FullSuite) {
        [void](Invoke-ValidationStep "windows-regression-suite" $pythonExecutable ($pythonPrefix + @("-m", "unittest", "tests.test_phase5", "tests.test_phase14", "-q")))
    }
}

$failedSteps = @($Steps | Where-Object { $_.status -ne "PASS" })
$overallStatus = if ($failedSteps.Count -eq 0) { "PASS" } else { "FAIL" }
$receipt = [ordered]@{
    schema = "piton-windows-validation-v1"
    run_id = $RunId
    status = $overallStatus
    host = [ordered]@{
        computer = $env:COMPUTERNAME
        os = [Environment]::OSVersion.VersionString
        architecture = $env:PROCESSOR_ARCHITECTURE
    }
    repository = [ordered]@{
        commit = $gitCommit
        worktree_dirty = ($worktreeStatus.Length -gt 0)
    }
    python = $pythonExecutable
    tools = $toolVersions
    commands = @($Steps | ForEach-Object { $_ })
    notes = @(
        "Windows results are authoritative only for this host and commit.",
        "A PASS here does not claim Linux parity; combine with the Linux receipt.",
        "The script never stages implementation files, only validation/windows/*."
    )
}

$receiptJson = $receipt | ConvertTo-Json -Depth 8
Set-Content -LiteralPath $ArchiveJson -Value $receiptJson -Encoding UTF8
Set-Content -LiteralPath $LatestJson -Value $receiptJson -Encoding UTF8

$stepRows = @($Steps | ForEach-Object {
    "| {0} | {1} | {2} | {3} |" -f $_.name, $_.status, $_.exit_code, $_.duration_seconds
}) -join "`n"
$failureNote = if ($failedSteps.Count -eq 0) { "No failed steps." } else { "Failed: " + (($failedSteps | ForEach-Object { $_.name }) -join ", ") }
$markdown = @"
# Windows validation

- Status: **$overallStatus**
- Run: `$RunId`
- Commit: `$gitCommit`
- Computer: `$($env:COMPUTERNAME)`
- Python: `$pythonExecutable`
- Worktree dirty before run: `$($worktreeStatus.Length -gt 0)`

## Steps

| Step | Status | Exit | Seconds |
|---|---|---:|---:|
$stepRows

$failureNote

Raw output: [`$ArchiveStem.log`](./$ArchiveStem.log)
Machine-readable receipt: [`$ArchiveStem.json`](./$ArchiveStem.json)
"@
Set-Content -LiteralPath $ArchiveMarkdown -Value $markdown -Encoding UTF8
Set-Content -LiteralPath $LatestMarkdown -Value $markdown -Encoding UTF8

$LogLines | Set-Content -LiteralPath $ArchiveLog -Encoding UTF8
Copy-Item -LiteralPath $ArchiveLog -Destination $LatestLog -Force

$publishStatus = "NOT_REQUESTED"
if ($Publish) {
    try {
        $branch = (& git branch --show-current 2>$null).Trim()
        if ([string]::IsNullOrWhiteSpace($branch)) {
            throw "Cannot publish from a detached HEAD"
        }
        $publishPaths = @(
            "validation/windows/$ArchiveStem.json",
            "validation/windows/$ArchiveStem.md",
            "validation/windows/$ArchiveStem.log",
            "validation/windows/latest.json",
            "validation/windows/latest.md",
            "validation/windows/latest.log"
        )
        & git add -- $publishPaths
        if ($LASTEXITCODE -ne 0) { throw "git add failed" }
        & git commit -m "chore: publish Windows validation $RunId"
        if ($LASTEXITCODE -ne 0) { throw "git commit failed" }
        & git push $Remote $branch
        if ($LASTEXITCODE -ne 0) { throw "git push failed" }
        $publishStatus = "PUBLISHED"
    } catch {
        $publishStatus = "PUBLISH_FAILED: " + $_.Exception.Message
        Write-Error $publishStatus
    }
}

Write-Host "Windows validation: $overallStatus"
Write-Host "Receipt: $LatestJson"
Write-Host "Publish: $publishStatus"

if ($overallStatus -ne "PASS" -or ($Publish -and $publishStatus -ne "PUBLISHED")) {
    exit 1
}
exit 0
