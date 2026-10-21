// MeowKeeper+ isolated prototype. No driver installation, elevation or network.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using Microsoft.Win32;
using Microsoft.Win32.SafeHandles;

class CpuSensorHelper
{
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 65536 };
    static object Computer;
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern SafeFileHandle CreateFile(string name, uint access, uint share,
                                           IntPtr security, uint creation, uint flags, IntPtr template);
    static string Clean(object value)
    {
        string text = Convert.ToString(value).Replace("\r", " ").Replace("\n", " ");
        return text.Substring(0, Math.Min(text.Length, 160));
    }
    static object Property(object value, string name) { return value.GetType().GetProperty(name).GetValue(value, null); }
    static bool DriverPresent()
    {
        foreach (RegistryView view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
        using (RegistryKey root = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, view))
        using (RegistryKey key = root.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO"))
        {
            Version version;
            if (key != null && Version.TryParse(Convert.ToString(key.GetValue("DisplayVersion")), out version)) return true;
        }
        return false;
    }
    static Dictionary<string, object> Read()
    {
        var result = new Dictionary<string, object> {
            {"schema_version", 1}, {"provider", "libre_hardware_monitor"},
            {"observed_at", DateTimeOffset.UtcNow.ToString("o")}, {"sensors", new List<object>()},
            {"status", "unavailable"}, {"reason", "sensor_missing"}
        };
        // 0.9.6 uses an externally installed PawnIO driver. Never install it here.
        if (!DriverPresent()) { result["status"] = "unsupported"; result["reason"] = "driver_missing"; return result; }
        // A registry entry is not proof that this user can access the kernel device.
        // Only open the existing device; no IOCTL, installation or ACL changes here.
        using (SafeFileHandle handle = CreateFile(@"\\?\GLOBALROOT\Device\PawnIO", 0xC0000000, 3, IntPtr.Zero, 3, 0x80, IntPtr.Zero))
        {
            if (handle.IsInvalid)
            {
                result["status"] = Marshal.GetLastWin32Error() == 5 ? "permission_required" : "unavailable";
                result["reason"] = "driver_unavailable";
                return result;
            }
        }
        if (Computer == null)
        {
            string dll = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "LibreHardwareMonitorLib.dll");
            Assembly library = Assembly.LoadFrom(dll);
            if (library.GetName().Version != new Version(0, 9, 6, 0)) throw new InvalidOperationException();
            Type type = library.GetType("LibreHardwareMonitor.Hardware.Computer", true);
            Computer = Activator.CreateInstance(type);
            type.GetProperty("IsCpuEnabled").SetValue(Computer, true, null);
            type.GetMethod("Open").Invoke(Computer, null);
        }
        var sensors = (List<object>)result["sensors"];
        foreach (object hardware in (IEnumerable)Property(Computer, "Hardware"))
        {
            if (Clean(Property(hardware, "HardwareType")) != "Cpu") continue;
            hardware.GetType().GetMethod("Update").Invoke(hardware, null);
            foreach (object sensor in (IEnumerable)Property(hardware, "Sensors"))
            {
                if (Clean(Property(sensor, "SensorType")) != "Temperature") continue;
                object value = Property(sensor, "Value");
                sensors.Add(new Dictionary<string, object> {
                    {"hardware_name", Clean(Property(hardware, "Name"))},
                    {"sensor_name", Clean(Property(sensor, "Name"))},
                    {"sensor_id", Clean(Property(sensor, "Identifier"))},
                    {"temperature_c", value == null ? null : (object)Convert.ToDouble(value)}
                });
                if (sensors.Count >= 128) break;
            }
        }
        if (sensors.Count > 0) { result["status"] = "available"; result["reason"] = null; }
        return result;
    }
    static string ReadCommand()
    {
        var text = new StringBuilder();
        int next;
        while ((next = Console.Read()) != -1 && next != '\n')
        {
            if (text.Length >= 256) throw new InvalidDataException();
            if (next != '\r') text.Append((char)next);
        }
        return next == -1 && text.Length == 0 ? null : text.ToString();
    }
    static Dictionary<string, object> ReadSafely()
    {
        try { return Read(); }
        catch (Exception error)
        {
            Exception cause = error.GetBaseException();
            return new Dictionary<string, object> {
                {"schema_version", 1}, {"provider", "libre_hardware_monitor"},
                {"observed_at", DateTimeOffset.UtcNow.ToString("o")}, {"sensors", new object[0]},
                {"status", cause is UnauthorizedAccessException || cause is SecurityException ? "permission_required" : "error"},
                {"reason", cause is FileNotFoundException || cause is FileLoadException ? "dependency_missing" : "sensor_read_failed"}
            };
        }
    }
    static bool PlainPath(string path)
    {
        for (string item = path; !String.IsNullOrEmpty(item); item = Path.GetDirectoryName(item))
            if ((File.Exists(item) || Directory.Exists(item)) &&
                (File.GetAttributes(item) & FileAttributes.ReparsePoint) != 0) return false;
        return true;
    }
    static int RunBroker()
    {
        // No caller-supplied paths, commands, device operations or network IPC.
        if (!WindowsIdentity.GetCurrent().IsSystem) return 5;
        string executableRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "MeowKeeperPlusCpuSensor");
        if (!String.Equals(AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\'), executableRoot, StringComparison.OrdinalIgnoreCase)) return 5;
        string folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "MeowKeeperPlusCpuSensor");
        string lease = Path.Combine(folder, "request.txt"), cache = Path.Combine(folder, "temperature.json");
        if (!PlainPath(executableRoot) || !PlainPath(folder) || !PlainPath(lease) || !PlainPath(cache)) return 5;
        Stopwatch timer = Stopwatch.StartNew();
        while (timer.Elapsed.TotalSeconds < 55)
        {
            DateTimeOffset requested;
            try
            {
                if (!PlainPath(lease) || new FileInfo(lease).Length > 128) return 5;
                string text;
                using (var input = new FileStream(lease, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                using (var reader = new StreamReader(input))
                {
                    char[] buffer = new char[129];
                    int count = reader.ReadBlock(buffer, 0, buffer.Length);
                    if (count > 128) return 5;
                    text = new string(buffer, 0, count);
                }
                if (!DateTimeOffset.TryParse(text, out requested)) return 0;
                double age = (DateTimeOffset.UtcNow - requested).TotalSeconds;
                if (age < -2 || age > 20) return 0;
            }
            catch (IOException) { return 0; }
            var result = ReadSafely();
            result["broker_schema"] = 1;
            string temporary = Path.Combine(folder, "temperature.new");
            if (!PlainPath(cache) || !PlainPath(temporary)) return 5;
            File.WriteAllText(temporary, Json.Serialize(result), new UTF8Encoding(false));
            try
            {
                if (File.Exists(cache)) File.Replace(temporary, cache, null);
                else File.Move(temporary, cache);
            }
            catch (IOException) { if (File.Exists(temporary)) File.Delete(temporary); }
            Thread.Sleep(5000);
        }
        return 0;
    }
    static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        try
        {
            if (args.Length == 1 && args[0] == "--broker") return RunBroker();
            Guid snapshotId;
            if (args.Length == 2 && args[0] == "--snapshot" && Guid.TryParseExact(args[1], "N", out snapshotId))
            {
                // A finite, explicitly authorized elevated trial, not a service.
                var samples = new List<object>();
                Stopwatch elapsed = Stopwatch.StartNew();
                using (Process process = Process.GetCurrentProcess())
                {
                    for (int i = 0; i < 6; i++)
                    {
                        samples.Add(ReadSafely());
                        if (i < 5) Thread.Sleep(2000);
                    }
                    process.Refresh();
                    var report = new Dictionary<string, object> {
                        {"request_id", snapshotId.ToString("N")}, {"samples", samples},
                        {"elapsed_ms", elapsed.Elapsed.TotalMilliseconds},
                        {"cpu_ms", process.TotalProcessorTime.TotalMilliseconds},
                        {"peak_working_set_bytes", process.PeakWorkingSet64},
                        {"finished_at", DateTimeOffset.UtcNow.ToString("o")}
                    };
                    string target = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "cpu-snapshot-" + snapshotId.ToString("N") + ".json");
                    File.WriteAllText(target, Json.Serialize(report), new UTF8Encoding(false));
                }
                return 0;
            }
            if (args.Length == 2 && args[0] == "--inspect" && Path.GetFileName(args[1]) == args[1] && args[1].EndsWith(".dll"))
            {
                // Metadata-only dependency inspection; never runs assembly code.
                Assembly assembly = Assembly.ReflectionOnlyLoadFrom(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, args[1]));
                var references = new List<string>();
                foreach (AssemblyName reference in assembly.GetReferencedAssemblies()) references.Add(reference.Name);
                Console.WriteLine(Json.Serialize(new Dictionary<string, object> {
                    {"name", assembly.GetName().Name}, {"version", assembly.GetName().Version.ToString()}, {"references", references}
                }));
                return 0;
            }
            if (args.Length != 0) return 2;
            string line;
            while ((line = ReadCommand()) != null)
            {
                if (line == "stop") break;
                Guid id;
                if (!line.StartsWith("sample ") || !Guid.TryParseExact(line.Substring(7), "N", out id)) return 2;
                Dictionary<string, object> result = ReadSafely();
                result["request_id"] = id.ToString("N");
                Console.WriteLine(Json.Serialize(result));
                Console.Out.Flush();
            }
            return 0;
        }
        catch { return 2; }
        finally
        {
            if (Computer != null)
                try { Computer.GetType().GetMethod("Close").Invoke(Computer, null); } catch { }
        }
    }
}
