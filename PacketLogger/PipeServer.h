#pragma once
#include <Windows.h>
#include <string>

namespace PipeServer
{
	bool Start();
	void Stop();
	bool IsConnected();
	void Send(const std::string& message);
	bool Receive(std::string& message);
}
