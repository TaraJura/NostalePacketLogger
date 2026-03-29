namespace PacketLoggerGUI.Bot
{
    public enum PacketDirection { Recv, Send }

    public abstract class ParsedPacket
    {
        public string Opcode { get; set; } = "";
        public PacketDirection Direction { get; set; }
        public string Raw { get; set; } = "";
    }

    // === Movement ===

    public class WalkPacket : ParsedPacket
    {
        public int X { get; set; }
        public int Y { get; set; }
        public int Checksum { get; set; }
        public int Speed { get; set; }
    }

    public class MvPacket : ParsedPacket
    {
        public int EntityType { get; set; }
        public int EntityId { get; set; }
        public int X { get; set; }
        public int Y { get; set; }
        public int Speed { get; set; }
    }

    // === Entities ===

    public class InPacket : ParsedPacket
    {
        public int EntityType { get; set; }
        public int VNum { get; set; }
        public int EntityId { get; set; }
        public int X { get; set; }
        public int Y { get; set; }
        public string Name { get; set; } = "";
        public int HpPercent { get; set; }
        public int MpPercent { get; set; }
    }

    public class OutPacket : ParsedPacket
    {
        public int EntityType { get; set; }
        public int EntityId { get; set; }
    }

    // === Stats ===

    public class StatPacket : ParsedPacket
    {
        public int Hp { get; set; }
        public int HpMax { get; set; }
        public int Mp { get; set; }
        public int MpMax { get; set; }
        public long Xp { get; set; }
    }

    public class CondPacket : ParsedPacket
    {
        public int EntityType { get; set; }
        public int EntityId { get; set; }
        public int Speed { get; set; }
    }

    // === Map ===

    public class CMapPacket : ParsedPacket
    {
        public int MapId { get; set; }
    }

    public class AtPacket : ParsedPacket
    {
        public int CharId { get; set; }
        public int MapId { get; set; }
        public int X { get; set; }
        public int Y { get; set; }
    }

    // === Combat ===

    public class SuPacket : ParsedPacket
    {
        public int AttackerType { get; set; }
        public int AttackerId { get; set; }
        public int TargetType { get; set; }
        public int TargetId { get; set; }
        public int SkillVNum { get; set; }
        public int Damage { get; set; }
        public int TargetHpPercent { get; set; }
    }

    // === Parser ===

    public static class PacketParser
    {
        public static ParsedPacket? Parse(string direction, string raw)
        {
            if (string.IsNullOrWhiteSpace(raw)) return null;

            string[] parts = raw.Split(' ');
            if (parts.Length == 0) return null;

            string opcode = parts[0];
            var dir = direction == "RECV" ? PacketDirection.Recv : PacketDirection.Send;

            try
            {
                return opcode switch
                {
                    "walk" when dir == PacketDirection.Send && parts.Length >= 5 =>
                        new WalkPacket
                        {
                            Opcode = opcode, Direction = dir, Raw = raw,
                            X = int.Parse(parts[1]),
                            Y = int.Parse(parts[2]),
                            Checksum = int.Parse(parts[3]),
                            Speed = int.Parse(parts[4])
                        },

                    "mv" when parts.Length >= 6 =>
                        new MvPacket
                        {
                            Opcode = opcode, Direction = dir, Raw = raw,
                            EntityType = int.Parse(parts[1]),
                            EntityId = int.Parse(parts[2]),
                            X = int.Parse(parts[3]),
                            Y = int.Parse(parts[4]),
                            Speed = int.Parse(parts[5])
                        },

                    "stat" when parts.Length >= 6 =>
                        new StatPacket
                        {
                            Opcode = opcode, Direction = dir, Raw = raw,
                            Hp = int.Parse(parts[1]),
                            HpMax = int.Parse(parts[2]),
                            Mp = int.Parse(parts[3]),
                            MpMax = int.Parse(parts[4]),
                            Xp = long.Parse(parts[5])
                        },

                    "cond" when parts.Length >= 6 =>
                        new CondPacket
                        {
                            Opcode = opcode, Direction = dir, Raw = raw,
                            EntityType = int.Parse(parts[1]),
                            EntityId = int.Parse(parts[2]),
                            Speed = int.Parse(parts[5])
                        },

                    "c_map" when parts.Length >= 3 =>
                        new CMapPacket
                        {
                            Opcode = opcode, Direction = dir, Raw = raw,
                            MapId = int.Parse(parts[2])
                        },

                    "at" when parts.Length >= 5 =>
                        new AtPacket
                        {
                            Opcode = opcode, Direction = dir, Raw = raw,
                            CharId = int.Parse(parts[1]),
                            MapId = int.Parse(parts[2]),
                            X = int.Parse(parts[3]),
                            Y = int.Parse(parts[4])
                        },

                    "in" when parts.Length >= 6 =>
                        ParseInPacket(parts, dir, raw),

                    "out" when parts.Length >= 3 =>
                        new OutPacket
                        {
                            Opcode = opcode, Direction = dir, Raw = raw,
                            EntityType = int.Parse(parts[1]),
                            EntityId = int.Parse(parts[2])
                        },

                    "su" when parts.Length >= 12 =>
                        new SuPacket
                        {
                            Opcode = opcode, Direction = dir, Raw = raw,
                            AttackerType = int.Parse(parts[1]),
                            AttackerId = int.Parse(parts[2]),
                            TargetType = int.Parse(parts[3]),
                            TargetId = int.Parse(parts[4]),
                            SkillVNum = int.Parse(parts[5]),
                            Damage = int.Parse(parts[10]),
                            TargetHpPercent = parts.Length >= 15 ? int.Parse(parts[14]) : -1
                        },

                    _ => null // Unknown packet — skip
                };
            }
            catch
            {
                return null; // Malformed packet — skip
            }
        }

        private static InPacket? ParseInPacket(string[] parts, PacketDirection dir, string raw)
        {
            int entityType = int.Parse(parts[1]);
            var packet = new InPacket
            {
                Opcode = "in", Direction = dir, Raw = raw,
                EntityType = entityType
            };

            switch (entityType)
            {
                case 1: // Player: in 1 <name> - <charId> <x> <y> ...
                    if (parts.Length < 7) return null;
                    packet.Name = parts[2];
                    packet.EntityId = int.Parse(parts[4]);
                    packet.X = int.Parse(parts[5]);
                    packet.Y = int.Parse(parts[6]);
                    break;

                case 2: // NPC/Pet: in 2 <vnum> <entityId> <x> <y> ...
                    if (parts.Length < 6) return null;
                    packet.VNum = int.Parse(parts[2]);
                    packet.EntityId = int.Parse(parts[3]);
                    packet.X = int.Parse(parts[4]);
                    packet.Y = int.Parse(parts[5]);
                    break;

                case 3: // Monster: in 3 <vnum> <entityId> <x> <y> ...
                    if (parts.Length < 6) return null;
                    packet.VNum = int.Parse(parts[2]);
                    packet.EntityId = int.Parse(parts[3]);
                    packet.X = int.Parse(parts[4]);
                    packet.Y = int.Parse(parts[5]);
                    break;

                default:
                    return null;
            }

            return packet;
        }
    }
}
