namespace PacketLoggerGUI.Bot
{
    public enum PacketDirection { Recv, Send }
    public enum EntityType { Player = 1, Npc = 2, Monster = 3 }
    public enum SkillTargetType { Target = 0, Self = 1, SelfOrTarget = 2, NoTarget = 3 }
    public enum GameDirection { Up = 0, Right = 1, Down = 2, Left = 3, UpLeft = 4, UpRight = 5, DownRight = 6, DownLeft = 7 }

    public abstract class ParsedPacket
    {
        public string Opcode { get; set; } = "";
        public PacketDirection Direction { get; set; }
        public string Raw { get; set; } = "";
    }

    // === Movement ===
    public class WalkPacket : ParsedPacket { public int X, Y, Checksum, Speed; }
    public class MvPacket : ParsedPacket { public int EntityType, EntityId, X, Y, Speed; }
    public class AtPacket : ParsedPacket { public int CharId, MapId, X, Y, Dir; }
    public class CMapPacket : ParsedPacket { public int MapId; }
    public class DirPacket : ParsedPacket { public int EntityType, EntityId, Dir; }
    public class RestPacket : ParsedPacket { public int Toggle, EntityType, EntityId; }
    public class PreqPacket : ParsedPacket { } // portal request, no fields
    public class TpPacket : ParsedPacket { public int EntityType, EntityId, X, Y; }

    // === Entities ===
    public class InPacket : ParsedPacket
    {
        public int EntityType, VNum, EntityId, X, Y, Dir;
        public string Name { get; set; } = "";
        public int HpPercent, MpPercent;
    }
    public class OutPacket : ParsedPacket { public int EntityType, EntityId; }

    // === Stats ===
    public class StatPacket : ParsedPacket { public int Hp, HpMax, Mp, MpMax; public long Xp; }
    public class CondPacket : ParsedPacket { public int EntityType, EntityId, NoAttack, NoMove, Speed; }
    public class LevPacket : ParsedPacket { public int Level, JobLevel; public long Xp, XpMax, JobXp, JobXpMax; }

    // === Combat ===
    public class SuPacket : ParsedPacket
    {
        public int AttackerType, AttackerId, TargetType, TargetId;
        public int SkillVNum, Cooldown, AttackAnim, SkillEffect;
        public int PosX, PosY;
        public bool TargetIsAlive;
        public int HpPercent, Damage, HitMode, SkillType;
    }
    public class SrPacket : ParsedPacket { public int SkillSlotId; }
    public class EffPacket : ParsedPacket { public int EntityType, EntityId, EffectId; }
    public class DiePacket : ParsedPacket { public int EntityType, EntityId; }
    public class UseSkillPacket : ParsedPacket { public int CastId, TargetType, TargetId; }
    public class NcifPacket : ParsedPacket { public int EntityType, EntityId; }

    // === Chat ===
    public class SayRecvPacket : ParsedPacket { public int EntityType, EntityId, MsgType; public string Message { get; set; } = ""; }
    public class SayiPacket : ParsedPacket { public int EntityType, EntityId, MsgCode; }
    public class SpkPacket : ParsedPacket { public int Type; public string Name { get; set; } = ""; public string Message { get; set; } = ""; }

    // === NPC / Shop ===
    public class NpcReqPacket : ParsedPacket { public int EntityType, EntityId; }
    public class NRunPacket : ParsedPacket { public int ActionId; public string FullArgs { get; set; } = ""; }

    // === Inventory ===
    public class UseItemPacket : ParsedPacket { public int InvTab, PlayerId, BagType, Slot; }
    public class GetPacket : ParsedPacket { public int PickerType, PickerId, EntityId; }

    // === Map ===
    public class GpPacket : ParsedPacket { public int SrcX, SrcY, MapId, PortalType, PortalId; }

    // === Special ===
    public class GuriPacket : ParsedPacket { public int Type, SubType; public string FullArgs { get; set; } = ""; }
    public class DlgiPacket : ParsedPacket { public string Dialog { get; set; } = ""; }
    public class PulsePacket : ParsedPacket { public int Time, Flag; }
    public class RevivalPacket : ParsedPacket { public int Type; }

    // === Parser ===
    public static class PacketParser
    {
        public static ParsedPacket? Parse(string direction, string raw)
        {
            if (string.IsNullOrWhiteSpace(raw)) return null;
            string[] p = raw.Split(' ');
            if (p.Length == 0) return null;

            string op = p[0];
            var dir = direction == "RECV" ? PacketDirection.Recv : PacketDirection.Send;

            try
            {
                // Handle special packets starting with #
                if (op.StartsWith("#revival") && dir == PacketDirection.Send)
                {
                    int revType = 0;
                    var parts = op.Split('^');
                    if (parts.Length >= 2) int.TryParse(parts[1], out revType);
                    return new RevivalPacket { Opcode = "#revival", Direction = dir, Raw = raw, Type = revType };
                }

                return (op, dir) switch
                {
                    // === SEND packets ===
                    ("walk", PacketDirection.Send) when p.Length >= 5 =>
                        new WalkPacket { Opcode = op, Direction = dir, Raw = raw, X = Int(p[1]), Y = Int(p[2]), Checksum = Int(p[3]), Speed = Int(p[4]) },

                    ("u_s", PacketDirection.Send) when p.Length >= 4 =>
                        new UseSkillPacket { Opcode = op, Direction = dir, Raw = raw, CastId = Int(p[1]), TargetType = Int(p[2]), TargetId = Int(p[3]) },

                    ("ncif", PacketDirection.Send) when p.Length >= 3 =>
                        new NcifPacket { Opcode = op, Direction = dir, Raw = raw, EntityType = Int(p[1]), EntityId = Int(p[2]) },

                    ("npc_req", PacketDirection.Send) when p.Length >= 3 =>
                        new NpcReqPacket { Opcode = op, Direction = dir, Raw = raw, EntityType = Int(p[1]), EntityId = Int(p[2]) },

                    ("n_run", PacketDirection.Send) when p.Length >= 2 =>
                        new NRunPacket { Opcode = op, Direction = dir, Raw = raw, ActionId = Int(p[1]), FullArgs = raw },

                    ("dir", PacketDirection.Send) when p.Length >= 4 =>
                        new DirPacket { Opcode = op, Direction = dir, Raw = raw, EntityType = Int(p[1]), EntityId = Int(p[2]), Dir = Int(p[3]) },

                    ("rest", PacketDirection.Send) when p.Length >= 4 =>
                        new RestPacket { Opcode = op, Direction = dir, Raw = raw, Toggle = Int(p[1]), EntityType = Int(p[2]), EntityId = Int(p[3]) },

                    ("preq", PacketDirection.Send) =>
                        new PreqPacket { Opcode = op, Direction = dir, Raw = raw },

                    ("pulse", PacketDirection.Send) when p.Length >= 3 =>
                        new PulsePacket { Opcode = op, Direction = dir, Raw = raw, Time = Int(p[1]), Flag = Int(p[2]) },

                    ("get", PacketDirection.Send) when p.Length >= 4 =>
                        new GetPacket { Opcode = op, Direction = dir, Raw = raw, PickerType = Int(p[1]), PickerId = Int(p[2]), EntityId = Int(p[3]) },

                    ("u_i", PacketDirection.Send) when p.Length >= 5 =>
                        new UseItemPacket { Opcode = op, Direction = dir, Raw = raw, InvTab = Int(p[1]), PlayerId = Int(p[2]), BagType = Int(p[3]), Slot = Int(p[4]) },

                    ("guri", PacketDirection.Send) when p.Length >= 3 =>
                        new GuriPacket { Opcode = op, Direction = dir, Raw = raw, Type = Int(p[1]), SubType = p.Length >= 3 ? Int(p[2]) : 0, FullArgs = raw },

                    // === RECV packets ===
                    ("mv", PacketDirection.Recv) when p.Length >= 6 =>
                        new MvPacket { Opcode = op, Direction = dir, Raw = raw, EntityType = Int(p[1]), EntityId = Int(p[2]), X = Int(p[3]), Y = Int(p[4]), Speed = Int(p[5]) },

                    ("at", PacketDirection.Recv) when p.Length >= 6 =>
                        new AtPacket { Opcode = op, Direction = dir, Raw = raw, CharId = Int(p[1]), MapId = Int(p[2]), X = Int(p[3]), Y = Int(p[4]), Dir = Int(p[5]) },

                    ("c_map", PacketDirection.Recv) when p.Length >= 3 =>
                        new CMapPacket { Opcode = op, Direction = dir, Raw = raw, MapId = Int(p[2]) },

                    ("stat", PacketDirection.Recv) when p.Length >= 6 =>
                        new StatPacket { Opcode = op, Direction = dir, Raw = raw, Hp = Int(p[1]), HpMax = Int(p[2]), Mp = Int(p[3]), MpMax = Int(p[4]), Xp = Long(p[5]) },

                    ("cond", PacketDirection.Recv) when p.Length >= 6 =>
                        new CondPacket { Opcode = op, Direction = dir, Raw = raw, EntityType = Int(p[1]), EntityId = Int(p[2]), NoAttack = Int(p[3]), NoMove = Int(p[4]), Speed = Int(p[5]) },

                    ("lev", PacketDirection.Recv) when p.Length >= 7 =>
                        new LevPacket { Opcode = op, Direction = dir, Raw = raw, Level = Int(p[1]), JobLevel = Int(p[2]), Xp = Long(p[3]), XpMax = Long(p[4]), JobXp = Long(p[5]), JobXpMax = Long(p[6]) },

                    ("in", PacketDirection.Recv) when p.Length >= 6 =>
                        ParseInPacket(p, dir, raw),

                    ("out", PacketDirection.Recv) when p.Length >= 3 =>
                        new OutPacket { Opcode = op, Direction = dir, Raw = raw, EntityType = Int(p[1]), EntityId = Int(p[2]) },

                    ("su", PacketDirection.Recv) when p.Length >= 15 =>
                        new SuPacket
                        {
                            Opcode = op, Direction = dir, Raw = raw,
                            AttackerType = Int(p[1]), AttackerId = Int(p[2]),
                            TargetType = Int(p[3]), TargetId = Int(p[4]),
                            SkillVNum = Int(p[5]), Cooldown = Int(p[6]),
                            AttackAnim = Int(p[7]), SkillEffect = Int(p[8]),
                            PosX = Int(p[9]), PosY = Int(p[10]),
                            TargetIsAlive = Int(p[11]) != 0,
                            HpPercent = Int(p[12]), Damage = Int(p[13]),
                            HitMode = Int(p[14]),
                            SkillType = p.Length >= 16 ? Int(p[15]) : 0
                        },

                    ("sr", PacketDirection.Recv) when p.Length >= 2 =>
                        new SrPacket { Opcode = op, Direction = dir, Raw = raw, SkillSlotId = Int(p[1]) },

                    ("eff", PacketDirection.Recv) when p.Length >= 4 =>
                        new EffPacket { Opcode = op, Direction = dir, Raw = raw, EntityType = Int(p[1]), EntityId = Int(p[2]), EffectId = Int(p[3]) },

                    ("die", PacketDirection.Recv) when p.Length >= 3 =>
                        new DiePacket { Opcode = op, Direction = dir, Raw = raw, EntityType = Int(p[1]), EntityId = Int(p[2]) },

                    ("say", PacketDirection.Recv) when p.Length >= 5 =>
                        new SayRecvPacket { Opcode = op, Direction = dir, Raw = raw, EntityType = Int(p[1]), EntityId = Int(p[2]), MsgType = Int(p[3]), Message = string.Join(' ', p, 4, p.Length - 4) },

                    ("sayi", PacketDirection.Recv) when p.Length >= 5 =>
                        new SayiPacket { Opcode = op, Direction = dir, Raw = raw, EntityType = Int(p[1]), EntityId = Int(p[2]), MsgCode = Int(p[4]) },

                    ("spk", PacketDirection.Recv) when p.Length >= 5 =>
                        new SpkPacket { Opcode = op, Direction = dir, Raw = raw, Type = Int(p[1]), Name = p[3], Message = p.Length >= 5 ? string.Join(' ', p, 4, p.Length - 4) : "" },

                    ("gp", PacketDirection.Recv) when p.Length >= 6 =>
                        new GpPacket { Opcode = op, Direction = dir, Raw = raw, SrcX = Int(p[1]), SrcY = Int(p[2]), MapId = Int(p[3]), PortalType = Int(p[4]), PortalId = Int(p[5]) },

                    ("tp", PacketDirection.Recv) when p.Length >= 5 =>
                        new TpPacket { Opcode = op, Direction = dir, Raw = raw, EntityType = Int(p[1]), EntityId = Int(p[2]), X = Int(p[3]), Y = Int(p[4]) },

                    ("dlgi", PacketDirection.Recv) =>
                        new DlgiPacket { Opcode = op, Direction = dir, Raw = raw, Dialog = p.Length >= 2 ? p[1] : "" },

                    ("guri", PacketDirection.Recv) when p.Length >= 3 =>
                        new GuriPacket { Opcode = op, Direction = dir, Raw = raw, Type = Int(p[1]), SubType = Int(p[2]), FullArgs = raw },

                    _ => null
                };
            }
            catch { return null; }
        }

        private static InPacket? ParseInPacket(string[] p, PacketDirection dir, string raw)
        {
            int entityType = Int(p[1]);
            var pkt = new InPacket { Opcode = "in", Direction = dir, Raw = raw, EntityType = entityType };

            switch (entityType)
            {
                case 1: // Player: in 1 <name> - <charId> <x> <y> <dir> ...
                    if (p.Length < 8) return null;
                    pkt.Name = p[2];
                    pkt.EntityId = Int(p[4]);
                    pkt.X = Int(p[5]);
                    pkt.Y = Int(p[6]);
                    pkt.Dir = Int(p[7]);
                    // HP/MP are percentages later in the packet
                    if (p.Length > 18) { pkt.HpPercent = IntSafe(p[17]); pkt.MpPercent = IntSafe(p[18]); }
                    break;

                case 2: // NPC/Pet: in 2 <vnum> <entityId> <x> <y> <dir> <hp%> <mp%> ...
                    if (p.Length < 8) return null;
                    pkt.VNum = Int(p[2]);
                    pkt.EntityId = Int(p[3]);
                    pkt.X = Int(p[4]);
                    pkt.Y = Int(p[5]);
                    pkt.Dir = Int(p[6]);
                    pkt.HpPercent = IntSafe(p[7]);
                    pkt.MpPercent = p.Length > 8 ? IntSafe(p[8]) : 100;
                    // Name is later in the packet
                    if (p.Length > 15) pkt.Name = p[15];
                    break;

                case 3: // Monster: in 3 <vnum> <entityId> <x> <y> <dir> <hp%> <mp%> ...
                    if (p.Length < 8) return null;
                    pkt.VNum = Int(p[2]);
                    pkt.EntityId = Int(p[3]);
                    pkt.X = Int(p[4]);
                    pkt.Y = Int(p[5]);
                    pkt.Dir = Int(p[6]);
                    pkt.HpPercent = IntSafe(p[7]);
                    pkt.MpPercent = p.Length > 8 ? IntSafe(p[8]) : 100;
                    break;

                default: return null;
            }
            return pkt;
        }

        private static int Int(string s) => int.Parse(s);
        private static long Long(string s) => long.Parse(s);
        private static int IntSafe(string s) => int.TryParse(s, out int v) ? v : 0;
    }
}
