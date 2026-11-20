#pragma once

// Compile-time defaults. Everything can be overridden at runtime through
// environment variables, so one build serves several accounts, several game
// installs, and both Windows and Wine without a rebuild.

#include <cstdint>
#include <string>

namespace ntpl {

constexpr const char* kVersion = "0.2.8";

// The DLL listens on loopback only. A signed 16-bit port is plenty; the host
// connects with a retry loop, so nothing has to be started in a fixed order.
constexpr std::uint16_t kDefaultPort = 47821;

// One log per game process, so several clients in one prefix do not truncate
// and interleave each other's session:
//   Windows          C:\NosTalePacketLog-<pid>.txt
//   Wine on Linux    <prefix>/drive_c/NosTalePacketLog-<pid>.txt
// NTPL_LOG still overrides it for a single-client run.
constexpr const char* kDefaultLogPrefix = "C:\\NosTalePacketLog-";

std::uint16_t config_port();
std::string   config_log_path();
bool          config_quiet();

}  // namespace ntpl
