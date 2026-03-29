using System;
using System.Collections.Concurrent;

namespace PacketLoggerGUI.Bot
{
    public class PlayerState
    {
        public int Id { get; set; }
        public int X { get; set; }
        public int Y { get; set; }
        public int Hp { get; set; }
        public int HpMax { get; set; }
        public int Mp { get; set; }
        public int MpMax { get; set; }
        public long Xp { get; set; }
        public int Speed { get; set; } = 10;

        public double HpPercent => HpMax > 0 ? (double)Hp / HpMax * 100 : 0;
        public double MpPercent => MpMax > 0 ? (double)Mp / MpMax * 100 : 0;
        public bool IsAlive => Hp > 0;
    }

    public class Entity
    {
        public int Id { get; set; }
        public int Type { get; set; } // 1=player, 2=npc/pet, 3=monster
        public int VNum { get; set; }
        public int X { get; set; }
        public int Y { get; set; }
        public int Speed { get; set; }
        public string Name { get; set; } = "";
        public int HpPercent { get; set; } = 100;
        public DateTime LastSeen { get; set; } = DateTime.Now;

        public bool IsMonster => Type == 3;
        public bool IsNpc => Type == 2;
        public bool IsPlayer => Type == 1;

        public double DistanceTo(int x, int y)
        {
            int dx = X - x;
            int dy = Y - y;
            return Math.Sqrt(dx * dx + dy * dy);
        }
    }

    public class GameState
    {
        public PlayerState Self { get; } = new PlayerState();
        public int MapId { get; set; }
        public ConcurrentDictionary<int, Entity> Entities { get; } = new();
        public bool IsConnected { get; set; }
        public DateTime LastPacketTime { get; set; }

        public event Action<string>? OnLog;

        public void ProcessPacket(string direction, string rawPacket)
        {
            LastPacketTime = DateTime.Now;

            var parsed = PacketParser.Parse(direction, rawPacket);
            if (parsed == null) return;

            switch (parsed)
            {
                case StatPacket stat:
                    Self.Hp = stat.Hp;
                    Self.HpMax = stat.HpMax;
                    Self.Mp = stat.Mp;
                    Self.MpMax = stat.MpMax;
                    Self.Xp = stat.Xp;
                    break;

                case WalkPacket walk:
                    Self.X = walk.X;
                    Self.Y = walk.Y;
                    break;

                case AtPacket at:
                    Self.Id = at.CharId;
                    Self.X = at.X;
                    Self.Y = at.Y;
                    MapId = at.MapId;
                    Entities.Clear();
                    Log($"Map loaded: {at.MapId} at ({at.X}, {at.Y})");
                    break;

                case CMapPacket cmap:
                    MapId = cmap.MapId;
                    Entities.Clear();
                    break;

                case MvPacket mv:
                    if (Entities.TryGetValue(mv.EntityId, out var movingEntity))
                    {
                        movingEntity.X = mv.X;
                        movingEntity.Y = mv.Y;
                        movingEntity.Speed = mv.Speed;
                        movingEntity.LastSeen = DateTime.Now;
                    }
                    break;

                case InPacket inp:
                    var entity = new Entity
                    {
                        Id = inp.EntityId,
                        Type = inp.EntityType,
                        VNum = inp.VNum,
                        X = inp.X,
                        Y = inp.Y,
                        Name = inp.Name,
                        HpPercent = inp.HpPercent
                    };
                    Entities[inp.EntityId] = entity;
                    break;

                case OutPacket outp:
                    Entities.TryRemove(outp.EntityId, out _);
                    break;

                case CondPacket cond:
                    if (cond.EntityId == Self.Id)
                        Self.Speed = cond.Speed;
                    else if (Entities.TryGetValue(cond.EntityId, out var condEntity))
                        condEntity.Speed = cond.Speed;
                    break;

                case SuPacket su:
                    if (Entities.TryGetValue(su.TargetId, out var target) && su.TargetHpPercent >= 0)
                        target.HpPercent = su.TargetHpPercent;
                    break;
            }
        }

        private void Log(string msg) => OnLog?.Invoke(msg);
    }
}
