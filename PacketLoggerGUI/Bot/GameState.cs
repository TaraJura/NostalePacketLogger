using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;

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
        public long XpMax { get; set; }
        public int Level { get; set; }
        public int JobLevel { get; set; }
        public long JobXp { get; set; }
        public long JobXpMax { get; set; }
        public int Speed { get; set; } = 10;
        public int Direction { get; set; }
        public bool IsResting { get; set; }
        public bool IsDead { get; set; }

        public double HpPercent => HpMax > 0 ? (double)Hp / HpMax * 100 : 0;
        public double MpPercent => MpMax > 0 ? (double)Mp / MpMax * 100 : 0;
        public bool IsAlive => !IsDead && Hp > 0;
    }

    public class Entity
    {
        public int Id { get; set; }
        public int Type { get; set; } // 1=player, 2=npc/pet, 3=monster
        public int VNum { get; set; }
        public int X { get; set; }
        public int Y { get; set; }
        public int Speed { get; set; }
        public int Direction { get; set; }
        public string Name { get; set; } = "";
        public int HpPercent { get; set; } = 100;
        public int MpPercent { get; set; } = 100;
        public bool IsDead { get; set; }
        public DateTime LastSeen { get; set; } = DateTime.Now;

        public bool IsMonster => Type == 3;
        public bool IsNpc => Type == 2;
        public bool IsPlayer => Type == 1;
        public bool IsAlive => !IsDead && HpPercent > 0;

        public double DistanceTo(int x, int y)
        {
            int dx = X - x; int dy = Y - y;
            return Math.Sqrt(dx * dx + dy * dy);
        }
    }

    public class SkillState
    {
        public int SlotId { get; set; }
        public int VNum { get; set; }
        public bool IsReady { get; set; } = true;
        public DateTime LastUsed { get; set; }
        public int CooldownMs { get; set; }
    }

    public class Portal
    {
        public int X { get; set; }
        public int Y { get; set; }
        public int DestMapId { get; set; }
        public int PortalType { get; set; }
        public int PortalId { get; set; }
    }

    public class GroundItem
    {
        public int EntityId { get; set; }
        public int VNum { get; set; }
        public int X { get; set; }
        public int Y { get; set; }
        public int OwnerId { get; set; }
        public string Name { get; set; } = "";
    }

    public class ChatMessage
    {
        public DateTime Time { get; set; } = DateTime.Now;
        public int EntityType { get; set; }
        public int EntityId { get; set; }
        public string Sender { get; set; } = "";
        public string Text { get; set; } = "";
    }

    public class GameState
    {
        public PlayerState Self { get; } = new PlayerState();
        public int MapId { get; set; }
        public ConcurrentDictionary<int, Entity> Entities { get; } = new();
        public ConcurrentDictionary<int, SkillState> Skills { get; } = new();
        public ConcurrentDictionary<int, Portal> Portals { get; } = new();
        public ConcurrentDictionary<int, GroundItem> GroundItems { get; } = new();
        public List<ChatMessage> ChatLog { get; } = new();

        public bool IsConnected { get; set; }
        public DateTime LastPacketTime { get; set; }
        public bool ShowDeathDialog { get; set; }

        public event Action<string>? OnLog;
        public event Action<SuPacket>? OnCombatHit;
        public event Action<DiePacket>? OnEntityDied;
        public event Action<ChatMessage>? OnChat;

        // Derived queries
        public IEnumerable<Entity> Monsters => Entities.Values.Where(e => e.IsMonster && e.IsAlive);
        public IEnumerable<Entity> Players => Entities.Values.Where(e => e.IsPlayer);
        public IEnumerable<Entity> Npcs => Entities.Values.Where(e => e.IsNpc);
        public Entity? NearestMonster => Monsters
            .OrderBy(e => e.DistanceTo(Self.X, Self.Y))
            .FirstOrDefault();

        public void ProcessPacket(string direction, string rawPacket)
        {
            LastPacketTime = DateTime.Now;
            var parsed = PacketParser.Parse(direction, rawPacket);
            if (parsed == null) return;

            switch (parsed)
            {
                // === Stats ===
                case StatPacket stat:
                    Self.Hp = stat.Hp; Self.HpMax = stat.HpMax;
                    Self.Mp = stat.Mp; Self.MpMax = stat.MpMax;
                    Self.Xp = stat.Xp;
                    if (Self.IsDead && stat.Hp > 0) Self.IsDead = false;
                    break;

                case LevPacket lev:
                    Self.Level = lev.Level; Self.JobLevel = lev.JobLevel;
                    Self.Xp = lev.Xp; Self.XpMax = lev.XpMax;
                    Self.JobXp = lev.JobXp; Self.JobXpMax = lev.JobXpMax;
                    break;

                // === Movement ===
                case WalkPacket walk:
                    Self.X = walk.X; Self.Y = walk.Y;
                    Self.IsResting = false;
                    break;

                case AtPacket at:
                    Self.Id = at.CharId; Self.X = at.X; Self.Y = at.Y; Self.Direction = at.Dir;
                    MapId = at.MapId;
                    Entities.Clear(); Portals.Clear(); GroundItems.Clear();
                    Self.IsDead = false; ShowDeathDialog = false;
                    Log($"Map {at.MapId} at ({at.X}, {at.Y})");
                    break;

                case CMapPacket cmap:
                    MapId = cmap.MapId;
                    Entities.Clear(); Portals.Clear(); GroundItems.Clear();
                    break;

                case MvPacket mv:
                    if (Entities.TryGetValue(mv.EntityId, out var movEnt))
                    {
                        movEnt.X = mv.X; movEnt.Y = mv.Y; movEnt.Speed = mv.Speed;
                        movEnt.LastSeen = DateTime.Now;
                    }
                    break;

                case TpPacket tp:
                    if (Entities.TryGetValue(tp.EntityId, out var tpEnt))
                    {
                        tpEnt.X = tp.X; tpEnt.Y = tp.Y;
                        tpEnt.LastSeen = DateTime.Now;
                    }
                    break;

                case RestPacket rest:
                    if (rest.EntityId == Self.Id)
                        Self.IsResting = rest.Toggle == 1;
                    break;

                // === Entities ===
                case InPacket inp:
                    var entity = new Entity
                    {
                        Id = inp.EntityId, Type = inp.EntityType, VNum = inp.VNum,
                        X = inp.X, Y = inp.Y, Direction = inp.Dir,
                        Name = inp.Name, HpPercent = inp.HpPercent, MpPercent = inp.MpPercent
                    };
                    Entities[inp.EntityId] = entity;
                    break;

                case OutPacket outp:
                    Entities.TryRemove(outp.EntityId, out _);
                    break;

                case CondPacket cond:
                    if (cond.EntityId == Self.Id)
                        Self.Speed = cond.Speed;
                    else if (Entities.TryGetValue(cond.EntityId, out var condEnt))
                        condEnt.Speed = cond.Speed;
                    break;

                // === Combat ===
                case SuPacket su:
                    if (Entities.TryGetValue(su.TargetId, out var target))
                    {
                        target.HpPercent = su.HpPercent;
                        if (!su.TargetIsAlive) target.IsDead = true;
                    }
                    // Track cooldown for our skills
                    if (su.AttackerId == Self.Id && su.Cooldown > 0)
                    {
                        var skill = Skills.GetOrAdd(su.SkillVNum, _ => new SkillState { VNum = su.SkillVNum });
                        skill.IsReady = false;
                        skill.LastUsed = DateTime.Now;
                        skill.CooldownMs = su.Cooldown * 100; // cooldown is in tenths of seconds
                    }
                    OnCombatHit?.Invoke(su);
                    break;

                case SrPacket sr:
                    // Skill cooldown reset by slot ID
                    foreach (var s in Skills.Values.Where(s => s.SlotId == sr.SkillSlotId))
                        s.IsReady = true;
                    break;

                case EffPacket eff:
                    // Effect 8 = all cooldowns reset
                    if (eff.EffectId == 8 && eff.EntityId == Self.Id)
                        foreach (var s in Skills.Values) s.IsReady = true;
                    break;

                case DiePacket die:
                    if (die.EntityId == Self.Id)
                    {
                        Self.IsDead = true;
                        Log("Character died!");
                    }
                    else if (Entities.TryGetValue(die.EntityId, out var deadEnt))
                    {
                        deadEnt.IsDead = true;
                        deadEnt.HpPercent = 0;
                    }
                    OnEntityDied?.Invoke(die);
                    break;

                // === Chat ===
                case SayRecvPacket say:
                    var chatMsg = new ChatMessage { EntityType = say.EntityType, EntityId = say.EntityId, Text = say.Message };
                    if (Entities.TryGetValue(say.EntityId, out var speaker)) chatMsg.Sender = speaker.Name;
                    lock (ChatLog) { ChatLog.Add(chatMsg); if (ChatLog.Count > 500) ChatLog.RemoveAt(0); }
                    OnChat?.Invoke(chatMsg);
                    break;

                // === Map features ===
                case GpPacket gp:
                    Portals[gp.PortalId] = new Portal { X = gp.SrcX, Y = gp.SrcY, DestMapId = gp.MapId, PortalType = gp.PortalType, PortalId = gp.PortalId };
                    break;

                // === Death dialog ===
                case DlgiPacket dlgi:
                    if (dlgi.Dialog.Contains("revival"))
                        ShowDeathDialog = true;
                    break;
            }
        }

        private void Log(string msg) => OnLog?.Invoke(msg);
    }
}
