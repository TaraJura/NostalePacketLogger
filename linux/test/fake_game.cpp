// A stand-in for NostaleClientX.exe.
//
// Two modes:
//
//   --no-load   just stay alive, so the real Injector.exe has something to
//               inject into. This is how dev-test-wine.sh exercises the whole
//               injection path under Wine.
//   (default)   LoadLibraryA the DLL directly, for a quick manual test.
//
// The DLL's pattern scan will not find the game signatures in this process, so
// it reports "NOT-FOUND" and stays alive with the TCP server running. That
// makes the transport and the injector testable without the game installed.
//
// It is a test fixture, not part of the shipped tool.

#include <windows.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>

int main(int argc, char** argv) {
    const char* dll = "PacketLogger.dll";
    int seconds = 10;
    bool load_dll = true;

    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--dll") == 0 && i + 1 < argc) {
            dll = argv[++i];
        } else if (std::strcmp(argv[i], "--seconds") == 0 && i + 1 < argc) {
            seconds = std::atoi(argv[++i]);
        } else if (std::strcmp(argv[i], "--no-load") == 0) {
            load_dll = false;
        }
    }

    if (load_dll) {
        std::printf("FakeGame: loading %s\n", dll);
        std::fflush(stdout);

        HMODULE module = LoadLibraryA(dll);
        if (module == nullptr) {
            std::printf("FakeGame: LoadLibraryA failed: %lu\n",
                        static_cast<unsigned long>(GetLastError()));
            return 1;
        }
        std::printf("FakeGame: loaded\n");
    } else {
        std::printf("FakeGame: --no-load, waiting to be injected\n");
    }

    std::printf("FakeGame: sleeping %d s\n", seconds);
    std::fflush(stdout);
    Sleep(static_cast<DWORD>(seconds) * 1000);
    std::printf("FakeGame: exiting\n");
    return 0;
}
