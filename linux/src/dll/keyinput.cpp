#include "keyinput.h"

#include <windows.h>

#include <cstring>
#include <string>

#include "key_names.h"
#include "log.h"

namespace {

constexpr const char* kMainClass = "TNosTaleMainF";

struct WindowSearch {
    HWND main = nullptr;
    LONG main_area = 0;
    HWND fallback = nullptr;
    LONG fallback_area = 0;
};

BOOL CALLBACK find_main_proc(HWND window, LPARAM parameter)
{
    auto* search = reinterpret_cast<WindowSearch*>(parameter);
    DWORD pid = 0;
    GetWindowThreadProcessId(window, &pid);
    if (pid != GetCurrentProcessId() || !IsWindowVisible(window)) {
        return TRUE;
    }

    char title[256] = {0};
    GetWindowTextA(window, title, sizeof(title));
    if (title[0] == '\0') {
        return TRUE;
    }

    RECT rect = {0, 0, 0, 0};
    GetClientRect(window, &rect);
    const LONG area = (rect.right - rect.left) * (rect.bottom - rect.top);

    char class_name[128] = {0};
    GetClassNameA(window, class_name, sizeof(class_name));
    if (_stricmp(class_name, kMainClass) == 0) {
        if (search->main == nullptr || area > search->main_area) {
            search->main = window;
            search->main_area = area;
        }
    } else if (_strnicmp(title, "NosTale", 7) == 0 &&
               (search->fallback == nullptr ||
                area > search->fallback_area)) {
        // The class name is stable on the tested build; keep a safe fallback
        // for a patched client that registers a different form class. Only a
        // NosTale-titled window can be the game (not a CEF shop helper).
        search->fallback = window;
        search->fallback_area = area;
    }
    return TRUE;
}

bool window_usable(HWND window)
{
    if (window == nullptr || !IsWindow(window)) {
        return false;
    }
    DWORD pid = 0;
    GetWindowThreadProcessId(window, &pid);
    if (pid != GetCurrentProcessId()) {
        return false;
    }
    char class_name[128] = {0};
    GetClassNameA(window, class_name, sizeof(class_name));
    return _stricmp(class_name, kMainClass) == 0;
}

HWND main_window()
{
    static HWND cached = nullptr;
    if (window_usable(cached)) {
        return cached;
    }
    WindowSearch search;
    EnumWindows(find_main_proc, reinterpret_cast<LPARAM>(&search));
    cached = search.main != nullptr ? search.main : search.fallback;
    return cached;
}

unsigned int scan_code(unsigned int vk)
{
    if (vk == 0) {
        return 0;
    }
    const UINT scan = MapVirtualKeyA(vk, MAPVK_VK_TO_VSC);
    return static_cast<unsigned int>(scan & 0xFFU);
}

// One queued press. The worker thread replaces the job if a new key arrives
// while the previous one is held; the GUI's minimum interval (40 ms) is longer
// than the 25 ms hold, so this is only a guard against a hand-written client.
struct KeyJob {
    HWND window = nullptr;
    ntpl::KeyCode code = {};
};

CRITICAL_SECTION g_lock;
bool g_lock_ready = false;
bool g_thread_ready = false;
HANDLE g_event = nullptr;
KeyJob g_job;
bool g_pending = false;

bool game_is_foreground(HWND window)
{
    const HWND foreground = GetForegroundWindow();
    if (foreground == nullptr) {
        return false;
    }
    return foreground == window ||
           GetAncestor(foreground, GA_ROOT) == window;
}

bool send_input(unsigned int vk, unsigned int scan, bool up, bool extended)
{
    INPUT input = {};
    input.type = INPUT_KEYBOARD;
    input.ki.wVk = static_cast<WORD>(vk);
    input.ki.wScan = static_cast<WORD>(scan);
    input.ki.dwFlags = static_cast<DWORD>(
        (up ? KEYEVENTF_KEYUP : 0) |
        (extended ? KEYEVENTF_EXTENDEDKEY : 0));
    return SendInput(1, &input, sizeof(INPUT)) == 1;
}

void press(const KeyJob& job)
{
    if (job.window == nullptr || !IsWindow(job.window)) {
        return;
    }
    const unsigned int scan =
        job.code.scan != 0 ? job.code.scan : scan_code(job.code.vk);
    const LPARAM down = static_cast<LPARAM>(1) |
                        (static_cast<LPARAM>(scan) << 16) |
                        (job.code.extended ? (static_cast<LPARAM>(1) << 24)
                                           : 0);
    const LPARAM up = down | static_cast<LPARAM>(0xC0000000UL);

    // Real input when the game owns the foreground: SendInput updates the
    // thread keyboard state (GetKeyboardState/GetKeyState) as a physical key
    // would, which a posted message cannot do. When the window is in the
    // background the press is posted to its queue instead, so the GUI can be
    // focused without losing the spam.
    static bool logged_mode = false;
    const bool foreground = game_is_foreground(job.window);
    bool sent = false;
    if (foreground) {
        sent = send_input(job.code.vk, scan, false, job.code.extended);
    }
    if (!sent) {
        sent = PostMessageA(job.window, WM_KEYDOWN, job.code.vk, down) != 0;
    }
    if (!logged_mode) {
        ntpl::logf("KEY first press via %s",
                   foreground ? "SendInput" : "PostMessage");
        logged_mode = true;
    }
    if (!sent) {
        ntpl::logf("KEY down failed: %lu",
                   static_cast<unsigned long>(GetLastError()));
    }

    // The game reads key-down in its form's message handler. Holding the key
    // for a moment also lets a frame-based input poll observe it; the key
    // thread is separate from the packet worker, so this sleep never delays a
    // packet or a TARGET command.
    Sleep(25);
    bool released = false;
    if (foreground) {
        released = send_input(job.code.vk, scan, true, job.code.extended);
    }
    // Always queue the key-up for the game window too. If focus moved in the
    // 25 ms hold, the real key-up went elsewhere; this clears the game's own
    // message-based key state instead of leaving the key stuck. A second
    // key-up is harmless to the game's input handler.
    if (!PostMessageA(job.window, WM_KEYUP, job.code.vk, up) && !released) {
        ntpl::logf("KEY up failed: %lu",
                   static_cast<unsigned long>(GetLastError()));
    }
}

DWORD WINAPI key_thread(LPVOID module)
{
    (void)module;
    for (;;) {
        WaitForSingleObject(g_event, INFINITE);
        KeyJob job;
        bool pending = false;
        EnterCriticalSection(&g_lock);
        if (g_pending) {
            job = g_job;
            g_pending = false;
            pending = true;
        }
        LeaveCriticalSection(&g_lock);
        if (pending) {
            press(job);
        }
    }
    return 0;
}

bool ensure_thread()
{
    if (g_thread_ready) {
        return true;
    }
    if (!g_lock_ready) {
        InitializeCriticalSection(&g_lock);
        g_lock_ready = true;
    }
    if (g_event == nullptr) {
        g_event = CreateEventA(nullptr, FALSE, FALSE, nullptr);
    }
    if (g_event == nullptr) {
        return false;
    }
    const HANDLE thread =
        CreateThread(nullptr, 0, key_thread, nullptr, 0, nullptr);
    if (thread == nullptr) {
        return false;
    }
    CloseHandle(thread);
    g_thread_ready = true;
    return true;
}

}  // namespace

bool client_key(const std::string& name, std::string& error)
{
    ntpl::KeyCode code = {};
    if (!ntpl::parse_key_name(name, code, error)) {
        return false;
    }
    const HWND window = main_window();
    if (window == nullptr) {
        error = "game window not found";
        return false;
    }
    if (!ensure_thread()) {
        error = "key thread unavailable";
        return false;
    }

    EnterCriticalSection(&g_lock);
    g_job.window = window;
    g_job.code = code;
    g_pending = true;
    LeaveCriticalSection(&g_lock);
    SetEvent(g_event);
    return true;
}
