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
    int   number;
};

// Drop an earlier " [#<n>]" marker so renumbering does not stack suffixes.
std::wstring without_number_marker(const std::wstring& title) {
    const std::wstring open = L" [#";
    const std::size_t start = title.rfind(open);
    if (start == std::wstring::npos || title.empty() ||
        title.back() != L']') {
        return title;
    }
    const std::size_t first = start + open.size();
    if (first + 1 >= title.size()) {
        return title;  // no digits between " [#" and "]"
    }
    for (std::size_t i = first; i + 1 < title.size(); ++i) {
        if (title[i] < L'0' || title[i] > L'9') {
            return title;
        }
    }
    return title.substr(0, start);
}

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
    std::swprintf(suffix, 32, L" [#%d]", data->number);

    const std::wstring base = without_number_marker(title);
    const std::wstring wanted = base + suffix;
    if (std::wstring(title) != wanted) {
        SetWindowTextW(window, wanted.c_str());
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
            info.has_window = !info.window_title.empty();
            if (info.window_title.empty()) {
                info.window_title = L"(no window title)";
            }
            result.push_back(info);
        } while (Process32NextW(snapshot, &entry));
    }

    CloseHandle(snapshot);
    return result;
}

void TagWindowsWithNumber(std::vector<ProcessInfo>& processes) {
    int number = 0;
    for (ProcessInfo& process : processes) {
        if (!process.has_window) {
            continue;
        }
        ++number;
        TagSearch data = {process.pid, number};
        EnumWindows(tag_enum_proc, reinterpret_cast<LPARAM>(&data));
        // Keep the printed title identical to what the title bar now shows.
        const std::wstring marked = window_title_for(process.pid);
        if (!marked.empty()) {
            process.window_title = marked;
        }
    }
}
