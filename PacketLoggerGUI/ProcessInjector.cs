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

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(uint dwDesiredAccess, bool bInheritHandle, int dwProcessId);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr VirtualAllocEx(IntPtr hProcess, IntPtr lpAddress, uint dwSize, uint flAllocationType, uint flProtect);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool WriteProcessMemory(IntPtr hProcess, IntPtr lpBaseAddress, byte[] lpBuffer, uint nSize, out int lpNumberOfBytesWritten);

        [DllImport("kernel32.dll")]
        private static extern IntPtr GetProcAddress(IntPtr hModule, string lpProcName);

        [DllImport("kernel32.dll")]
        private static extern IntPtr GetModuleHandle(string lpModuleName);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr CreateRemoteThread(IntPtr hProcess, IntPtr lpThreadAttributes, uint dwStackSize, IntPtr lpStartAddress, IntPtr lpParameter, uint dwCreationFlags, out int lpThreadId);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr hObject);

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

        private const uint PROCESS_ALL_ACCESS = 0x1F0FFF;
        private const uint MEM_COMMIT = 0x1000;
        private const uint MEM_RESERVE = 0x2000;
        private const uint PAGE_READWRITE = 0x04;

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

        public static bool Inject(int pid, string dllPath)
        {
            IntPtr hProc = OpenProcess(PROCESS_ALL_ACCESS, false, pid);
            if (hProc == IntPtr.Zero)
                return false;

            try
            {
                byte[] dllBytes = Encoding.ASCII.GetBytes(dllPath + "\0");

                IntPtr allocMem = VirtualAllocEx(hProc, IntPtr.Zero, (uint)dllBytes.Length, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
                if (allocMem == IntPtr.Zero)
                    return false;

                if (!WriteProcessMemory(hProc, allocMem, dllBytes, (uint)dllBytes.Length, out _))
                    return false;

                IntPtr loadLibAddr = GetProcAddress(GetModuleHandle("kernel32.dll"), "LoadLibraryA");
                if (loadLibAddr == IntPtr.Zero)
                    return false;

                IntPtr hThread = CreateRemoteThread(hProc, IntPtr.Zero, 0, loadLibAddr, allocMem, 0, out _);
                if (hThread == IntPtr.Zero)
                    return false;

                CloseHandle(hThread);
                return true;
            }
            finally
            {
                CloseHandle(hProc);
            }
        }
    }
}
