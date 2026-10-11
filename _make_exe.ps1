$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ico = Join-Path $root 'huge.ico'
$exe = Join-Path $root 'Huge.exe'
$cs = Join-Path $root '_HugeLauncher.cs'
$appId = 'vsnq.Huge'

# Huge.exe launches a private renamed pythonw.exe (HugeRuntime.exe) whose
# embedded icon is the Huge logo. The Huge window then belongs to that process,
# so the taskbar / Alt-Tab icon is Huge instead of Python.
$code = @'
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;

internal static class Huge {
  [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
  private static extern int SetCurrentProcessExplicitAppUserModelID(string appID);

  [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
  static extern IntPtr BeginUpdateResource(string pFileName, bool bDeleteExistingResources);
  [DllImport("kernel32.dll", SetLastError = true)]
  static extern bool UpdateResource(IntPtr hUpdate, IntPtr lpType, IntPtr lpName, ushort wLanguage, byte[] lpData, uint cbData);
  [DllImport("kernel32.dll", SetLastError = true)]
  static extern bool EndUpdateResource(IntPtr hUpdate, bool fDiscard);

  static readonly IntPtr RT_ICON = (IntPtr)3;
  static readonly IntPtr RT_GROUP_ICON = (IntPtr)14;

  [STAThread]
  static void Main() {
    try { SetCurrentProcessExplicitAppUserModelID("vsnq.Huge"); } catch { }

    string dir = Path.GetDirectoryName(Application.ExecutablePath);
    if (string.IsNullOrEmpty(dir)) dir = Environment.CurrentDirectory;
    Directory.SetCurrentDirectory(dir);

    try {
      string pyw;
      string host = EnsureHost(dir, out pyw);
      var psi = new ProcessStartInfo();
      psi.FileName = host;
      psi.Arguments = "\"" + Path.Combine(dir, "desktop.py") + "\"";
      psi.WorkingDirectory = dir;
      psi.UseShellExecute = false;
      string pyHome = Path.GetDirectoryName(pyw);
      if (!string.IsNullOrEmpty(pyHome)) {
        psi.EnvironmentVariables["PYTHONHOME"] = pyHome;
        string path = Environment.GetEnvironmentVariable("PATH") ?? "";
        psi.EnvironmentVariables["PATH"] = pyHome + ";" + path;
      }
      Process.Start(psi);
    } catch (Exception ex) {
      // Fallback: plain py launch if runtime host cannot be prepared.
      try {
        var psi = new ProcessStartInfo();
        psi.FileName = "py";
        psi.Arguments = "-3 desktop.py";
        psi.WorkingDirectory = dir;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        Process.Start(psi);
      } catch (Exception ex2) {
        MessageBox.Show(ex.Message + "\n\n" + ex2.Message, "Huge");
      }
    }
  }

  static string EnsureHost(string dir, out string pyw) {
    string hostDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Huge", "runtime");
    Directory.CreateDirectory(hostDir);
    string host = Path.Combine(hostDir, "HugeRuntime.exe");
    string stamp = Path.Combine(hostDir, "icon.stamp");
    string ico = Path.Combine(dir, "huge.ico");
    pyw = FindPythonw();
    if (string.IsNullOrEmpty(pyw) || !File.Exists(pyw)) throw new Exception("Could not locate pythonw.exe");
    string pyDir = Path.GetDirectoryName(pyw);

    foreach (string pattern in new string[] { "python*.dll", "vcruntime*.dll" }) {
      foreach (string src in Directory.GetFiles(pyDir, pattern)) {
        string dest = Path.Combine(hostDir, Path.GetFileName(src));
        if (!File.Exists(dest) || File.GetLastWriteTimeUtc(src) > File.GetLastWriteTimeUtc(dest)) {
          File.Copy(src, dest, true);
        }
      }
    }

    bool needCopy = !File.Exists(host) || File.GetLastWriteTimeUtc(pyw) > File.GetLastWriteTimeUtc(host);
    if (needCopy) File.Copy(pyw, host, true);

    string icoHash = File.GetLastWriteTimeUtc(ico).Ticks.ToString() + ":" + new FileInfo(ico).Length;
    bool needIcon = needCopy || !File.Exists(stamp) || File.ReadAllText(stamp) != icoHash;
    if (needIcon) {
      PatchExeIcon(host, ico);
      File.WriteAllText(stamp, icoHash);
    }
    return host;
  }

  static string FindPythonw() {
    var which = new ProcessStartInfo();
    which.FileName = "py";
    which.Arguments = "-3 -c \"import sys; print(sys.executable)\"";
    which.UseShellExecute = false;
    which.RedirectStandardOutput = true;
    which.CreateNoWindow = true;
    using (var p = Process.Start(which)) {
      string line = p.StandardOutput.ReadLine();
      p.WaitForExit(8000);
      if (string.IsNullOrWhiteSpace(line)) return null;
      string exe = line.Trim();
      string dir = Path.GetDirectoryName(exe);
      string candidate = Path.Combine(dir, "pythonw.exe");
      if (File.Exists(candidate)) return candidate;
      if (File.Exists(exe)) return exe;
    }
    return null;
  }

  static void PatchExeIcon(string exePath, string icoPath) {
    byte[] ico = File.ReadAllBytes(icoPath);
    if (ico.Length < 6) throw new Exception("Invalid ico");
    ushort count = BitConverter.ToUInt16(ico, 4);
    var images = new byte[count][];
    int offsetDir = 6;
    for (int i = 0; i < count; i++) {
      int bytesInRes = BitConverter.ToInt32(ico, offsetDir + 8);
      int imgOffset = BitConverter.ToInt32(ico, offsetDir + 12);
      images[i] = new byte[bytesInRes];
      Buffer.BlockCopy(ico, imgOffset, images[i], 0, bytesInRes);
      offsetDir += 16;
    }
    using (var ms = new MemoryStream())
    using (var bw = new BinaryWriter(ms)) {
      bw.Write((ushort)0);
      bw.Write((ushort)1);
      bw.Write(count);
      offsetDir = 6;
      for (int i = 0; i < count; i++) {
        bw.Write(ico[offsetDir + 0]);
        bw.Write(ico[offsetDir + 1]);
        bw.Write(ico[offsetDir + 2]);
        bw.Write((byte)0);
        bw.Write(BitConverter.ToUInt16(ico, offsetDir + 4));
        bw.Write(BitConverter.ToUInt16(ico, offsetDir + 6));
        bw.Write(BitConverter.ToUInt32(ico, offsetDir + 8));
        bw.Write((ushort)(i + 1));
        offsetDir += 16;
      }
      byte[] group = ms.ToArray();
      IntPtr h = BeginUpdateResource(exePath, false);
      if (h == IntPtr.Zero) throw new Exception("BeginUpdateResource failed");
      bool ok = true;
      for (int i = 0; i < count; i++) {
        ok = ok && UpdateResource(h, RT_ICON, (IntPtr)(i + 1), 0x0409, images[i], (uint)images[i].Length);
      }
      ok = ok && UpdateResource(h, RT_GROUP_ICON, (IntPtr)1, 0x0409, group, (uint)group.Length);
      if (!EndUpdateResource(h, !ok)) throw new Exception("EndUpdateResource failed");
      if (!ok) throw new Exception("UpdateResource failed");
    }
  }
}
'@

Set-Content -Path $cs -Value $code -Encoding ASCII
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $compiler)) {
  $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path $compiler)) { throw 'csc.exe not found' }
& $compiler /nologo /target:winexe /win32icon:"$ico" /out:"$exe" /r:System.Windows.Forms.dll /r:System.dll $cs
if ($LASTEXITCODE -ne 0) { throw 'csc failed' }
Remove-Item $cs -Force
Write-Host "wrote $exe"

$helper = @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class HugeLnk {
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
    void SetShowCmd(int iShowCmd);
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
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
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

  public static void Create(string lnkPath, string target, string workDir, string iconPath, string appId) {
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
    pv.p = Marshal.StringToCoTaskMemUni(appId);
    store.SetValue(ref key, ref pv);
    store.Commit();
    PropVariantClear(ref pv);
    var file = (IPersistFile)link;
    file.Save(lnkPath, true);
  }
}
'@

# File.Exists needs System.IO in the C# helper
$helper = $helper.Replace('public static class HugeLnk {', "public static class HugeLnk {`r`n  // uses System.IO.File")
$helper = "using System.IO;`r`n" + $helper

if (-not ([System.Management.Automation.PSTypeName]'HugeLnk').Type) {
  Add-Type -TypeDefinition $helper -Language CSharp
}

function New-HugeShortcut([string]$LnkPath, [string]$Target, [string]$WorkDir, [string]$IconPath, [string]$AppUserModelId) {
  [HugeLnk]::Create($LnkPath, $Target, $WorkDir, $IconPath, $AppUserModelId)
  Write-Host "shortcut $LnkPath"
}

$desktop = [Environment]::GetFolderPath('Desktop')
New-HugeShortcut -LnkPath (Join-Path $desktop 'Huge.lnk') -Target $exe -WorkDir $root -IconPath $ico -AppUserModelId $appId

$startMenu = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'
New-Item -ItemType Directory -Path $startMenu -Force | Out-Null
New-HugeShortcut -LnkPath (Join-Path $startMenu 'Huge.lnk') -Target $exe -WorkDir $root -IconPath $ico -AppUserModelId $appId
