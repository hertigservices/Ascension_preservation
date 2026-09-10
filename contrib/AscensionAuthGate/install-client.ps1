[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ClientRoot,
    # Optional build profile (from tools/find-offsets.py) for an exe-drifted build whose
    # extension is unchanged. Only accepted when its ext_sha256 equals the trusted genuine
    # extension; the derived login_rva is written to <ClientRoot>\authgate.profile.
    [string]$Profile)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ClientRoot = (Resolve-Path -LiteralPath $ClientRoot).Path.TrimEnd('\')
if ((Get-Item -LiteralPath $ClientRoot).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Pass the real client directory, not a junction.' }
$exe = Join-Path $ClientRoot 'Ascension.exe'
$target = Join-Path $ClientRoot 'Extensions.dll'
$original = Join-Path $ClientRoot 'Extensions_orig.dll'
$expectedExe = '5B26E33B2129737AF3A0C3164459F4C9B109398DAB921F76C6740A8746FBB929'
$expectedOriginal = '0F8D847B3ADC44A963606F0CD4F7938AD6FCD6F4D87FEAC3131C7153BDF3BB11'
$prof = $null
if ($Profile) {
    $prof = Get-Content -LiteralPath $Profile -Raw | ConvertFrom-Json
    if (([string]$prof.ext_sha256).ToUpper() -ne $expectedOriginal) {
        throw 'Profile is for a different extension build. The auth-object offsets are only trusted for the known extension; use the build-agnostic shim fallback instead.'
    }
    $expectedExe = ([string]$prof.exe_sha256).ToUpper()   # accept the drifted exe named by the verified profile
}
if (@(Get-Process -Name Ascension -ErrorAction SilentlyContinue).Count) { throw 'Close Ascension clients before installation.' }
if ((Get-FileHash -LiteralPath $exe).Hash -ne $expectedExe) { throw 'Unsupported Ascension executable.' }
$verified = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'build\binary-verification.json') -Raw | ConvertFrom-Json
$build = Join-Path $PSScriptRoot 'build\Extensions.dll'
if ((Get-FileHash -LiteralPath $build).Hash -ne $verified.sha256) { throw 'Build hash differs from verification.' }
foreach ($file in @($exe,$target)) {
    if ((Get-Item -LiteralPath $file).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Executable/DLL must be regular files.' }
}
$currentHash = (Get-FileHash -LiteralPath $target).Hash
if ($currentHash -eq $verified.sha256) {
    if ((Get-FileHash -LiteralPath $original).Hash -ne $expectedOriginal) { throw 'Genuine companion hash mismatch.' }
    Write-Output 'Verified AuthGate already installed.'; exit 0
}
# First installation only; never overwrite another third-party proxy or a genuine backup.
if ($currentHash -ne $expectedOriginal) { throw 'Unexpected existing extension; review migration rather than overwriting it.' }
if (Test-Path -LiteralPath $original) { throw 'Companion DLL already exists; review before installation.' }
# Rename the original directory entry, then create a new proxy file. Do not overwrite
# an existing inode/file record that could be hardlinked to another client.
Move-Item -LiteralPath $target -Destination $original
try {
    Copy-Item -LiteralPath $build -Destination $target
    if ((Get-FileHash -LiteralPath $target).Hash -ne $verified.sha256 -or (Get-FileHash -LiteralPath $original).Hash -ne $expectedOriginal) { throw 'Installed file verification failed.' }
} catch {
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
    Move-Item -LiteralPath $original -Destination $target
    throw
}
$profileWritten = $false
if ($prof) {
    # Write the runtime offset override the DLL reads at load. Omit for the exact archive
    # build (offsets equal the compiled defaults, so no file is needed).
    "login_rva=$($prof.login_rva)`nauthobj_slot_rva=$($prof.authobj_slot_rva)`noff_login_k=$($prof.off_login_k)`n" |
        Set-Content -LiteralPath (Join-Path $ClientRoot 'authgate.profile') -Encoding ASCII
    $profileWritten = $true
}
[ordered]@{installedAt=(Get-Date -Format o);client=$ClientRoot;originalSha256=$expectedOriginal;installedSha256=$verified.sha256;profileWritten=$profileWritten;dataTouched=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'last-original-install.json') -Encoding UTF8
Write-Output 'AuthGate installed; genuine Extensions_orig.dll preserved; Data untouched.'
