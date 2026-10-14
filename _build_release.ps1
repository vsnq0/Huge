# Build standalone Huge ONEDIR bundle + Huge Setup.exe installer.
# Packaging only - does not change application behavior.
#
# Critical: ship the COMPLETE onedir folder (Huge.exe + _internal\...), never
# a lone EXE. Clean PCs without Python installed need _internal\python313.dll.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$versionFile = Join-Path $root 'VERSION.txt'
if (-not (Test-Path $versionFile)) { throw 'VERSION.txt missing' }
$version = (Get-Content $versionFile -Raw).Trim().TrimStart('v', 'V')
if (-not $version) { throw 'VERSION.txt is empty' }

$release = Join-Path $root 'release'
$archiveRoot = Join-Path $root 'release-archive'
$buildDir = Join-Path $root 'build'
$distDir = Join-Path $root 'dist'
$ico = Join-Path $root 'huge.ico'

function Assert-OnedirBundle {
  param([Parameter(Mandatory=$true)][string]$BundleDir)
  $exe = Join-Path $BundleDir 'Huge.exe'
  $internal = Join-Path $BundleDir '_internal'
  $pyDll = Join-Path $internal 'python313.dll'
  if (-not (Test-Path $exe)) { throw "Missing Huge.exe in $BundleDir" }
  if (-not (Test-Path $internal)) { throw "Missing _internal folder in $BundleDir - onedir incomplete" }
  if (-not (Test-Path $pyDll)) { throw "Missing _internal\python313.dll in $BundleDir - clean PCs will fail to launch" }
  $dllCount = @(Get-ChildItem $internal -Filter '*.dll' -File -ErrorAction SilentlyContinue).Count
  if ($dllCount -lt 3) { throw "_internal has too few DLLs ($dllCount) in $BundleDir" }
  $fileCount = @(Get-ChildItem $internal -Recurse -File -ErrorAction SilentlyContinue).Count
  if ($fileCount -lt 20) { throw "_internal looks incomplete ($fileCount files) in $BundleDir" }
  Write-Host "ONEDIR_OK exe + _internal ($fileCount files, python313.dll present)"
}

function New-ZipFromFolder {
  param(
    [Parameter(Mandatory=$true)][string]$SourceDir,
    [Parameter(Mandatory=$true)][string]$ZipPath,
    [switch]$IncludeRootFolderName
  )
  if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
  Add-Type -AssemblyName System.IO.Compression
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $sourceFull = (Resolve-Path $SourceDir).Path
  if ($IncludeRootFolderName) {
    # Zip so extract yields FolderName\... (portable distribution).
    $parent = Split-Path $sourceFull -Parent
    $name = Split-Path $sourceFull -Leaf
    $tmpParent = Join-Path $env:TEMP ('HugeZipStage-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmpParent | Out-Null
    try {
      Copy-Item $sourceFull (Join-Path $tmpParent $name) -Recurse -Force
      [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $tmpParent,
        $ZipPath,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false
      )
    } finally {
      Remove-Item $tmpParent -Recurse -Force -ErrorAction SilentlyContinue
    }
  } else {
    # Zip contents at archive root (installer payload: Huge.exe + _internal at top).
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
      $sourceFull,
      $ZipPath,
      [System.IO.Compression.CompressionLevel]::Optimal,
      $false
    )
  }
}

Write-Host "== Huge packaging v$version =="

# Preserve previous release artifacts (do not overwrite older GitHub release files).
if (Test-Path $release) {
  $prevSetup = Join-Path $release 'Huge Setup.exe'
  $prevPortable = Join-Path $release 'Huge'
  if ((Test-Path $prevSetup) -or (Test-Path $prevPortable)) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $prevLabel = 'previous'
    $prevWhere = Join-Path $release 'WHERE_ARE_THE_FILES.txt'
    if (Test-Path $prevWhere) {
      $m = Select-String -Path $prevWhere -Pattern 'v?\d+\.\d+\.\d+' -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($m) { $prevLabel = ($m.Matches[0].Value.TrimStart('v')) }
    }
    # Prefer VERSION from archived build note; fall back to stamp.
    $dest = Join-Path $archiveRoot ("v{0}-{1}" -f $prevLabel, $stamp)
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    Write-Host "== Archiving previous release -> $dest =="
    Copy-Item $release\* $dest -Recurse -Force
  }
}

Write-Host '== Rebuild UI =='
Push-Location (Join-Path $root 'ui')
npm run build
if ($LASTEXITCODE -ne 0) { throw 'UI build failed' }
Pop-Location

Write-Host '== PyInstaller Huge (ONEDIR) =='
if (Test-Path $distDir) { Remove-Item $distDir -Recurse -Force }
if (Test-Path $buildDir) { Remove-Item $buildDir -Recurse -Force }
py -3 -m PyInstaller --noconfirm --clean (Join-Path $root 'Huge.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

$packaged = Join-Path $distDir 'Huge'
Assert-OnedirBundle $packaged

Copy-Item $ico (Join-Path $packaged 'huge.ico') -Force
if (Test-Path (Join-Path $root 'huge-logo.png')) {
  Copy-Item (Join-Path $root 'huge-logo.png') (Join-Path $packaged 'huge-logo.png') -Force
}
Assert-OnedirBundle $packaged

Write-Host '== Stage release folder =='
if (Test-Path $release) { Remove-Item $release -Recurse -Force }
New-Item -ItemType Directory -Path $release -Force | Out-Null
$portable = Join-Path $release 'Huge'
Copy-Item $packaged $portable -Recurse -Force
Assert-OnedirBundle $portable

# Versioned portable zip for GitHub (complete folder - never upload bare Huge.exe).
$portableZip = Join-Path $release ("Huge-v{0}-windows-portable.zip" -f $version)
Write-Host "== Create $portableZip =="
New-ZipFromFolder -SourceDir $portable -ZipPath $portableZip -IncludeRootFolderName
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zipCheck = [System.IO.Compression.ZipFile]::OpenRead($portableZip)
try {
  $names = $zipCheck.Entries | ForEach-Object { $_.FullName.Replace('\', '/') }
  if (-not ($names | Where-Object { $_ -match '(^|/)Huge/Huge\.exe$' -or $_ -eq 'Huge/Huge.exe' })) {
    # CreateFromDirectory of parent with Huge child => entries like Huge/Huge.exe
    if (-not ($names | Where-Object { $_ -like 'Huge/Huge.exe' -or $_ -like '*/Huge/Huge.exe' })) {
      $sample = ($names | Select-Object -First 8) -join ', '
      throw "Portable zip missing Huge/Huge.exe (sample: $sample)"
    }
  }
  if (-not ($names | Where-Object { $_ -like '*_internal/python313.dll' })) {
    throw 'Portable zip missing _internal/python313.dll'
  }
} finally {
  $zipCheck.Dispose()
}
Write-Host 'PORTABLE_ZIP_OK'

Write-Host '== Compile Huge Setup.exe (embeds complete onedir) =='
$setupCs = Join-Path $env:TEMP 'HugeSetup.cs'
$setupExe = Join-Path $release 'Huge Setup.exe'
$setupVersioned = Join-Path $release ("Huge-v{0}-Setup.exe" -f $version)
$payloadZip = Join-Path $env:TEMP ('HugePayload-' + [guid]::NewGuid().ToString('N') + '.zip')
New-ZipFromFolder -SourceDir $portable -ZipPath $payloadZip
# Verify payload before embedding
$payloadCheck = [System.IO.Compression.ZipFile]::OpenRead($payloadZip)
try {
  $pnames = $payloadCheck.Entries | ForEach-Object { $_.FullName.Replace('\', '/') }
  if (-not ($pnames | Where-Object { $_ -eq 'Huge.exe' -or $_ -like '*/Huge.exe' })) {
    throw 'Installer payload missing Huge.exe at zip root'
  }
  if (-not ($pnames | Where-Object { $_ -eq '_internal/python313.dll' -or $_ -like '*/_internal/python313.dll' })) {
    throw 'Installer payload missing _internal/python313.dll - refusing to build broken Setup'
  }
  Write-Host ("PAYLOAD_OK entries={0}" -f $payloadCheck.Entries.Count)
} finally {
  $payloadCheck.Dispose()
}

$payloadBytes = (Get-Item $payloadZip).Length
$setupTemplate = Join-Path $root 'installer\HugeSetup.cs.in'
if (-not (Test-Path $setupTemplate)) { throw "Missing installer template: $setupTemplate" }
$setupSource = Get-Content -Path $setupTemplate -Raw -Encoding UTF8
$setupSource = $setupSource.Replace('__HUGE_VERSION__', $version).Replace('__PAYLOAD_BYTES__', [string]$payloadBytes)
Set-Content -Path $setupCs -Value $setupSource -Encoding ASCII

$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $compiler)) {
  $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path $compiler)) { throw 'csc.exe not found' }

$fw = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
& $compiler /nologo /target:winexe /win32icon:"$ico" /out:"$setupExe" `
  /resource:"$payloadZip",PAYLOAD.ZIP `
  /r:System.Windows.Forms.dll /r:System.dll /r:System.Drawing.dll `
  /r:"$fw\System.IO.Compression.dll" /r:"$fw\System.IO.Compression.FileSystem.dll" `
  $setupCs
if ($LASTEXITCODE -ne 0) { throw 'Setup compile failed' }

Copy-Item $setupExe $setupVersioned -Force
Remove-Item $setupCs,$payloadZip -Force -ErrorAction SilentlyContinue

# Post-build: extract Setup payload into a clean folder and verify (no reliance on system Python).
$verifyRoot = Join-Path $env:TEMP ('HugeSetupVerify-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $verifyRoot | Out-Null
$verifyCs = Join-Path $env:TEMP 'HugeVerifySetup.cs'
$verifyExe = Join-Path $env:TEMP 'HugeVerifySetup.exe'
@'
using System;
using System.IO;
using System.Reflection;
using System.IO.Compression;
class V {
  static int Main() {
    string setup = Environment.GetEnvironmentVariable("SETUP");
    string outDir = Environment.GetEnvironmentVariable("OUTDIR");
    var asm = Assembly.LoadFile(Path.GetFullPath(setup));
    using (Stream src = asm.GetManifestResourceStream("PAYLOAD.ZIP")) {
      if (src == null) { Console.WriteLine("NO_RESOURCE"); return 2; }
      string zip = Path.Combine(outDir, "payload.zip");
      using (FileStream dst = File.Create(zip)) src.CopyTo(dst);
      ZipFile.ExtractToDirectory(zip, Path.Combine(outDir, "install"));
    }
    string dll = Path.Combine(outDir, "install", "_internal", "python313.dll");
    string exe = Path.Combine(outDir, "install", "Huge.exe");
    if (!File.Exists(exe) || !File.Exists(dll)) {
      Console.WriteLine("VERIFY_FAIL missing runtime");
      return 3;
    }
    Console.WriteLine("VERIFY_OK");
    return 0;
  }
}
'@ | Set-Content $verifyCs -Encoding ASCII
& $compiler /nologo /out:$verifyExe /r:System.dll /r:"$fw\System.IO.Compression.dll" /r:"$fw\System.IO.Compression.FileSystem.dll" $verifyCs
$env:SETUP = $setupExe
$env:OUTDIR = $verifyRoot
& $verifyExe
if ($LASTEXITCODE -ne 0) { throw 'Setup payload verification failed' }
Remove-Item $verifyCs,$verifyExe -Force -ErrorAction SilentlyContinue
Remove-Item $verifyRoot -Recurse -Force -ErrorAction SilentlyContinue

$setSummary = @"
Huge release build v$version
============================
Portable app folder: $portable
  MUST keep Huge.exe together with _internal\ (includes python313.dll)
Portable zip:        $portableZip
Installer:           $setupExe
Versioned installer: $setupVersioned

Installed location after Setup:
  User-chosen folder (default %LOCALAPPDATA%\Programs\Huge\)
  Recorded in HKCU\Software\vsnq\Huge\InstallLocation
    Huge.exe
    _internal\python313.dll
    _internal\...

GitHub release assets for v$version (upload these - do NOT upload bare Huge.exe):
  1) Huge-v$version-Setup.exe
  2) Huge-v$version-windows-portable.zip

Do not overwrite older GitHub releases (e.g. v2.0.0). Publish a new tag/release.
"@
Set-Content -Path (Join-Path $release 'WHERE_ARE_THE_FILES.txt') -Value $setSummary -Encoding UTF8

# SHA-256 sidecars for GitHub release verification (upload alongside Setup/ZIP)
function Write-Sha256File([string]$Path) {
  if (-not (Test-Path $Path)) { return }
  $hash = (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLowerInvariant()
  $name = Split-Path $Path -Leaf
  $out = "$Path.sha256"
  Set-Content -Path $out -Value "$hash  $name" -Encoding ASCII -NoNewline
  Write-Host "SHA256 $name = $hash"
}
Write-Sha256File $setupVersioned
Write-Sha256File $portableZip

Write-Host $setSummary
Write-Host 'BUILD_OK'
