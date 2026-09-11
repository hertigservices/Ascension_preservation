param([Parameter(Mandatory=$true)][string]$ConfigPath)
$ErrorActionPreference = 'Stop'
$configFile = (Resolve-Path -LiteralPath $ConfigPath).Path
$configData = Get-Content -Raw -LiteralPath $configFile | ConvertFrom-Json
if ($configData.url -notmatch '^https://[^/]+$' -or $configData.url -match 'REPLACE') { throw 'Set the deployed HTTPS origin in the collector configuration first.' }
if (-not $configData.token_file -or -not (Test-Path -LiteralPath $configData.token_file)) { throw 'Configure a private collector token_file before installing the task.' }
$collectorScript = Join-Path $PSScriptRoot 'collector.py'
$pythonPath = $configData.python
if (-not $pythonPath) { $pythonPath = (Get-Command python).Source }
$windowlessPython = Join-Path (Split-Path -Parent $pythonPath) 'pythonw.exe'
if (Test-Path -LiteralPath $windowlessPython) { $pythonPath = $windowlessPython }
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument ('-B "{0}" --config "{1}"' -f $collectorScript,$configFile) -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'Ascension Private Upload Collector' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Downloads private community game-data contributions, validates them, and invokes the audited consolidator publisher.' -Force | Out-Null
Start-ScheduledTask -TaskName 'Ascension Private Upload Collector'
Write-Output 'Collector installed for your next sign-in and started now.'
