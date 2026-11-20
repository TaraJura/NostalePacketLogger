#include "injector.h"

#include <tlhelp32.h>

#include <cstdio>
#include <cwctype>
#include <string>

#include "processes.h"

namespace {

// A logger module stays mapped after QUIT (Windows cannot unload our DLL), and
// two DLLs hooking the same send function corrupt each other. Refuse to inject
// into a process that already has one; the game has to be restarted instead.
std::wstring find_logger_module(DWORD pid) {
    HANDLE snapshot =
        CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid);
    if (snapshot == INVALID_HANDLE_VALUE) {
        return std::wstring();
    }

    MODULEENTRY32W entry = {};
    entry.dwSize = sizeof(entry);
    std::wstring found;
    if (Module32FirstW(snapshot, &entry)) {
        do {
            std::wstring name(entry.szModule);
            if (name.size() < 12) {
                continue;
            }
            std::wstring prefix = name.substr(0, 12);
            for (wchar_t& c : prefix) {
                c = static_cast<wchar_t>(std::towlower(c));
            }
            if (prefix == L"packetlogger") {
                found = name;
                break;
            }
        } while (Module32NextW(snapshot, &entry));
    }
    CloseHandle(snapshot);
    return found;
}


std::string last_error_text(DWORD code) {
    char buffer[512] = {0};
    const DWORD length = FormatMessageA(
        FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, nullptr,
        code, 0, buffer, sizeof(buffer), nullptr);
    if (length == 0) {
        std::snprintf(buffer, sizeof(buffer), "error %lu",
                      static_cast<unsigned long>(code));
        return std::string(buffer);
    }
    std::string text(buffer, length);
    while (!text.empty() && (text.back() == '\r' || text.back() == '\n')) {
        text.pop_back();
    }
    return text;
}

}  // namespace

bool InjectDll(DWORD pid, const std::string& dll_path, std::string& error,
               bool force) {
    if (pid == 0 || dll_path.empty()) {
        error = "invalid pid or DLL path";
        return false;
    }

    if (!force) {
        const std::wstring loaded = find_logger_module(pid);
        if (!loaded.empty()) {
            error = "a packet logger is already loaded in this process (" +
                    narrow(loaded) +
                    "); restart the game client before injecting again";
            return false;
        }
    }

    HANDLE process = OpenProcess(
        PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION |
            PROCESS_VM_WRITE | PROCESS_VM_READ,
        FALSE, pid);
    if (process == nullptr) {
        error = "OpenProcess: " + last_error_text(GetLastError());
        return false;
    }

    const SIZE_T size = dll_path.size() + 1;
    void* remote = VirtualAllocEx(process, nullptr, size,
                                  MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (remote == nullptr) {
        error = "VirtualAllocEx: " + last_error_text(GetLastError());
        CloseHandle(process);
        return false;
    }

    SIZE_T written = 0;
    if (!WriteProcessMemory(process, remote, dll_path.c_str(), size, &written) ||
        written != size) {
        error = "WriteProcessMemory: " + last_error_text(GetLastError());
        VirtualFreeEx(process, remote, 0, MEM_RELEASE);
        CloseHandle(process);
        return false;
    }

    // kernel32 is mapped at the same base in every 32-bit process of a session
    // (Windows ASLR randomises system DLLs once per boot; Wine likewise keeps
    // one mapping), so the address of LoadLibraryA is valid in the target too.
    const HMODULE kernel32 = GetModuleHandleA("kernel32.dll");
    const FARPROC load_library =
        kernel32 != nullptr ? GetProcAddress(kernel32, "LoadLibraryA") : nullptr;
    if (load_library == nullptr) {
        error = "GetProcAddress(LoadLibraryA) failed";
        VirtualFreeEx(process, remote, 0, MEM_RELEASE);
        CloseHandle(process);
        return false;
    }

    // FARPROC -> LPTHREAD_START_ROUTINE is the documented way to use the
    // address returned by GetProcAddress as a remote thread entry point.
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wcast-function-type"
    HANDLE thread = CreateRemoteThread(
        process, nullptr, 0, reinterpret_cast<LPTHREAD_START_ROUTINE>(load_library),
        remote, 0, nullptr);
#pragma GCC diagnostic pop
    if (thread == nullptr) {
        error = "CreateRemoteThread: " + last_error_text(GetLastError());
        VirtualFreeEx(process, remote, 0, MEM_RELEASE);
        CloseHandle(process);
        return false;
    }

    const DWORD wait = WaitForSingleObject(thread, 10000);
    if (wait != WAIT_OBJECT_0) {
        // Do NOT free `remote` here: if the remote thread is still running it
        // may not have read the DLL path yet. Leaking a few hundred bytes in
        // the target is strictly better than a use-after-free in the game.
        error = wait == WAIT_TIMEOUT ? "timed out waiting for LoadLibraryA"
                                     : "WaitForSingleObject failed";
        CloseHandle(thread);
        CloseHandle(process);
        return false;
    }

    DWORD exit_code = 0;
    const bool have_exit_code = GetExitCodeThread(thread, &exit_code) != FALSE;

    CloseHandle(thread);
    VirtualFreeEx(process, remote, 0, MEM_RELEASE);
    CloseHandle(process);

    if (!have_exit_code) {
        error = "GetExitCodeThread failed";
        return false;
    }
    if (exit_code == 0) {
        error = "LoadLibraryA returned NULL inside the game (bad path, missing "
                "architecture, or the DLL was rejected)";
        return false;
    }

    return true;
}
