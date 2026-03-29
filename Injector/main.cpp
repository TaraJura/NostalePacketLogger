#include <iostream>
#include <string>
#include "Utils.h"
#include "Injector.h"

#define MAX_SIZE 255

struct ProcessInfo {
    DWORD pid;
    std::wstring exeName;
    std::wstring windowTitle;
};

std::vector<ProcessInfo> GetNosTaleProcesses()
{
    std::vector<std::wstring> targetNames = { L"NostaleClientX.exe", L"NostaleX.dat", L"CustomClient.exe" };
    std::vector<DWORD> pidList = GetPIDList(targetNames);
    std::vector<ProcessInfo> result;

    for (DWORD pid : pidList)
    {
        ProcessInfo info;
        info.pid = pid;
        info.windowTitle = L"(no window title)";

        // Get exe name from snapshot
        HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        PROCESSENTRY32W entry;
        entry.dwSize = sizeof(entry);
        if (Process32FirstW(snap, &entry)) {
            do {
                if (entry.th32ProcessID == pid) {
                    info.exeName = entry.szExeFile;
                    break;
                }
            } while (Process32NextW(snap, &entry));
        }
        CloseHandle(snap);

        // Get window title for this PID
        struct EnumData {
            DWORD pid;
            std::wstring title;
        } enumData = { pid, L"" };

        EnumWindows([](HWND hwnd, LPARAM lParam) -> BOOL {
            auto* data = reinterpret_cast<EnumData*>(lParam);
            if (!IsWindowVisible(hwnd)) return TRUE;
            DWORD windowPid;
            GetWindowThreadProcessId(hwnd, &windowPid);
            if (windowPid == data->pid) {
                wchar_t title[256] = { 0 };
                GetWindowTextW(hwnd, title, 256);
                if (wcslen(title) > 0) {
                    data->title = title;
                    return FALSE; // stop enumerating
                }
            }
            return TRUE;
        }, reinterpret_cast<LPARAM>(&enumData));

        if (!enumData.title.empty())
            info.windowTitle = enumData.title;

        result.push_back(info);
    }

    return result;
}

void TagWindowsWithPID(const std::vector<ProcessInfo>& processes)
{
    for (const auto& proc : processes)
    {
        struct EnumData {
            DWORD pid;
        } data = { proc.pid };

        EnumWindows([](HWND hwnd, LPARAM lParam) -> BOOL {
            auto* d = reinterpret_cast<EnumData*>(lParam);
            if (!IsWindowVisible(hwnd)) return TRUE;
            DWORD windowPid;
            GetWindowThreadProcessId(hwnd, &windowPid);
            if (windowPid == d->pid) {
                wchar_t title[256] = { 0 };
                GetWindowTextW(hwnd, title, 256);
                if (wcslen(title) > 0) {
                    // Only tag if not already tagged with PID
                    std::wstring current(title);
                    wchar_t pidStr[32];
                    swprintf_s(pidStr, L" - %lu", d->pid);
                    if (current.find(pidStr) == std::wstring::npos) {
                        std::wstring newTitle = current + pidStr;
                        SetWindowTextW(hwnd, newTitle.c_str());
                    }
                    return FALSE;
                }
            }
            return TRUE;
        }, reinterpret_cast<LPARAM>(&data));
    }
}

int main(int argc, char* argv[])
{
    char currentDirectory[MAX_SIZE] = { 0 };
    GetCurrentDirectoryA(MAX_SIZE, currentDirectory);
    std::string dllPath = std::string(currentDirectory) + "\\PacketLogger.dll";

    // Silent mode: Injector.exe --pid <PID> [--dll <path>]
    // Used by the GUI to inject without user interaction
    for (int i = 1; i < argc; i++)
    {
        if (std::string(argv[i]) == "--pid" && i + 1 < argc)
        {
            DWORD pid = (DWORD)atoi(argv[i + 1]);
            // Check for optional --dll argument
            for (int j = i + 2; j < argc; j++)
            {
                if (std::string(argv[j]) == "--dll" && j + 1 < argc)
                {
                    dllPath = argv[j + 1];
                    break;
                }
            }
            if (Inject(pid, dllPath.c_str()))
                return 0; // success
            else
                return 1; // failure
        }
    }

    // Interactive mode (no arguments)
    printf("========================================\n");
    printf("     NosTale Packet Logger - Injector\n");
    printf("========================================\n\n");

    auto processes = GetNosTaleProcesses();

    // Tag game windows with their PID so user can identify them
    TagWindowsWithPID(processes);

    if (processes.empty())
    {
        printf("[ERROR] No NosTale process found. Make sure the game is running.\n");
        printf("\nPress Enter to exit...");
        getchar();
        return 1;
    }

    printf("Found %d NosTale process(es):\n\n", (int)processes.size());

    for (int i = 0; i < (int)processes.size(); i++)
    {
        printf("  [%d] PID: %-6lu | %ls | %ls\n",
            i + 1,
            processes[i].pid,
            processes[i].exeName.c_str(),
            processes[i].windowTitle.c_str());
    }

    printf("\n  [0] Inject into ALL\n");
    printf("\nSelect option: ");

    int choice = -1;
    scanf_s("%d", &choice);

    if (choice == 0)
    {
        for (auto& proc : processes)
        {
            if (Inject(proc.pid, dllPath.c_str()))
                printf("[%lu] Injected successfully.\n", proc.pid);
            else
                printf("[%lu] Injection FAILED.\n", proc.pid);
        }
    }
    else if (choice >= 1 && choice <= (int)processes.size())
    {
        auto& proc = processes[choice - 1];
        if (Inject(proc.pid, dllPath.c_str()))
            printf("[%lu] Injected successfully.\n", proc.pid);
        else
            printf("[%lu] Injection FAILED.\n", proc.pid);
    }
    else
    {
        printf("[ERROR] Invalid selection.\n");
    }

    printf("\nPress Enter to exit...");
    getchar(); getchar();
    return 0;
}
