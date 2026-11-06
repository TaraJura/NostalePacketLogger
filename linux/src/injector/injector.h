#pragma once

#include <windows.h>

#include <string>

// Injects `dll_path` into `pid` with the classic
// VirtualAllocEx + WriteProcessMemory + CreateRemoteThread(LoadLibraryA) chain.
//
// Waits for the remote thread and treats a NULL HMODULE (exit code 0) as a
// failure, so a missing 32-bit dependency or a blocked DLL is reported instead
// of silently doing nothing. The caller must pass an absolute path: the target
// process resolves relative paths against its own working directory.
bool InjectDll(DWORD pid, const std::string& dll_path, std::string& error);
