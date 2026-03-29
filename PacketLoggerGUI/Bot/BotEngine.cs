using System;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace PacketLoggerGUI.Bot
{
    public enum BotState
    {
        Idle,
        Walking,
        Attacking,
        Looting,
        Resting,
        Dead,
        Buffing
    }

    public class BotEngine
    {
        private readonly GameState _game;
        private readonly PacketSender _sender;
        private CancellationTokenSource? _cts;
        private Task? _runTask;

        public BotState State { get; private set; } = BotState.Idle;
        public bool IsRunning => _runTask != null && !_runTask.IsCompleted;
        public Entity? CurrentTarget { get; private set; }

        public event Action<string>? OnLog;

        // Config
        public double AttackRange { get; set; } = 12.0;
        public double LootRange { get; set; } = 20.0;
        public double HpRestPercent { get; set; } = 30.0;
        public double MpRestPercent { get; set; } = 10.0;
        public int LoopDelayMs { get; set; } = 300;
        public bool AutoLoot { get; set; } = true;
        public bool AutoRevive { get; set; } = true;
        public int[] SkillRotation { get; set; } = { 0 }; // castId rotation (0 = basic attack)
        private int _currentSkillIndex = 0;

        public BotEngine(GameState game, PacketSender sender)
        {
            _game = game;
            _sender = sender;
        }

        public void Start()
        {
            if (IsRunning) return;
            _cts = new CancellationTokenSource();
            _runTask = Task.Run(() => RunLoop(_cts.Token));
            Log("Bot started");
        }

        public void Stop()
        {
            _cts?.Cancel();
            State = BotState.Idle;
            CurrentTarget = null;
            Log("Bot stopped");
        }

        private async Task RunLoop(CancellationToken token)
        {
            while (!token.IsCancellationRequested)
            {
                try
                {
                    if (!_sender.IsReady)
                    {
                        State = BotState.Idle;
                        await Task.Delay(1000, token);
                        continue;
                    }

                    // Handle death
                    if (_game.Self.IsDead || !_game.Self.IsAlive)
                    {
                        State = BotState.Dead;
                        if (AutoRevive && _game.ShowDeathDialog)
                        {
                            Log("Auto-reviving...");
                            _sender.Revive(8);
                            _game.ShowDeathDialog = false;
                            await Task.Delay(3000, token);
                        }
                        else
                        {
                            await Task.Delay(1000, token);
                        }
                        continue;
                    }

                    // Rest if HP/MP low
                    if (_game.Self.HpPercent < HpRestPercent || _game.Self.MpPercent < MpRestPercent)
                    {
                        State = BotState.Resting;
                        if (!_game.Self.IsResting)
                        {
                            _sender.Rest(1, _game.Self.Id, true);
                            Log($"Resting — HP:{_game.Self.HpPercent:F0}% MP:{_game.Self.MpPercent:F0}%");
                        }
                        await Task.Delay(2000, token);
                        continue;
                    }
                    else if (_game.Self.IsResting)
                    {
                        _sender.Rest(1, _game.Self.Id, false);
                        await Task.Delay(500, token);
                        continue;
                    }

                    // Find nearest alive monster
                    var target = _game.NearestMonster;

                    if (target == null)
                    {
                        State = BotState.Idle;
                        CurrentTarget = null;
                        await Task.Delay(LoopDelayMs, token);
                        continue;
                    }

                    CurrentTarget = target;
                    double dist = target.DistanceTo(_game.Self.X, _game.Self.Y);

                    if (dist > AttackRange)
                    {
                        // Walk toward target
                        State = BotState.Walking;
                        _sender.Walk(target.X, target.Y, _game.Self.Speed);
                        Log($"Walking to M{target.VNum} (id:{target.Id}) dist={dist:F0}");
                        await Task.Delay(800, token);
                    }
                    else
                    {
                        // Attack with skill rotation
                        State = BotState.Attacking;
                        _sender.SelectTarget(target.Type, target.Id);
                        await Task.Delay(100, token);

                        int castId = SkillRotation[_currentSkillIndex % SkillRotation.Length];
                        _sender.UseSkill(castId, target.Type, target.Id);
                        _currentSkillIndex++;

                        Log($"Attack M{target.VNum} skill={castId} HP={target.HpPercent}%");
                        await Task.Delay(1200, token);

                        // Check if target died — loot
                        if (AutoLoot && (target.IsDead || target.HpPercent <= 0))
                        {
                            await Task.Delay(300, token);
                            await TryLoot(token);
                        }
                    }
                }
                catch (OperationCanceledException) { break; }
                catch (Exception ex)
                {
                    Log($"Error: {ex.Message}");
                    await Task.Delay(1000, token);
                }
            }
            State = BotState.Idle;
        }

        private async Task TryLoot(CancellationToken token)
        {
            // Look for ground items near our position (items appear as type 2 with specific vnums after kills)
            // For now just wait — ground item tracking requires more packet analysis
            State = BotState.Looting;
            await Task.Delay(500, token);
        }

        private void Log(string msg) => OnLog?.Invoke($"[Bot] {msg}");
    }
}
