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

@'
using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

internal static class HugeSetup {
  const string AppId = "vsnq.Huge";
  const string AppVersion = "__HUGE_VERSION__";

  [STAThread]
  static void Main(string[] args) {
    try {
      Application.EnableVisualStyles();
      Application.SetCompatibleTextRenderingDefault(false);

      bool updateMode = false;
      string relaunchPath = "";
      if (args != null) {
        foreach (string raw in args) {
          string a = (raw ?? "").Trim();
          if (a.Equals("/update", StringComparison.OrdinalIgnoreCase) ||
              a.Equals("--update", StringComparison.OrdinalIgnoreCase) ||
              a.Equals("/S", StringComparison.OrdinalIgnoreCase) ||
              a.Equals("/silent", StringComparison.OrdinalIgnoreCase)) {
            updateMode = true;
          } else if (a.StartsWith("/relaunch=", StringComparison.OrdinalIgnoreCase)) {
            relaunchPath = a.Substring("/relaunch=".Length).Trim().Trim('"');
          } else if (a.StartsWith("--relaunch=", StringComparison.OrdinalIgnoreCase)) {
            relaunchPath = a.Substring("--relaunch=".Length).Trim().Trim('"');
          }
        }
      }

      string installDir = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "Programs", "Huge");
      string exePath = Path.Combine(installDir, "Huge.exe");
      string internalDir = Path.Combine(installDir, "_internal");
      string pythonDll = Path.Combine(internalDir, "python313.dll");

      // Wait briefly so the previous Huge process can exit during auto-update.
      if (updateMode) {
        for (int i = 0; i < 30; i++) {
          try {
            Process[] procs = Process.GetProcessesByName("Huge");
            if (procs == null || procs.Length == 0) break;
            foreach (Process p in procs) { try { p.Dispose(); } catch { } }
          } catch { }
          System.Threading.Thread.Sleep(200);
        }
      }

      Directory.CreateDirectory(installDir);
      foreach (string path in Directory.GetFileSystemEntries(installDir)) {
        try {
          if (Directory.Exists(path)) Directory.Delete(path, true);
          else File.Delete(path);
        } catch { }
      }

      string tempZip = Path.Combine(Path.GetTempPath(), "HugeInstall-" + Guid.NewGuid().ToString("N") + ".zip");
      using (Stream src = Assembly.GetExecutingAssembly().GetManifestResourceStream("PAYLOAD.ZIP")) {
        if (src == null) throw new Exception("Embedded payload missing.");
        using (FileStream dst = File.Create(tempZip)) src.CopyTo(dst);
      }
      try {
        ZipFile.ExtractToDirectory(tempZip, installDir);
      } finally {
        try { File.Delete(tempZip); } catch { }
      }

      if (!File.Exists(exePath)) {
        MessageBox.Show(
          "Installation failed: Huge.exe was not found after extract.\n\n" +
          "The installer payload is incomplete.",
          "Huge Setup");
        return;
      }
      if (!Directory.Exists(internalDir) || !File.Exists(pythonDll)) {
        MessageBox.Show(
          "Installation failed: the PyInstaller _internal runtime is missing.\n\n" +
          "Expected:\n" + pythonDll + "\n\n" +
          "Huge cannot run without the complete application folder " +
          "(Huge.exe + _internal, including python313.dll).\n" +
          "Do not distribute Huge.exe alone.",
          "Huge Setup");
        return;
      }

      string stableIconDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Huge");
      Directory.CreateDirectory(stableIconDir);
      string stableIcon = Path.Combine(stableIconDir, "huge.ico");
      string srcIcon = Path.Combine(installDir, "huge.ico");
      if (File.Exists(srcIcon)) {
        try { File.Copy(srcIcon, stableIcon, true); } catch { }
      }
      string iconForShortcut = File.Exists(stableIcon) ? stableIcon : (File.Exists(srcIcon) ? srcIcon : exePath);

      string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
      string startMenu = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs");
      Directory.CreateDirectory(startMenu);
      CreateShortcut(Path.Combine(desktop, "Huge.lnk"), exePath, installDir, iconForShortcut, AppId);
      CreateShortcut(Path.Combine(startMenu, "Huge.lnk"), exePath, installDir, iconForShortcut, AppId);

      bool launch = updateMode;
      if (!updateMode) {
        var result = MessageBox.Show(
          "Huge v" + AppVersion + " was installed successfully.\n\n" +
          "Location:\n" + installDir + "\n\n" +
          "This install includes the complete runtime folder (_internal).\n" +
          "Desktop and Start Menu shortcuts named \"Huge\" were created.\n\n" +
          "Launch Huge now?",
          "Huge Setup",
          MessageBoxButtons.YesNo,
          MessageBoxIcon.Information);
        launch = (result == DialogResult.Yes);
      }
      if (launch) {
        string runExe = File.Exists(exePath) ? exePath : relaunchPath;
        Process.Start(new ProcessStartInfo {
          FileName = runExe,
          WorkingDirectory = installDir,
          UseShellExecute = true
        });
      }
    } catch (Exception ex) {
      MessageBox.Show(ex.Message, "Huge Setup");
    }
  }

  static void CreateShortcut(string lnkPath, string target, string workDir, string iconPath, string appUserModelId) {
    if (File.Exists(lnkPath)) File.Delete(lnkPath);
    var link = (IShellLinkW)Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("00021401-0000-0000-C000-000000000046")));
    link.SetPath(target);
    link.SetWorkingDirectory(workDir);
    link.SetIconLocation(iconPath, 0);
    link.SetDescription("Huge");
    var store = (IPropertyStore)link;
    var key = new PROPERTYKEY { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
    var pv = new PropVariant();
    pv.vt = 31;
    pv.p = Marshal.StringToCoTaskMemUni(appUserModelId);
    store.SetValue(ref key, ref pv);
    store.Commit();
    PropVariantClear(ref pv);
    var file = (IPersistFile)link;
    file.Save(lnkPath, true);
  }

  [ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IShellLinkW {
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cchMaxPath, IntPtr pfd, uint fFlags);
    void GetIDList(out IntPtr ppidl);
    void SetIDList(IntPtr pidl);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cchMaxName);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cchMaxPath);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cchMaxPath);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
    void GetHotkey(out short pwHotkey);
    void SetHotkey(short wHotkey);
    void GetShowCmd(out int piShowCmd);
    void SetShowCmd(int piShowCmd);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cchIconPath, out int piIcon);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, uint dwReserved);
    void Resolve(IntPtr hwnd, uint fFlags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
  }
  [ComImport, Guid("0000010b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IPersistFile {
    void GetClassID(out Guid pClassID);
    [PreserveSig] int IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, bool fRemember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
    void GetCurFile([Out, MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
  }
  [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IPropertyStore {
    void GetCount(out uint cProps);
    void GetAt(uint iProp, out PROPERTYKEY pkey);
    void GetValue(ref PROPERTYKEY key, out PropVariant pv);
    void SetValue(ref PROPERTYKEY key, ref PropVariant pv);
    void Commit();
  }
  [StructLayout(LayoutKind.Sequential, Pack = 4)]
  struct PROPERTYKEY { public Guid fmtid; public uint pid; }
  [StructLayout(LayoutKind.Sequential)]
  struct PropVariant {
    public ushort vt; public ushort wReserved1; public ushort wReserved2; public ushort wReserved3;
    public IntPtr p; public int p2;
  }
  [DllImport("ole32.dll")] static extern int PropVariantClear(ref PropVariant pvar);
}
'@.Replace('__HUGE_VERSION__', $version) | Set-Content -Path $setupCs -Encoding ASCII

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
  %LOCALAPPDATA%\Programs\Huge\
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
