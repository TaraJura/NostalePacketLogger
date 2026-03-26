#pragma once
#include "Memory.h"
#include "SafeQueue.h"

namespace Packetlogger
{
	void Initialize(SafeQueue* recvQueue, SafeQueue* sendQueue);
	void SendPacket(LPCSTR szPacket);
	void HookRecv();
	void UnhookRecv();
	void HookSend();
	void UnhookSend();
	bool IsSendHookActive();
	DWORD GetSendAddy();
	DWORD GetRecvAddy();
}
