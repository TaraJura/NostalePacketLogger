#pragma once

namespace ntpl {

// Opens the packet log. Falls back to %LOCALAPPDATA%\NosTalePacketLogger\dll.log
// and then to %TEMP% when the preferred path is not writable. Safe to call once
// from the worker thread; every other log call is a no-op until it succeeds.
void log_open();
void log_close();

// printf-style status line. Thread safe.
void logf(const char* fmt, ...);

// One packet line in the historic format:
//   10:19:19.123 [RECV] walk 19 146 0 10
// `direction` is "RECV" or "SEND".
void log_packet(const char* direction, const char* packet);

}  // namespace ntpl
