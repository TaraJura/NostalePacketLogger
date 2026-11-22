#pragma once

#include <string>

// Queue one key press to the game's own main window (`TNosTaleMainF`).
//
// The press is asynchronous: the TCP worker thread only queues it and returns,
// while a small DLL thread posts WM_KEYDOWN, waits 25 ms and posts WM_KEYUP to
// the game's VCL form. That is the same path a physical key takes through the
// game's message loop, so the sheep-raid action sees a normal key press.
//
// False means the name was not understood or no game window was found. The
// caller turns that into `STATUS|KEY ERR <reason>`; a successful queue is
// reported once as `STATUS|KEY OK <key>`.
bool client_key(const std::string& name, std::string& error);
