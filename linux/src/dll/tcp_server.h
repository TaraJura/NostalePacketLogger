#pragma once

#include <cstdint>
#include <string>

namespace tcpserver {

// Binds to 127.0.0.1:<port> and starts listening. Returns false when the port
// is already in use or Winsock could not be initialised.
bool start(std::uint16_t port);
void stop();

// Blocks until a host connects. Returns false on socket error.
bool accept_client();
bool is_connected();
void disconnect();

// Non-blocking. Returns one complete line (without CR/LF) at a time.
bool poll_command(std::string& out);

// Adds the trailing newline. On a dead peer the connection is closed and the
// call returns false.
bool send_line(const std::string& line);

}  // namespace tcpserver
