[CmdletBinding()]
param(
    [ValidateSet('Install', 'Start', 'Stop', 'Uninstall', 'Status')]
    [string]$Action = 'Status',
    [string]$TaskName = 'ZTE-Telemetry-WindowsValidation',
    [string]$PythonPath,
    [string]$ConfigPath,
    [string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $repoRoot 'lab\mock-telemetry'
$runner = Join-Path $repoRoot 'tools\windows_boot_agent.py'

if (-not $ConfigPath) {
    $ConfigPath = Join-Path $runtimeRoot 'config.windows-validation.json'
}
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $runtimeRoot 'out\boot-session'
}

function Resolve-PythonExecutable {
    if ($PythonPath) {
        return (Resolve-Path -LiteralPath $PythonPath).Path
    }
    if ($env:VIRTUAL_ENV) {
        $candidate = Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    $command = Get-Command python -CommandType Application -ErrorAction Stop
    return $command.Source
}

switch ($Action) {
    'Install' {
        $python = Resolve-PythonExecutable
        $arguments = ('"{0}" --config "{1}" --output-root "{2}"' -f $runner, $ConfigPath, $OutputRoot)
        $taskAction = New-ScheduledTaskAction `
            -Execute $python `
            -Argument $arguments `
            -WorkingDirectory $runtimeRoot
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $trigger.Delay = 'PT30S'
        $principal = New-ScheduledTaskPrincipal `
            -UserId 'SYSTEM' `
            -LogonType ServiceAccount `
            -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet `
            -StartWhenAvailable `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $taskAction `
            -Trigger $trigger `
            -Principal $principal `
            -Settings $settings `
            -Description 'Windows validation telemetry boot-session capture' `
            -Force | Out-Null
        Get-ScheduledTask -TaskName $TaskName
    }
    'Start' {
        Start-ScheduledTask -TaskName $TaskName
        Get-ScheduledTask -TaskName $TaskName
    }
    'Stop' {
        Stop-ScheduledTask -TaskName $TaskName
        Get-ScheduledTask -TaskName $TaskName
    }
    'Uninstall' {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
    }
    'Status' {
        Get-ScheduledTask -TaskName $TaskName
        Get-ScheduledTaskInfo -TaskName $TaskName
    }
}
