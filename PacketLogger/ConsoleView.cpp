#include "ConsoleView.h"
#include <ctime>
#include <fstream>
#include <mutex>

static std::mutex logMutex;
static std::ofstream logFile;

static std::string getCurrentTime()
{
	time_t rawTime;
	struct tm timeInfo;
	char buffer[64];

	time(&rawTime);
	localtime_s(&timeInfo, &rawTime);
	strftime(buffer, sizeof(buffer), "%H:%M:%S", &timeInfo);
	return "[" + std::string(buffer) + "]";
}

void Console::showHeader()
{
	// Log to a fixed path so it's easy to tail from any terminal
	logFile.open("C:\\NosTalePacketLog.txt", std::ios::out | std::ios::trunc);

	std::string text;
	text += "======== NosTale Packet Logger ========\n\n";
	text += "[INFO] F1  => Toggle RECV logging\n";
	text += "[INFO] F2  => Toggle SEND logging\n";
	text += "[INFO] F3  => Send custom packet\n";
	text += "[INFO] F12 => EXIT\n\n";
	text += "================ Logs =================\n\n";
	std::cout << text;
}

void Console::logRecv(const std::string& packet)
{
	std::lock_guard<std::mutex> lock(logMutex);
	std::string line = getCurrentTime() + " [RECV] " + packet + "\n";
	std::cout << "\033[36m" << line << "\033[0m";
	if (logFile.is_open())
		logFile << line << std::flush;
}

void Console::logSend(const std::string& packet)
{
	std::lock_guard<std::mutex> lock(logMutex);
	std::string line = getCurrentTime() + " [SEND] " + packet + "\n";
	std::cout << "\033[33m" << line << "\033[0m";
	if (logFile.is_open())
		logFile << line << std::flush;
}

void Console::showStatus(const std::string& msg)
{
	std::lock_guard<std::mutex> lock(logMutex);
	std::cout << getCurrentTime() << " [STATUS] " << msg << "\n";
}
