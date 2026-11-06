#pragma once

#include <windows.h>

#include <string>
#include <vector>

struct ProcessInfo {
    DWORD         pid = 0;
    std::wstring  exe_name;
    std::wstring  window_title;
};

// Enumerates running processes whose executable name matches one of the known
// NosTale clients. This uses Windows PIDs inside the prefix, so it works the
// same under Wine and on Windows; the Linux PID of the wineserver process is
// irrelevant.
std::vector<ProcessInfo> FindNostaleProcesses();

// Appends " - <pid>" to each game window title so several clients can be told
// apart. Harmless when only one client is running.
void TagWindowsWithPid(const std::vector<ProcessInfo>& processes);

std::string narrow(const std::wstring& text);
