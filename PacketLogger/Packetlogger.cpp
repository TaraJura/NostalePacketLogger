#include "Packetlogger.h"
#include "NostaleString.h"
#include <iostream>

#define HOOK_SIZE 5
#define SEND_HOOK_SIZE 6

SafeQueue* qRecv;
SafeQueue* qSend;
DWORD SendAddy;
DWORD RecvHookAddy;
DWORD TNTClient;

// Recv hook state
DWORD originalRecvCallAddy;
DWORD jmpBackRecvAddy;
BYTE originalRecvBytes[10]{ 0 };
char* recvPacket;

// Send hook state
BYTE originalSendBytes[10]{ 0 };
BYTE* sendTrampoline = nullptr;
char* sendPacket;
bool sendHookReady = false;

void __declspec(naked) CustomRecv()
{
	__asm
	{
		pushad;
		pushfd;
		mov recvPacket, edx;
	}

	qRecv->push(recvPacket);

	__asm
	{
		popfd;
		popad;
		call originalRecvCallAddy;
		jmp jmpBackRecvAddy;
	}
}

void __declspec(naked) CustomSend()
{
	__asm
	{
		pushad;
		pushfd;
		mov sendPacket, edx;
	}

	qSend->push(sendPacket);

	__asm
	{
		popfd;
		popad;
		jmp sendTrampoline;
	}
}

void Packetlogger::Initialize(SafeQueue* recvQueue, SafeQueue* sendQueue)
{
	RecvHookAddy = Memory::FindPattern(
		(char*)"\xe8\x00\x00\x00\x00\x33\xc0\x55\x68\x00\x00\x00\x00\x64\xff\x00\x64\x89\x00\x8d\x45\x00\x8b\x55",
		(char*)"x????xxxx????xx?xx?xx?xx");

	TNTClient = Memory::FindPattern(
		(char*)"\xA1\x00\x00\x00\x00\x8B\x00\xE8\x00\x00\x00\x00\xA1\x00\x00\x00\x00\x8B\x00\x33\xD2\x89\x10",
		(char*)"x????xxx????x????xxxxxx") + 1;

	SendAddy = Memory::FindPattern((char*)"\xeb\x00\xeb\x00\x39\x19\x8b\xd6", (char*)"x?x?xxxx") - 6;

	// Setup recv hook
	qRecv = recvQueue;
	jmpBackRecvAddy = RecvHookAddy + HOOK_SIZE;
	DWORD recvCallArg = *(DWORD*)(RecvHookAddy + 1);
	originalRecvCallAddy = RecvHookAddy + recvCallArg + HOOK_SIZE;
	memcpy_s(originalRecvBytes, HOOK_SIZE, (LPVOID)RecvHookAddy, HOOK_SIZE);

	// Setup send hook - trampoline approach
	// Allocate executable memory for the trampoline
	qSend = sendQueue;

	if (SendAddy != 0)
	{
		// Save original bytes (6 bytes to land on instruction boundary)
		// SendAddy bytes: 53 56 8B F2 8B D8 = push ebx, push esi, mov esi edx, mov ebx eax
		//                 1  + 1 + 2  + 2   = 6 bytes (clean boundary)
		memcpy_s(originalSendBytes, SEND_HOOK_SIZE, (LPVOID)SendAddy, SEND_HOOK_SIZE);

		// Create trampoline: original 6 bytes + JMP back to SendAddy+6
		sendTrampoline = (BYTE*)VirtualAlloc(NULL, 32, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
		if (sendTrampoline)
		{
			memcpy(sendTrampoline, (void*)SendAddy, SEND_HOOK_SIZE);

			sendTrampoline[SEND_HOOK_SIZE] = 0xE9; // JMP rel32
			DWORD jmpTarget = (SendAddy + SEND_HOOK_SIZE) - ((DWORD)(sendTrampoline + SEND_HOOK_SIZE) + 5);
			*(DWORD*)(sendTrampoline + SEND_HOOK_SIZE + 1) = jmpTarget;

			sendHookReady = true;
		}
	}
}

void Packetlogger::SendPacket(LPCSTR szPacket)
{
	NostaleStringA str(szPacket);
	char* packet = str.get();

	__asm
	{
		mov eax, dword ptr ds : [TNTClient];
		mov eax, dword ptr ds : [eax];
		mov eax, dword ptr ds : [eax];
		mov eax, dword ptr ds : [eax];
		mov edx, packet;
		call SendAddy;
	}
}

void Packetlogger::HookRecv()
{
	Memory::Hook((LPVOID)RecvHookAddy, CustomRecv, HOOK_SIZE);
}

void Packetlogger::UnhookRecv()
{
	Memory::Patch((BYTE*)RecvHookAddy, originalRecvBytes, HOOK_SIZE);
}

void Packetlogger::HookSend()
{
	if (sendHookReady)
		Memory::Hook((LPVOID)SendAddy, CustomSend, SEND_HOOK_SIZE);
}

void Packetlogger::UnhookSend()
{
	if (sendHookReady)
	{
		Memory::Patch((BYTE*)SendAddy, originalSendBytes, SEND_HOOK_SIZE);
		VirtualFree(sendTrampoline, 0, MEM_RELEASE);
		sendTrampoline = nullptr;
		sendHookReady = false;
	}
}

bool Packetlogger::IsSendHookActive()
{
	return sendHookReady;
}

DWORD Packetlogger::GetSendAddy()
{
	return SendAddy;
}

DWORD Packetlogger::GetRecvAddy()
{
	return RecvHookAddy;
}
