using System;

namespace PacketLoggerGUI.Bot
{
    public class PacketSender
    {
        private readonly PipeClient _pipe;

        public PacketSender(PipeClient pipe) { _pipe = pipe; }
        public bool IsReady => _pipe.IsConnected;

        public void Send(string packet)
        {
            if (!IsReady) return;
            _pipe.SendPacket(packet);
        }

        // === Movement ===
        public void Walk(int x, int y, int speed = 10) => Send($"walk {x} {y} 0 {speed}");
        public void ChangeDirection(int entityType, int entityId, int dir) => Send($"dir {entityType} {entityId} {dir}");
        public void Rest(int entityType, int entityId, bool sit) => Send($"rest {(sit ? 1 : 0)} {entityType} {entityId}");
        public void EnterPortal() => Send("preq");
        public void Pulse(int time, int flag = 0) => Send($"pulse {time} {flag}");

        // === Combat ===
        public void SelectTarget(int entityType, int entityId) => Send($"ncif {entityType} {entityId}");
        public void UseSkill(int castId, int targetType, int targetId) => Send($"u_s {castId} {targetType} {targetId}");
        public void BasicAttack(int targetType, int targetId) => UseSkill(0, targetType, targetId);
        public void SelfBuff(int castId) => UseSkill(castId, 1, 0); // target self

        // === Loot ===
        public void PickUp(int entityId) => Send($"get 1 0 {entityId}");

        // === Items ===
        public void UseItem(int invTab, int playerId, int slot) => Send($"u_i {invTab} {playerId} {invTab} {slot} 0 0");
        public void UseItemMain(int playerId, int slot) => UseItem(1, playerId, slot);
        public void UseItemEtc(int playerId, int slot) => UseItem(2, playerId, slot);

        // === NPC ===
        public void TalkToNpc(int entityType, int entityId) => Send($"npc_req {entityType} {entityId}");
        public void NpcAction(int actionId) => Send($"n_run {actionId}");
        public void NpcAction(int actionId, int subId, int entityType, int entityId) => Send($"n_run {actionId} {subId} {entityType} {entityId}");
        public void CloseShop() => Send("shop_end 1");

        // === Chat ===
        public void Say(string message) => Send($"say {message}");

        // === Revival ===
        public void Revive(int type = 8) => Send($"#revival^{type}");

        // === Special ===
        public void Guri(int type, int subType, int playerId, string extra = "") =>
            Send(string.IsNullOrEmpty(extra) ? $"guri {type} {subType} {playerId}" : $"guri {type} {subType} {playerId} {extra}");
    }
}
