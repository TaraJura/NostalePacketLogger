#pragma once
#include <iostream>
#include <string>

namespace Console
{
	void showHeader();
	void logRecv(const std::string& packet);
	void logSend(const std::string& packet);
	void showStatus(const std::string& msg);
}
