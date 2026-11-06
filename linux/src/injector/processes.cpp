#include "processes.h"

#include <tlhelp32.h>

#include <cstdio>
#include <cwchar>

namespace {

const wchar_t* const kTargets[] = {
    L"NostaleClientX.exe",
    L"NostaleX.dat",
    L"CustomClient.exe",
};

bool is_target(const wchar_t* exe_name) {
    for (const wchar_t* target : kTargets) {
        if (_wcsicmp(exe_name, target) == 0) {
            return true;
        }
    }
    return false;
}

struct TitleSearch {
    DWORD        pid;
    std::wstring title;
};

// EnumWindows requires a __stdcall (CALLBACK) function, so this cannot be a
// plain lambda: on 32-bit Windows a lambda's operator() is __cdecl.
BOOL CALLBACK title_enum_proc(HWND window, LPARAM parameter) {
    auto* data = reinterpret_cast<TitleSearch*>(parameter);

    if (!IsWindowVisible(window)) {
        return TRUE;
    }

    DWORD window_pid = 0;
    GetWindowThreadProcessId(window, &window_pid);
    if (window_pid != data->pid) {
        return TRUE;
    }

    wchar_t title[256] = {0};
    GetWindowTextW(window, title, 256);
    if (title[0] != L'\0') {
        data->title = title;
        return FALSE;
    }
    return TRUE;
}

std::wstring window_title_for(DWORD pid) {
    TitleSearch data = {pid, L""};
    EnumWindows(title_enum_proc, reinterpret_cast<LPARAM>(&data));
    return data.title;
}

struct TagSearch {
    DWORD pid;
};

BOOL CALLBACK tag_enum_proc(HWND window, LPARAM parameter) {
    auto* data = reinterpret_cast<TagSearch*>(parameter);

    if (!IsWindowVisible(window)) {
        return TRUE;
    }

    DWORD window_pid = 0;
    GetWindowThreadProcessId(window, &window_pid);
    if (window_pid != data->pid) {
        return TRUE;
    }

    wchar_t title[256] = {0};
    GetWindowTextW(window, title, 256);
    if (title[0] == L'\0') {
        return TRUE;
    }

    wchar_t suffix[32] = {0};
    std::swprintf(suffix, 32, L" - %lu",
                  static_cast<unsigned long>(data->pid));

    const std::wstring current(title);
    if (current.find(suffix) == std::wstring::npos) {
        SetWindowTextW(window, (current + suffix).c_str());
    }
    return FALSE;
}

}  // namespace

std::string narrow(const std::wstring& text) {
    if (text.empty()) {
        return std::string();
    }

    const int size = WideCharToMultiByte(CP_UTF8, 0, text.c_str(),
                                         static_cast<int>(text.size()),
                                         nullptr, 0, nullptr, nullptr);
    if (size <= 0) {
        return std::string();
    }

    std::string result(static_cast<std::size_t>(size), '\0');
    WideCharToMultiByte(CP_UTF8, 0, text.c_str(),
                        static_cast<int>(text.size()), result.data(), size,
                        nullptr, nullptr);
    return result;
}

std::vector<ProcessInfo> FindNostaleProcesses() {
    std::vector<ProcessInfo> result;

    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snapshot == INVALID_HANDLE_VALUE) {
        return result;
    }

    PROCESSENTRY32W entry = {};
    entry.dwSize = sizeof(entry);

    if (Process32FirstW(snapshot, &entry)) {
        do {
            if (!is_target(entry.szExeFile)) {
                continue;
            }

            ProcessInfo info;
            info.pid = entry.th32ProcessID;
            info.exe_name = entry.szExeFile;
            info.window_title = window_title_for(info.pid);
            if (info.window_title.empty()) {
                info.window_title = L"(no window title)";
            }
            result.push_back(info);
        } while (Process32NextW(snapshot, &entry));
    }

    CloseHandle(snapshot);
    return result;
}

void TagWindowsWithPid(const std::vector<ProcessInfo>& processes) {
    for (const ProcessInfo& process : processes) {
        TagSearch data = {process.pid};
        EnumWindows(tag_enum_proc, reinterpret_cast<LPARAM>(&data));
    }
}
