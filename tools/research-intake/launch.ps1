param([string]$Root = "$env:LOCALAPPDATA\AscensionPreservation\research-intake")
$ErrorActionPreference = 'Stop'
$ResearchScript = Join-Path $PSScriptRoot 'desktop.py'
$ResearchPython = (Get-Command python.exe -ErrorAction Stop).Source
$ResearchPythonWindowless = Join-Path (Split-Path $ResearchPython) 'pythonw.exe'
if (Test-Path -LiteralPath $ResearchPythonWindowless) {
    Start-Process -FilePath $ResearchPythonWindowless -ArgumentList @('-B', ('"' + $ResearchScript + '"'), '--root', ('"' + $Root + '"')) -WindowStyle Hidden
} else {
    Start-Process -FilePath $ResearchPython -ArgumentList @('-B', ('"' + $ResearchScript + '"'), '--root', ('"' + $Root + '"')) -WindowStyle Hidden
}
