using System;
using System.Collections.Generic;
using System.Linq;

namespace PacketLoggerGUI.Bot
{
    public class Skill
    {
        public int CastId { get; set; }       // used in u_s packet
        public int VNum { get; set; }          // skill vnum
        public string Name { get; set; } = "";
        public int Range { get; set; } = 10;
        public int ManaCost { get; set; }
        public int CooldownMs { get; set; }
        public SkillTargetType TargetType { get; set; } = SkillTargetType.Target;
        public bool IsReady { get; set; } = true;
        public DateTime LastUsed { get; set; }

        public bool CanUse(int currentMp)
        {
            if (!IsReady) return false;
            if (ManaCost > 0 && currentMp < ManaCost) return false;
            if (CooldownMs > 0 && (DateTime.Now - LastUsed).TotalMilliseconds < CooldownMs) return false;
            return true;
        }

        public void MarkUsed()
        {
            LastUsed = DateTime.Now;
            IsReady = CooldownMs == 0; // instant skills stay ready
        }

        public void MarkReady() => IsReady = true;
    }

    public enum TargetPriority
    {
        Nearest,
        LowestHp,
        HighestLevel
    }

    public class CombatManager
    {
        private readonly GameState _game;
        private readonly PacketSender _sender;
        private readonly List<Skill> _skillRotation = new();
        private int _currentSkillIndex;
        private Entity? _currentTarget;

        public event Action<string>? OnLog;

        // Config
        public TargetPriority Priority { get; set; } = TargetPriority.Nearest;
        public double MaxAggroRange { get; set; } = 20.0;
        public HashSet<int> TargetVNums { get; set; } = new(); // empty = all monsters

        public Entity? CurrentTarget => _currentTarget;
        public IReadOnlyList<Skill> Skills => _skillRotation;

        public CombatManager(GameState game, PacketSender sender)
        {
            _game = game;
            _sender = sender;

            // Listen for combat events
            _game.OnCombatHit += OnCombatHit;
        }

        /// <summary>Add a skill to the rotation. Order matters — first skill is used first.</summary>
        public void AddSkill(int castId, int range = 10, int cooldownMs = 0, int manaCost = 0, SkillTargetType targetType = SkillTargetType.Target)
        {
            _skillRotation.Add(new Skill
            {
                CastId = castId,
                Range = range,
                CooldownMs = cooldownMs,
                ManaCost = manaCost,
                TargetType = targetType
            });
        }

        /// <summary>Set default basic attack (castId 0) if no skills configured.</summary>
        public void EnsureBasicAttack()
        {
            if (_skillRotation.Count == 0)
                AddSkill(0, range: 10);
        }

        /// <summary>Find the best target based on priority and filters.</summary>
        public Entity? FindTarget()
        {
            var candidates = _game.Monsters
                .Where(m => m.IsAlive);

            // Filter by VNum if specified
            if (TargetVNums.Count > 0)
                candidates = candidates.Where(m => TargetVNums.Contains(m.VNum));

            // Filter by range
            candidates = candidates.Where(m => m.DistanceTo(_game.Self.X, _game.Self.Y) <= MaxAggroRange);

            // Sort by priority
            _currentTarget = Priority switch
            {
                TargetPriority.Nearest => candidates
                    .OrderBy(m => m.DistanceTo(_game.Self.X, _game.Self.Y))
                    .FirstOrDefault(),
                TargetPriority.LowestHp => candidates
                    .OrderBy(m => m.HpPercent)
                    .FirstOrDefault(),
                _ => candidates.FirstOrDefault()
            };

            return _currentTarget;
        }

        /// <summary>Get the next available skill from the rotation.</summary>
        public Skill? GetNextSkill()
        {
            if (_skillRotation.Count == 0) return null;

            // Try each skill in rotation order starting from current index
            for (int i = 0; i < _skillRotation.Count; i++)
            {
                int idx = (_currentSkillIndex + i) % _skillRotation.Count;
                var skill = _skillRotation[idx];

                if (skill.CanUse(_game.Self.Mp))
                {
                    _currentSkillIndex = (idx + 1) % _skillRotation.Count;
                    return skill;
                }
            }

            // No skills ready — try basic attack (castId 0)
            var basic = _skillRotation.FirstOrDefault(s => s.CastId == 0);
            if (basic != null && basic.CanUse(_game.Self.Mp))
                return basic;

            return null;
        }

        /// <summary>Attack the current target with the next available skill.</summary>
        public bool Attack(Entity target)
        {
            var skill = GetNextSkill();
            if (skill == null) return false;

            if (skill.TargetType == SkillTargetType.Self || skill.TargetType == SkillTargetType.NoTarget)
            {
                _sender.SelfBuff(skill.CastId);
            }
            else
            {
                _sender.SelectTarget(target.Type, target.Id);
                _sender.UseSkill(skill.CastId, target.Type, target.Id);
            }

            skill.MarkUsed();
            Log($"Skill {skill.CastId} -> {target.VNum} (HP:{target.HpPercent}%)");
            return true;
        }

        /// <summary>Check if target is in range of any available skill.</summary>
        public bool IsInAttackRange(Entity target)
        {
            var skill = GetNextSkill();
            if (skill == null) return false;
            return target.DistanceTo(_game.Self.X, _game.Self.Y) <= skill.Range;
        }

        /// <summary>Get the maximum range of all skills.</summary>
        public double GetMaxSkillRange()
        {
            if (_skillRotation.Count == 0) return 1;
            return _skillRotation.Max(s => s.Range);
        }

        private void OnCombatHit(SuPacket su)
        {
            // Update skill cooldowns from su packets
            if (su.AttackerId == _game.Self.Id)
            {
                var skill = _skillRotation.FirstOrDefault(s => s.VNum == su.SkillVNum);
                if (skill != null && su.Cooldown > 0)
                {
                    skill.CooldownMs = su.Cooldown * 100;
                    skill.MarkUsed();
                }
            }

            // Check if our target died
            if (_currentTarget != null && su.TargetId == _currentTarget.Id && !su.TargetIsAlive)
            {
                Log($"Target {_currentTarget.VNum} killed!");
                _currentTarget = null;
            }
        }

        /// <summary>Mark a skill as ready (from sr packet).</summary>
        public void ResetSkillCooldown(int slotId)
        {
            if (slotId >= 0 && slotId < _skillRotation.Count)
                _skillRotation[slotId].MarkReady();
        }

        /// <summary>Mark all skills as ready.</summary>
        public void ResetAllCooldowns()
        {
            foreach (var s in _skillRotation) s.MarkReady();
        }

        private void Log(string msg) => OnLog?.Invoke($"[Combat] {msg}");
    }
}
