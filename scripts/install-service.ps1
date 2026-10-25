# Installs/updates the panel service as a Windows Scheduled Task.
#
# The task runs at logon in the user session (required to open Explorer and
# control media), windowless, and Windows Task Scheduler restarts it if it crashes.
# It lives OUTSIDE the process tree of any terminal or agent session -- which is
# what was killing the companion after a few minutes, leaving display shortcuts dead.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-service.ps1
#   powershell ... -NoStart        # only register, do not restart now
#   powershell ... -Remove         # uninstall the scheduled task
#
# Idempotent: run again after modifying code or configuration.

param(
    [string]$TaskName = "PanelCompanion",
    [string]$Config = "config.json",
    [Alias("NaoIniciar")]
    [switch]$NoStart,
    [Alias("Remover")]
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$base = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$service = Join-Path $base "service.py"

# Kills ONLY the supervisor of this repository (matching the full path in
# the command line) and its child processes. A `Where-Object` purely by file
# name would kill `service.py` of any other Python program on the machine.
function Get-PanelPids {
    $all = Get-CimInstance Win32_Process
    $target = New-Object 'System.Collections.Generic.List[int]'
    $queue = New-Object 'System.Collections.Generic.Queue[int]'
    # root = supervisor: python(w) whose command line references the
    # ABSOLUTE path to service.py in this repository. Filtering by text alone
    # could match a terminal or agent that merely mentions the path.
    foreach ($root in @($all | Where-Object {
                $_.Name -match '^pythonw?\.exe$' -and
                $_.CommandLine -and
                $_.CommandLine.Contains($service) })) {
        $queue.Enqueue([int]$root.ProcessId)
    }
    # the companion writes its own PID to companion.pid: an orphan child of a
    # dead supervisor cannot be found via the command line
    $pidFile = Join-Path $base "companion.pid"
    if (Test-Path $pidFile) {
        $text = (Get-Content $pidFile -Raw -ErrorAction SilentlyContinue)
        if ($text -and $text.Trim() -match '^[0-9]+$') {
            $companion = $all | Where-Object { $_.ProcessId -eq [int]$text.Trim() -and $_.Name -match '^pythonw?\.exe$' }
            if ($companion) { $queue.Enqueue([int]$text.Trim()) }
        }
    }
    while ($queue.Count -gt 0) {
        $current = $queue.Dequeue()
        if ($target.Contains($current)) { continue }
        $target.Add($current)
        foreach ($child in @($all | Where-Object { $_.ParentProcessId -eq $current })) {
            $queue.Enqueue([int]$child.ProcessId)
        }
    }
    return $target
}

function Stop-PanelProcesses {
    foreach ($procId in @(Get-PanelPids)) {
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "stopping pid $procId ($($proc.ProcessName))"
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
}

if (-not (Test-Path (Join-Path $base "mcp_display_companion"))) {
    throw "run this script from within the repository (could not find $base\mcp_display_companion)"
}

if ($Remove) {
    Stop-PanelProcesses
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Stop-PanelProcesses
    Write-Host "task '$TaskName' removed."
    exit 0
}

$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) {
    foreach ($cand in @("C:\Python313\pythonw.exe", "$env:LOCALAPPDATA\Programs\Python\Python313\pythonw.exe")) {
        if (Test-Path $cand) { $pyw = $cand; break }
    }
}
if (-not $pyw) { throw "pythonw.exe not found (install Python 3.11+ with 'Add to PATH')" }

Write-Host "python : $pyw"
Write-Host "service: $service"

$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$service`" --config `"$Config`"" -WorkingDirectory $base

# AtLogOn (not AtStartup): opening Explorer and sending media keys requires the user session.
$triggers = @(New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME")

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -DisallowHardTerminate `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal `
    -Description "Executes MCP Display panel actions on the computer (config: $Config)." `
    -Force | Out-Null

New-Item -ItemType Directory -Force -Path (Join-Path $base "logs") | Out-Null
Write-Host "task '$TaskName' registered."

# An old shortcut in the Startup folder would launch a SECOND companion.
$lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "PanelCompanion.lnk"
if (Test-Path $lnk) {
    Remove-Item $lnk -Force
    Write-Host "old shortcut removed from Startup: $lnk"
}

if (-not $NoStart) {
    # Restart to apply new code/config. Only kills processes belonging to THIS
    # service (matched by command line), never any other pythonw.
    # Order matters: stopping the scheduled task first leaves child orphaned and alive.
    Stop-PanelProcesses
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Stop-PanelProcesses
    Start-Sleep -Seconds 1
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 5
    Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State | Format-Table -AutoSize
    $alive = @()
    foreach ($procId in @(Get-PanelPids)) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $procId" -ErrorAction SilentlyContinue
        if ($proc) {
            $alive += $proc
            Write-Host "running: pid $($proc.ProcessId) -> $($proc.CommandLine)"
        }
    }
    $companions = @($alive | Where-Object { $_.CommandLine -match '-m mcp_display_companion' })
    if ($companions.Count -gt 1) {
        Write-Warning "more than one active companion ($($companions.Count)): they conflict over the display"
    }
}
