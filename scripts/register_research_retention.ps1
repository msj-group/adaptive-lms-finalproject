param([string]$DailyAt = '03:00')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = (Resolve-Path -LiteralPath (Join-Path $repoRoot '.venv\Scripts\python.exe')).Path
$jobPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'run_research_retention.py')).Path
$taskName = 'AdaptiveEnglishLMS-ResearchRetention'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw 'The retention task already exists. Review it before updating.'
}
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument ('-B "' + $jobPath + '"') -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Expire research sessions, events, prompts and export archives using RESEARCH_RETENTION_DAYS.' | Select-Object TaskName, State
