#include "PipeServer.h"
#include <mutex>

#define PIPE_NAME "\\\\.\\pipe\\NosTalePacketLogger"
#define PIPE_BUFFER_SIZE 65536

static HANDLE hPipeSend = INVALID_HANDLE_VALUE;
static HANDLE hPipeRecv = INVALID_HANDLE_VALUE;
static std::mutex writeMutex;
static bool connected = false;

bool PipeServer::Start()
{
	// Pipe for DLL -> GUI (packets)
	hPipeSend = CreateNamedPipeA(
		PIPE_NAME "_packets",
		PIPE_ACCESS_OUTBOUND,
		PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
		1,
		PIPE_BUFFER_SIZE,
		0,
		0,
		NULL);

	if (hPipeSend == INVALID_HANDLE_VALUE)
		return false;

	// Pipe for GUI -> DLL (commands like send packet)
	hPipeRecv = CreateNamedPipeA(
		PIPE_NAME "_commands",
		PIPE_ACCESS_INBOUND,
		PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
		1,
		0,
		PIPE_BUFFER_SIZE,
		0,
		NULL);

	if (hPipeRecv == INVALID_HANDLE_VALUE)
	{
		CloseHandle(hPipeSend);
		hPipeSend = INVALID_HANDLE_VALUE;
		return false;
	}

	// Wait for GUI to connect to both pipes
	ConnectNamedPipe(hPipeSend, NULL);
	ConnectNamedPipe(hPipeRecv, NULL);

	connected = true;
	return true;
}

void PipeServer::Stop()
{
	connected = false;

	if (hPipeSend != INVALID_HANDLE_VALUE)
	{
		FlushFileBuffers(hPipeSend);
		DisconnectNamedPipe(hPipeSend);
		CloseHandle(hPipeSend);
		hPipeSend = INVALID_HANDLE_VALUE;
	}

	if (hPipeRecv != INVALID_HANDLE_VALUE)
	{
		DisconnectNamedPipe(hPipeRecv);
		CloseHandle(hPipeRecv);
		hPipeRecv = INVALID_HANDLE_VALUE;
	}
}

bool PipeServer::IsConnected()
{
	return connected;
}

void PipeServer::Send(const std::string& message)
{
	if (!connected || hPipeSend == INVALID_HANDLE_VALUE)
		return;

	std::lock_guard<std::mutex> lock(writeMutex);

	DWORD bytesWritten = 0;
	BOOL result = WriteFile(hPipeSend, message.c_str(), (DWORD)message.size(), &bytesWritten, NULL);

	if (!result)
		connected = false;
}

bool PipeServer::Receive(std::string& message)
{
	if (!connected || hPipeRecv == INVALID_HANDLE_VALUE)
		return false;

	char buffer[4096];
	DWORD bytesRead = 0;
	DWORD bytesAvail = 0;

	// Non-blocking peek
	if (!PeekNamedPipe(hPipeRecv, NULL, 0, NULL, &bytesAvail, NULL))
	{
		connected = false;
		return false;
	}

	if (bytesAvail == 0)
		return false;

	BOOL result = ReadFile(hPipeRecv, buffer, sizeof(buffer) - 1, &bytesRead, NULL);
	if (result && bytesRead > 0)
	{
		buffer[bytesRead] = '\0';
		message = std::string(buffer, bytesRead);
		return true;
	}

	connected = false;
	return false;
}
