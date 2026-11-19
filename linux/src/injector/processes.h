#pragma once

#include <windows.h>

#include <string>
#include <vector>

struct ProcessInfo {
    DWORD         pid = 0;
    std::wstring  exe_name;
    std::wstring  window_title;
    bool          has_window = false;
};

// Enumerates running processes whose executable name matches one of the known
// NosTale clients. This uses Windows PIDs inside the prefix, so it works the
// same under Wine and on Windows; the Linux PID of the wineserver process is
// irrelevant.
std::vector<ProcessInfo> FindNostaleProcesses();

// Appends " [#<n>]" to each visible game window title, numbering the windows
// 1..k in list order, so the picker row and the game window share a number.
// Repeated calls renumber instead of stacking markers.
void TagWindowsWithNumber(std::vector<ProcessInfo>& processes);

std::string narrow(const std::wstring& text);
