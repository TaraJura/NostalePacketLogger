#pragma once

// Compile-time defaults. Everything can be overridden at runtime through
// environment variables, so one build serves several accounts, several game
// installs, and both Windows and Wine without a rebuild.

#include <cstdint>
#include <string>

namespace ntpl {

constexpr const char* kVersion = "0.2.3";

// The DLL listens on loopback only. A signed 16-bit port is plenty; the host
// connects with a retry loop, so nothing has to be started in a fixed order.
constexpr std::uint16_t kDefaultPort = 47821;

// Same path the Windows build uses, so the "tail -f" workflow keeps working:
//   Windows          C:\NosTalePacketLog.txt
//   Wine on Linux    <prefix>/drive_c/NosTalePacketLog.txt
constexpr const char* kDefaultLogPath = "C:\\NosTalePacketLog.txt";

std::uint16_t config_port();
std::string   config_log_path();
bool          config_quiet();

}  // namespace ntpl
