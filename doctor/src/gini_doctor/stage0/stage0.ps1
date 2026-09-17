# gini-doctor Stage 0 — find a Python that can run the doctor. Windows.
#
# The same program as stage0.sh, for Windows PowerShell 5.1 (which ships with Windows 10 and 11)
# and PowerShell 7. GINI needs Python, so a machine with no usable Python is the diagnosis, not a
# failure: Stage 0 records every interpreter it finds, picks the best one that meets the floor, and
# hands off to Stage 1. If nothing qualifies it writes the same JSON report Stage 1 would.
#
#   powershell -ExecutionPolicy Bypass -File stage0.ps1 [stage 1 arguments…]
#   irm <url>/stage0.ps1 | iex
#
# Environment: GINI_DOCTOR_PYTHON, GINI_DOCTOR_MIN_PYTHON, GINI_HEALTHCENTER (see stage0.sh).
#
# Never blocks. Every interpreter is started with stdin closed and a 15 s limit, and the Microsoft
# Store "python" alias (a stub under WindowsApps that offers to install Python) is recorded and
# skipped rather than run.

$ErrorActionPreference = 'Continue'
$BuiltinMinPython = '3.8'
$Facts = New-Object 'System.Collections.Generic.List[object]'

function Protect-Text([string]$Text) {
    # Usernames and home paths do not leave the machine (the same rule as Stage 1's redact.py).
    if ($null -eq $Text) { return '' }
    $out = $Text -replace "[`r`n`t]+", ' '
    foreach ($h in @($HOME, $env:USERPROFILE) | Where-Object { $_ -and $_.Length -gt 1 } | Sort-Object Length -Descending) {
        $out = $out.Replace($h, '~').Replace($h.Replace('\', '/'), '~')
    }
    $u = $env:USERNAME; if (-not $u) { $u = $env:USER }
    if ($u -and $u.Length -ge 3) {
        $out = [regex]::Replace($out, '(?<![A-Za-z0-9_.-])' + [regex]::Escape($u) + '(?![A-Za-z0-9_-])', '<user>')
    }
    return $out.Trim()
}

function Add-Fact([string]$Key, [string]$Value) {
    $Facts.Add([pscustomobject]@{ Key = $Key; Value = (Protect-Text $Value) })
}

function Test-VersionAtLeast([string]$A, [string]$B) {
    $x = @($A.Split('.') | ForEach-Object { [int]($_ -replace '\D', '0') })
    $y = @($B.Split('.') | ForEach-Object { [int]($_ -replace '\D', '0') })
    for ($i = 0; $i -lt 3; $i++) {
        $xi = if ($i -lt $x.Count) { $x[$i] } else { 0 }
        $yi = if ($i -lt $y.Count) { $y[$i] } else { 0 }
        if ($xi -gt $yi) { return $true }
        if ($xi -lt $yi) { return $false }
    }
    return $true
}

function Invoke-Bounded([string]$Exe, [string[]]$Arguments, [int]$TimeoutMs = 15000) {
    # Start a process with stdin closed and a time limit. Returns stdout, or $null.
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $Exe
        $psi.Arguments = ($Arguments | ForEach-Object { if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } }) -join ' '
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.RedirectStandardInput = $true
        $psi.CreateNoWindow = $true
        $p = [System.Diagnostics.Process]::Start($psi)
        $p.StandardInput.Close()
        $stdout = $p.StandardOutput.ReadToEndAsync()
        $null = $p.StandardError.ReadToEndAsync()
        if (-not $p.WaitForExit($TimeoutMs)) { try { $p.Kill() } catch {} ; return $null }
        if ($p.ExitCode -ne 0) { return $null }
        return $stdout.Result.Trim()
    } catch { return $null }
}

# ------------------------------------------------------------------ platform
$IsWin = ($env:OS -eq 'Windows_NT')
if ($IsWin) { $Platform = 'windows' }
elseif ($PSVersionTable.PSVersion.Major -ge 6 -and $IsMacOS) { $Platform = 'macos' }
else { $Platform = 'linux' }
Add-Fact 'platform' $Platform
Add-Fact 'powershell' ($PSVersionTable.PSVersion.ToString())
if ($IsWin) { Add-Fact 'uname' ("Windows " + [Environment]::OSVersion.Version.ToString() + " " + $env:PROCESSOR_ARCHITECTURE) }

# ------------------------------------------------------------------ the floor
$MinPython = $BuiltinMinPython
$MinSource = 'builtin'
if ($env:GINI_DOCTOR_MIN_PYTHON) {
    $MinPython = $env:GINI_DOCTOR_MIN_PYTHON; $MinSource = 'environment'
} elseif ($env:GINI_HEALTHCENTER) {
    $url = $env:GINI_HEALTHCENTER.TrimEnd('/') + '/doctor/policy.txt'
    $txt = $null
    try { $txt = [string](Invoke-RestMethod -Uri $url -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop) } catch { $txt = $null }
    $m = if ($txt) { [regex]::Match($txt, '(?m)^\s*min_python\s*=\s*(\d+\.\d+)') } else { $null }
    if ($m -and $m.Success) { $MinPython = $m.Groups[1].Value; $MinSource = 'live' }
    else { Add-Fact 'policy.note' 'Health Center policy unavailable; using the built-in floor' }
}
if ($MinPython -notmatch '^\d+\.\d+') {
    Add-Fact 'policy.note' "ignored malformed floor '$MinPython'"
    $MinPython = $BuiltinMinPython; $MinSource = 'builtin'
}
Add-Fact 'min_python' $MinPython
Add-Fact 'min_python.source' $MinSource

# ------------------------------------------------------------------ candidates
$Candidates = New-Object 'System.Collections.Generic.List[string]'
function Add-Candidate([string]$Path) {
    if ($Path -and -not $Candidates.Contains($Path)) { $Candidates.Add($Path) }
}

Add-Candidate $env:GINI_DOCTOR_PYTHON
# The venv pipx made for gBuilder: the interpreter that actually runs the app.
$pipxHomes = @($env:PIPX_HOME, (Join-Path $HOME 'pipx'), (Join-Path $HOME '.local/pipx'), (Join-Path $HOME '.local/share/pipx'))
if ($env:LOCALAPPDATA) { $pipxHomes += (Join-Path $env:LOCALAPPDATA 'pipx\pipx') }
foreach ($ph in $pipxHomes | Where-Object { $_ }) {
    foreach ($rel in @('venvs\gini-toolkit\Scripts\python.exe', 'venvs/gini-toolkit/bin/python')) {
        $p = Join-Path $ph $rel
        if (Test-Path -LiteralPath $p -PathType Leaf) { Add-Candidate (Resolve-Path -LiteralPath $p).Path }
    }
}
# The py launcher knows every registered install; ask it for the newest Python 3's real path.
if (Get-Command py -CommandType Application -ErrorAction SilentlyContinue) {
    $pyExe = (Get-Command py -CommandType Application | Select-Object -First 1).Source
    Add-Candidate (Invoke-Bounded $pyExe @('-3', '-c', 'import sys; print(sys.executable)'))
}
foreach ($name in @('python', 'python3')) {
    foreach ($c in @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue)) { Add-Candidate $c.Source }
}
if ($env:LOCALAPPDATA) {
    Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Programs\Python') -Filter 'Python3*' -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | ForEach-Object { Add-Candidate (Join-Path $_.FullName 'python.exe') }
}

$Chosen = $null; $ChosenVersion = $null; $n = 0
foreach ($c in $Candidates) {
    $n++
    Add-Fact "python.$n.path" $c
    if ($c -match '\\WindowsApps\\') {
        Add-Fact "python.$n.verdict" 'skipped: Microsoft Store alias (installs Python instead of running it)'
        continue
    }
    if (-not (Test-Path -LiteralPath $c -PathType Leaf)) { Add-Fact "python.$n.verdict" 'not found'; continue }
    $ver = Invoke-Bounded $c @('-c', 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
    if (-not $ver -or $ver -notmatch '^\d+\.\d+\.\d+$') { Add-Fact "python.$n.verdict" 'does not run'; continue }
    Add-Fact "python.$n.version" $ver
    if (Test-VersionAtLeast $ver $MinPython) {
        if (-not $Chosen) { $Chosen = $c; $ChosenVersion = $ver; Add-Fact "python.$n.verdict" 'chosen' }
        else { Add-Fact "python.$n.verdict" 'usable' }
    } else {
        Add-Fact "python.$n.verdict" "below floor $MinPython"
    }
}
Add-Fact 'python.candidates' ([string]$n)

# ------------------------------------------------------------------ Stage 1
$Stage1Path = $null
if ($PSScriptRoot -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot '..\stage1\__init__.py'))) {
    $Stage1Path = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
}
$Stage1 = $null
if ($Chosen) {
    if ($Stage1Path) { $Stage1 = 'checkout' }
    elseif ($null -ne (Invoke-Bounded $Chosen @('-c', 'import gini_doctor.stage1; print(1)'))) { $Stage1 = 'installed' }
}

$StageArgs = @($args)
if ($Chosen -and $Stage1) {
    Add-Fact 'python.chosen' "$Chosen ($ChosenVersion)"
    Add-Fact 'stage1' $Stage1
    $env:GINI_DOCTOR_STAGE0 = ($Facts | ForEach-Object { $_.Key + "`t" + $_.Value }) -join "`n"
    if ($Stage1Path) {
        $sep = [IO.Path]::PathSeparator
        $env:PYTHONPATH = if ($env:PYTHONPATH) { $Stage1Path + $sep + $env:PYTHONPATH } else { $Stage1Path }
    }
    & $Chosen -m gini_doctor.stage1 @StageArgs
    $code = $LASTEXITCODE
    # Under `irm … | iex` there is no script file, and `exit` would close the person's own
    # PowerShell window; set the status and stop instead.
    if ($PSCommandPath) { exit $code } else { $global:LASTEXITCODE = $code; return }
}

# ------------------------------------------------------------------ no Stage 1: report
if (-not $Chosen) {
    $Verdict = "no usable Python: GINI needs Python $MinPython or newer, and none of the $n interpreter(s) found qualifies"
} else {
    Add-Fact 'python.chosen' "$Chosen ($ChosenVersion)"
    $Verdict = "a usable Python was found, but the doctor's Stage 1 is not available to it (install gini-doctor, or run stage0.ps1 from a checkout)"
}
Add-Fact 'verdict' $Verdict

$HostName = [Environment]::MachineName
$Now = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
$factMap = [ordered]@{}
foreach ($f in ($Facts | Sort-Object Key)) { $factMap['stage0.' + $f.Key] = [ordered]@{ status = 'ok'; value = $f.Value } }
$report = [ordered]@{
    schema = 'gini-doctor/1'
    doctor = [ordered]@{ engine = 'stage0-ps1'; version = '1.0.0.dev0' }
    host = $HostName
    collected_at = $Now
    platform = $Platform
    policy = [ordered]@{ source = $MinSource; version = 'stage0' }
    groups = @('stage0')
    facts = $factMap
}
$json = $report | ConvertTo-Json -Depth 5

if ($StageArgs -contains '--stdout') {
    Write-Output $json
} else {
    $safeHost = $HostName -replace '[^A-Za-z0-9._-]', '_'
    $file = "gini-doctor-$safeHost-$($Now -replace '[:-]', '').json"
    # UTF-8 without a BOM, so every other tool (and Python's json module) reads it.
    [IO.File]::WriteAllText((Join-Path (Get-Location).Path $file), $json + "`n", (New-Object System.Text.UTF8Encoding $false))
    Write-Output ''
    Write-Output "gini-doctor (Stage 0)  host=$HostName  platform=$Platform"
    Write-Output ''
    foreach ($f in $Facts) { Write-Output ('    {0,-24} {1}' -f $f.Key, $f.Value) }
    Write-Output ''
    Write-Output "  $Verdict"
    Write-Output ''
    Write-Output "  report saved: $file"
    Write-Output ''
}
if ($PSCommandPath) { exit 3 } else { $global:LASTEXITCODE = 3; return }
