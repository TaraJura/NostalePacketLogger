#include <Windows.h>
#include <iostream>
#include <fstream>
#include <string>
#include <sstream>
#include <iomanip>
#include <chrono>
#include "Packetlogger.h"
#include "PipeServer.h"

// Prevent closing the debug console from killing the game process
BOOL WINAPI ConsoleCtrlHandler(DWORD ctrlType)
{
    if (ctrlType == CTRL_CLOSE_EVENT || ctrlType == CTRL_C_EVENT)
        return TRUE; // Block the close — don't terminate the game
    return FALSE;
}

std::ofstream g_logFile;

void LogToFile(const std::string& type, const std::string& packet)
{
    if (!g_logFile.is_open()) return;

    auto now = std::chrono::system_clock::now();
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()) % 1000;
    auto time = std::chrono::system_clock::to_time_t(now);
    struct tm t;
    localtime_s(&t, &time);

    char timeBuf[32];
    sprintf_s(timeBuf, "%02d:%02d:%02d.%03d",
        t.tm_hour, t.tm_min, t.tm_sec, (int)ms.count());

    g_logFile << timeBuf << " [" << type << "] " << packet << std::endl;
}

DWORD WINAPI MainThread(LPVOID param)
{
    SafeQueue qRecv;
    SafeQueue qSend;

    AllocConsole();
    SetConsoleCtrlHandler(ConsoleCtrlHandler, TRUE);
    FILE* file = nullptr;
    freopen_s(&file, "CONOUT$", "w", stdout);
    SetConsoleTitleA("NosTale Packet Logger [DLL] - DO NOT CLOSE (use GUI to disconnect)");

    // Open live log file at C:\NosTalePacketLog.txt for easy access
    g_logFile.open("C:\\NosTalePacketLog.txt", std::ios::out | std::ios::trunc);
    std::cout << "[*] Live log: C:\\NosTalePacketLog.txt\n";

    std::cout << "[*] Waiting for GUI to connect...\n";

    if (!PipeServer::Start())
    {
        std::cout << "[!] Failed to create pipes.\n";
        Sleep(3000);
        FreeConsole();
        FreeLibrary((HMODULE)param);
        return 1;
    }

    std::cout << "[+] GUI connected!\n";
    std::cout << "[*] Initializing hooks...\n";

    Packetlogger::Initialize(&qRecv, &qSend);

    std::cout << "[DEBUG] RecvHookAddy = 0x" << std::hex << Packetlogger::GetRecvAddy() << std::dec << "\n";
    std::cout << "[DEBUG] SendAddy     = 0x" << std::hex << Packetlogger::GetSendAddy() << std::dec << "\n";
    std::cout << "[DEBUG] SendHookReady = " << (Packetlogger::IsSendHookActive() ? "YES" : "NO") << "\n";

    if (Packetlogger::GetSendAddy() != 0)
    {
        std::cout << "[DEBUG] Bytes at SendAddy: ";
        BYTE* ptr = (BYTE*)Packetlogger::GetSendAddy();
        for (int i = 0; i < 10; i++)
            std::cout << std::hex << std::setw(2) << std::setfill('0') << (int)ptr[i] << " ";
        std::cout << std::dec << "\n";
    }

    Packetlogger::HookRecv();
    Packetlogger::HookSend();

    std::cout << "[+] Recv hook: ACTIVE\n";
    std::cout << "[+] Send hook: " << (Packetlogger::IsSendHookActive() ? "ACTIVE" : "FAILED") << "\n";

    std::string statusMsg = "Recv hook: ACTIVE | Send hook: ";
    statusMsg += Packetlogger::IsSendHookActive() ? "ACTIVE" : "FAILED (send logging unavailable)";
    PipeServer::Send("STATUS|" + statusMsg);

    while (PipeServer::IsConnected())
    {
        std::string command;
        while (PipeServer::Receive(command))
        {
            if (command.substr(0, 5) == "SEND|")
            {
                std::string packet = command.substr(5);
                Packetlogger::SendPacket(packet.c_str());
                PipeServer::Send("SEND|" + packet);
                PipeServer::Send("STATUS|Sent: " + packet);
                std::cout << "[SENT] " << packet << "\n";
                LogToFile("MANUAL_SEND", packet);
            }
            else if (command.substr(0, 4) == "QUIT")
            {
                goto cleanup;
            }
        }

        while (!qRecv.empty())
        {
            std::string packet = qRecv.front();
            PipeServer::Send("RECV|" + packet);
            LogToFile("RECV", packet);
            qRecv.pop();
        }

        while (!qSend.empty())
        {
            std::string packet = qSend.front();
            PipeServer::Send("SEND|" + packet);
            LogToFile("SEND", packet);
            qSend.pop();
        }

        Sleep(5);
    }

cleanup:
    std::cout << "[*] Cleaning up...\n";
    PipeServer::Send("STATUS|Disconnecting...");

    Packetlogger::UnhookSend();
    Packetlogger::UnhookRecv();
    PipeServer::Stop();

    if (g_logFile.is_open())
        g_logFile.close();

    if (file != nullptr)
        fclose(file);

    FreeConsole();
    FreeLibrary((HMODULE)param);
    return 0;
}

BOOL APIENTRY DllMain(HMODULE hModule, DWORD ul_reason_for_call, LPVOID lpReserved)
{
    switch (ul_reason_for_call)
    {
    case DLL_PROCESS_ATTACH:
        CreateThread(0, 0, MainThread, hModule, 0, 0);
        break;
    case DLL_THREAD_ATTACH:
    case DLL_THREAD_DETACH:
    case DLL_PROCESS_DETACH:
        break;
    }
    return TRUE;
}
