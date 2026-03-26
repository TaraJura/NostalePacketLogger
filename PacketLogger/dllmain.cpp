#include <Windows.h>
#include <iostream>
#include <string>
#include <sstream>
#include <iomanip>
#include "Packetlogger.h"
#include "PipeServer.h"

DWORD WINAPI MainThread(LPVOID param)
{
    SafeQueue qRecv;
    SafeQueue qSend;

    AllocConsole();
    FILE* file = nullptr;
    freopen_s(&file, "CONOUT$", "w", stdout);
    SetConsoleTitleA("NosTale Packet Logger [DLL]");

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

    // Debug: print found addresses
    std::cout << "[DEBUG] RecvHookAddy = 0x" << std::hex << Packetlogger::GetRecvAddy() << std::dec << "\n";
    std::cout << "[DEBUG] SendAddy     = 0x" << std::hex << Packetlogger::GetSendAddy() << std::dec << "\n";
    std::cout << "[DEBUG] SendHookReady = " << (Packetlogger::IsSendHookActive() ? "YES" : "NO") << "\n";

    // Print first 10 bytes at SendAddy for analysis
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
        // Check for commands from GUI
        std::string command;
        while (PipeServer::Receive(command))
        {
            if (command.substr(0, 5) == "SEND|")
            {
                std::string packet = command.substr(5);
                Packetlogger::SendPacket(packet.c_str());
                // Log the manual send to GUI as a SEND packet
                PipeServer::Send("SEND|" + packet);
                PipeServer::Send("STATUS|Sent: " + packet);
                std::cout << "[SENT] " << packet << "\n";
            }
            else if (command.substr(0, 4) == "QUIT")
            {
                goto cleanup;
            }
        }

        // Drain recv queue
        while (!qRecv.empty())
        {
            std::string packet = qRecv.front();
            PipeServer::Send("RECV|" + packet);
            qRecv.pop();
        }

        // Drain send queue (from hook)
        while (!qSend.empty())
        {
            std::string packet = qSend.front();
            PipeServer::Send("SEND|" + packet);
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
