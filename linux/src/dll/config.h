#pragma once

// Compile-time defaults. Everything can be overridden at runtime through
// environment variables, so one build serves several accounts, several game
// installs, and both Windows and Wine without a rebuild.

#include <cstdint>
#include <string>

namespace ntpl {

// 0.3.3: client build 0.9.3.3262 TARGET selector re-derived (0x005699D0,
// controller at [0x007944F0]); the bot can select monsters again. 0.3.2:
// Speed + keeps the requested value through a lone self `tp`; only a burst
// of position corrections settles it. 0.3.1: client build 0.9.3.3262 player
// global/MoveTo/speed table addresses; 0.3.0 was the 0.9.3.3261 build.
constexpr const char* kVersion = "0.3.3";

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
