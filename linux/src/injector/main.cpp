#include <windows.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "injector.h"
#include "processes.h"

namespace {

constexpr const char* kDllName = "PacketLogger.dll";

std::string exe_directory() {
    char path[MAX_PATH] = {0};
    const DWORD length = GetModuleFileNameA(nullptr, path, sizeof(path));
    if (length == 0 || length >= sizeof(path)) {
        return std::string(".");
    }

    std::string full(path, length);
    const std::size_t slash = full.find_last_of("\\/");
    if (slash == std::string::npos) {
        return std::string(".");
    }
    return full.substr(0, slash);
}

std::string absolute_path(const std::string& path) {
    char resolved[MAX_PATH] = {0};
    const DWORD length =
        GetFullPathNameA(path.c_str(), sizeof(resolved), resolved, nullptr);
    if (length == 0 || length >= sizeof(resolved)) {
        return path;
    }
    return std::string(resolved, length);
}

void print_usage() {
    std::printf(
        "NosTale Packet Logger - injector\n"
        "\n"
        "Usage:\n"
        "  Injector.exe                     interactive menu\n"
        "  Injector.exe --all               inject every detected client\n"
        "  Injector.exe --pid <pid>         inject one client by Windows PID\n"
        "  Injector.exe --list              list detected clients\n"
        "\n"
        "Options:\n"
        "  --dll <path>                     DLL to inject (default: %s next to "
        "this exe)\n"
        "  --no-tag                         do not append the PID to window "
        "titles\n"
        "  --help                           this text\n"
        "\n"
        "Under Wine, use --all: the Windows PID of the client is not the Linux "
        "PID.\n",
        kDllName);
}

void print_processes(const std::vector<ProcessInfo>& processes) {
    if (processes.empty()) {
        std::printf("No NosTale client found. Start the game first.\n");
        return;
    }

    std::printf("Found %d NosTale process(es):\n\n",
                static_cast<int>(processes.size()));
    for (std::size_t i = 0; i < processes.size(); ++i) {
        std::printf("  [%d] PID %-6lu  %-20s  %s\n",
                    static_cast<int>(i + 1),
                    static_cast<unsigned long>(processes[i].pid),
                    narrow(processes[i].exe_name).c_str(),
                    narrow(processes[i].window_title).c_str());
    }
}

int inject_into(const ProcessInfo& process, const std::string& dll_path) {
    std::string error;
    if (InjectDll(process.pid, dll_path, error)) {
        std::printf("[+] injected into PID %lu (%s)\n",
                    static_cast<unsigned long>(process.pid),
                    narrow(process.exe_name).c_str());
        return 0;
    }
    std::printf("[!] PID %lu failed: %s\n",
                static_cast<unsigned long>(process.pid), error.c_str());
    return 1;
}

int interactive(const std::string& dll_path, bool tag_windows) {
    std::vector<ProcessInfo> processes = FindNostaleProcesses();

    if (tag_windows) {
        TagWindowsWithPid(processes);
    }

    print_processes(processes);
    if (processes.empty()) {
        return 2;
    }

    std::printf("\n  [0] inject into ALL\n\nSelect option: ");
    std::fflush(stdout);

    char line[32] = {0};
    if (std::fgets(line, sizeof(line), stdin) == nullptr) {
        return 1;
    }

    const long choice = std::strtol(line, nullptr, 10);
    int failures = 0;

    if (choice == 0) {
        for (const ProcessInfo& process : processes) {
            failures += inject_into(process, dll_path);
        }
    } else if (choice >= 1 &&
               choice <= static_cast<long>(processes.size())) {
        failures += inject_into(processes[static_cast<std::size_t>(choice - 1)],
                                dll_path);
    } else {
        std::printf("[!] invalid selection\n");
        return 1;
    }

    return failures == 0 ? 0 : 1;
}

}  // namespace

int main(int argc, char** argv) {
    std::string dll_path;
    long pid = 0;
    bool inject_all = false;
    bool list_only = false;
    bool tag_windows = true;

    for (int i = 1; i < argc; ++i) {
        const std::string argument = argv[i];

        if (argument == "--help" || argument == "-h") {
            print_usage();
            return 0;
        }
        if (argument == "--all") {
            inject_all = true;
            continue;
        }
        if (argument == "--list") {
            list_only = true;
            continue;
        }
        if (argument == "--no-tag") {
            tag_windows = false;
            continue;
        }
        if (argument == "--pid" && i + 1 < argc) {
            pid = std::strtol(argv[++i], nullptr, 10);
            continue;
        }
        if (argument == "--dll" && i + 1 < argc) {
            dll_path = argv[++i];
            continue;
        }

        std::printf("[!] unknown argument: %s\n\n", argument.c_str());
        print_usage();
        return 1;
    }

    if (dll_path.empty()) {
        dll_path = exe_directory() + "\\" + kDllName;
    }
    dll_path = absolute_path(dll_path);

    if (list_only) {
        std::vector<ProcessInfo> processes = FindNostaleProcesses();
        if (tag_windows) {
            TagWindowsWithPid(processes);
        }
        print_processes(processes);
        return processes.empty() ? 2 : 0;
    }

    if (pid > 0) {
        ProcessInfo process;
        process.pid = static_cast<DWORD>(pid);
        process.exe_name = L"(requested by pid)";
        return inject_into(process, dll_path);
    }

    if (inject_all) {
        std::printf("DLL: %s\n\n", dll_path.c_str());
        std::vector<ProcessInfo> processes = FindNostaleProcesses();
        if (tag_windows) {
            TagWindowsWithPid(processes);
        }
        print_processes(processes);
        if (processes.empty()) {
            return 2;
        }

        int failures = 0;
        for (const ProcessInfo& process : processes) {
            failures += inject_into(process, dll_path);
        }
        return failures == 0 ? 0 : 1;
    }

    std::printf("DLL: %s\n\n", dll_path.c_str());
    return interactive(dll_path, tag_windows);
}
