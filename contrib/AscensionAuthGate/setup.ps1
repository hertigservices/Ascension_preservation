[CmdletBinding()]
<#
  AuthGate client setup: detect the user's build, install AuthGate when it is
  verifiably compatible, and otherwise offer the build-agnostic shim as an
  explicitly-approved fallback. Fail-closed: an unrecognized build is never
  patched on a guess.

  Flow:
    1. Hash Ascension.exe + the genuine extension; run tools/find-offsets.py.
    2. COMPATIBLE  -> install AuthGate (with the verified/derived offset profile).
    3. UNSUPPORTED -> state the trade-offs, ask approval, then route to the shim.

  Read-only until it either installs AuthGate or (with your yes) sets up the shim.
#>
param(
    [Parameter(Mandatory=$true)][string]$ClientRoot,
    [ValidateSet('coa','ascension')][string]$Mode = 'coa',
    [string]$Python = 'python',
    # Pre-approve the shim fallback (for non-interactive runs). Interactive runs are
    # prompted instead; either way the shim is only set up with explicit approval.
    [switch]$ShimFallback
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$here = $PSScriptRoot

$ClientRoot = (Resolve-Path -LiteralPath $ClientRoot).Path.TrimEnd('\')
if ((Get-Item -LiteralPath $ClientRoot).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'Pass the REAL client directory, not a junction/symlink.'
}
$exe = Join-Path $ClientRoot 'Ascension.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "No Ascension.exe under $ClientRoot." }
# The genuine extension is Extensions_orig.dll once AuthGate is installed, else Extensions.dll.
$origDll = Join-Path $ClientRoot 'Extensions_orig.dll'
$ext = if (Test-Path -LiteralPath $origDll) { $origDll } else { Join-Path $ClientRoot 'Extensions.dll' }
if (-not (Test-Path -LiteralPath $ext)) { throw "No Extensions.dll under $ClientRoot." }

Write-Host "== AuthGate setup ==" -ForegroundColor Cyan
Write-Host "Client   : $ClientRoot"
Write-Host "Detecting build (read-only)..."

$profileOut = Join-Path ([IO.Path]::GetTempPath()) ("authgate-profile-{0}.json" -f ([guid]::NewGuid().ToString('N')))
$finder = Join-Path $here 'tools\find-offsets.py'
$profiles = Join-Path $here 'profiles'
# Run the finder tolerant of native stderr (PS 5.1 wraps native stderr as a terminating
# error under ErrorActionPreference=Stop even on exit 0), and rely on exit code + --out.
$prevEA = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
$stdout = (& $Python $finder --exe $exe --ext $ext --profiles $profiles --out $profileOut 2>&1 | Out-String)
$code = $LASTEXITCODE
$ErrorActionPreference = $prevEA

if ($code -eq 0 -and (Test-Path -LiteralPath $profileOut)) {
    $p = Get-Content -LiteralPath $profileOut -Raw | ConvertFrom-Json
    Write-Host "Build is AuthGate-COMPATIBLE." -ForegroundColor Green
    Write-Host ("  login_rva={0}  authobj_slot_rva={1}  off_login_k={2}  ({3})" -f `
        $p.login_rva, $p.authobj_slot_rva, $p.off_login_k, $p.source)
    Write-Host "Installing AuthGate..."
    & (Join-Path $here 'install-client.ps1') -ClientRoot $ClientRoot -Profile $profileOut
    Remove-Item -LiteralPath $profileOut -ErrorAction SilentlyContinue
    Write-Host ""
    Write-Host "AuthGate installed. Start the realm's authserver (3724), bridge (8088) and" -ForegroundColor Green
    Write-Host "worldserver (8086), then launch with start-client.ps1 -Mode $Mode." -ForegroundColor Green
    exit 0
}

# ---- Not compatible: state the trade-offs and offer the shim fallback ----
Remove-Item -LiteralPath $profileOut -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "This client build is NOT recognized as AuthGate-compatible." -ForegroundColor Yellow
if ($stdout) { Write-Host ($stdout -join "`n") -ForegroundColor DarkGray }
Write-Host ""
Write-Host "Why AuthGate can't auto-configure it:" -ForegroundColor Yellow
Write-Host "  AuthGate reads the auth object inside the extension at fixed offsets found by"
Write-Host "  static analysis of one build. Your extension's SHA-256 isn't recognized, and the"
Write-Host "  extension is VMProtect-obfuscated, so those offsets can't be safely re-derived."
Write-Host "  Rather than patch a guess (which can crash or trip anti-tamper), setup stops here."
Write-Host ""
Write-Host "Fallback: the SHIM (build-agnostic; works on any Ascension build)." -ForegroundColor Cyan
Write-Host "  It leaves your client's Extensions.dll UNCHANGED and redirects login with a loose"
Write-Host "  Interface\GlueXML\AccountLogin.lua. Its limitations, so you can decide:"
Write-Host "    - No per-account password check: the shim is PERMISSIVE (any password logs in)."
Write-Host "      Fine for a private single-user realm; it is NOT a real auth boundary."
Write-Host "    - It runs as a SEPARATE process you must start (shim3799.py), alongside the bridge."
Write-Host "    - It reads the session key via ReadProcessMemory, so the shim must run at the SAME"
Write-Host "      integrity as the client (launch both unelevated, or run the shim elevated to match)."
Write-Host "  See docs/HOW-THE-REDIRECT-WORKS.md (legacy shim section) for the full setup."
Write-Host ""
$approved = $false
if ($ShimFallback) {
    $approved = $true
    Write-Host "Shim fallback pre-approved (-ShimFallback)." -ForegroundColor Green
} else {
    try {
        $ans = Read-Host "Set up the shim fallback for this client? (y/N)"
        $approved = ($ans -match '^(y|yes)$')
    } catch {
        Write-Host "Non-interactive session: re-run with -ShimFallback to approve the shim, or run interactively." -ForegroundColor Yellow
        exit 3
    }
}
if (-not $approved) {
    Write-Host "No changes made. Client left untouched." -ForegroundColor Yellow
    exit 3
}

Write-Host ""
Write-Host "Approved. The shim is server-driven; complete it via the documented flow:" -ForegroundColor Green
Write-Host "  1. Keep your genuine Extensions.dll in place (do NOT install AuthGate)."
Write-Host "  2. Place the loose redirect: copy contrib/AscensionRedirect/force-realm-glue.lua"
Write-Host "     content into $ClientRoot\Interface\GlueXML\AccountLogin.lua per docs/HOW-THE-REDIRECT-WORKS.md."
Write-Host "  3. Start server/shim3799.py (auth on 3724->answered locally) and ascension_bridge.py (8088)."
Write-Host "  4. Point realmList at 127.0.0.1 and launch the client at the shim's integrity level."
$launch = Join-Path (Split-Path (Split-Path $here -Parent) -Parent) 'server\launch-client.ps1'
if (Test-Path -LiteralPath $launch) {
    Write-Host ""
    Write-Host "A shim launcher is present: $launch" -ForegroundColor Green
    Write-Host "Run it after steps 2-3 to launch the client on the shim path."
}
exit 0
