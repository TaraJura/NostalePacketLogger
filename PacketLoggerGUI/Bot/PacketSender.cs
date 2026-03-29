using System;

namespace PacketLoggerGUI.Bot
{
    public class PacketSender
    {
        private readonly PipeClient _pipe;

        public PacketSender(PipeClient pipe)
        {
            _pipe = pipe;
        }

        public bool IsReady => _pipe.IsConnected;

        public void Send(string packet)
        {
            if (!IsReady) return;
            _pipe.SendPacket(packet);
        }

        public void Walk(int x, int y, int speed = 10)
        {
            Send($"walk {x} {y} 0 {speed}");
        }

        public void SelectTarget(int entityType, int entityId)
        {
            Send($"ncif {entityType} {entityId}");
        }

        public void UseSkill(int skillSlot, int targetType, int targetId)
        {
            Send($"u_s 0 {targetType} {targetId} {skillSlot}");
        }

        public void BasicAttack(int targetType, int targetId)
        {
            UseSkill(0, targetType, targetId);
        }

        public void PickUp(int entityId)
        {
            Send($"get 1 0 {entityId}");
        }

        public void Say(string message)
        {
            Send($"say {message}");
        }

        public void TalkToNpc(int entityType, int entityId)
        {
            Send($"npc_req {entityType} {entityId}");
        }
    }
}
