using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace PacketLoggerGUI
{
    public class NosTaleProcess
    {
        public int Pid { get; set; }
        public string ExeName { get; set; } = "";
        public string WindowTitle { get; set; } = "";

        public override string ToString()
        {
            return $"PID: {Pid}  |  {ExeName}  |  {WindowTitle}";
        }
    }

    public static class ProcessInjector
    {
        private static readonly string[] TargetNames = { "NostaleClientX.exe", "NostaleX.dat", "CustomClient.exe" };

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern bool SetWindowText(IntPtr hWnd, string lpString);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool IsWindowVisible(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out int lpdwProcessId);

        private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

        public static List<NosTaleProcess> FindNosTaleProcesses()
        {
            var result = new List<NosTaleProcess>();
            var processes = Process.GetProcesses();

            foreach (var proc in processes)
            {
                try
                {
                    bool isTarget = false;
                    foreach (var name in TargetNames)
                    {
                        if (proc.ProcessName.Equals(System.IO.Path.GetFileNameWithoutExtension(name), StringComparison.OrdinalIgnoreCase))
                        {
                            isTarget = true;
                            break;
                        }
                    }

                    if (!isTarget) continue;

                    // Only include processes with visible windows
                    if (proc.MainWindowHandle == IntPtr.Zero) continue;

                    var info = new NosTaleProcess
                    {
                        Pid = proc.Id,
                        ExeName = proc.ProcessName + ".exe",
                        WindowTitle = GetWindowTitle(proc.Id)
                    };

                    if (string.IsNullOrEmpty(info.WindowTitle))
                        info.WindowTitle = "(no title)";

                    result.Add(info);
                }
                catch { }
            }

            return result;
        }

        private static string GetWindowTitle(int pid)
        {
            string title = "";
            EnumWindows((hWnd, lParam) =>
            {
                if (!IsWindowVisible(hWnd)) return true;
                GetWindowThreadProcessId(hWnd, out int windowPid);
                if (windowPid == pid)
                {
                    var sb = new StringBuilder(256);
                    GetWindowText(hWnd, sb, 256);
                    if (sb.Length > 0)
                    {
                        title = sb.ToString();
                        return false; // stop
                    }
                }
                return true;
            }, IntPtr.Zero);
            return title;
        }

        public static void TagWindowWithPID(int pid)
        {
            EnumWindows((hWnd, lParam) =>
            {
                if (!IsWindowVisible(hWnd)) return true;
                GetWindowThreadProcessId(hWnd, out int windowPid);
                if (windowPid == pid)
                {
                    var sb = new StringBuilder(256);
                    GetWindowText(hWnd, sb, 256);
                    string current = sb.ToString();
                    string pidTag = $" - {pid}";
                    if (current.Length > 0 && !current.Contains(pidTag))
                    {
                        SetWindowText(hWnd, current + pidTag);
                    }
                    return false;
                }
                return true;
            }, IntPtr.Zero);
        }
    }
}
