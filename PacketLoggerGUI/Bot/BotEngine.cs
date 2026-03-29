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
        private readonly CombatManager _combat;
        private CancellationTokenSource? _cts;
        private Task? _runTask;
        private MapGrid? _mapGrid;

        public BotState State { get; private set; } = BotState.Idle;
        public bool IsRunning => _runTask != null && !_runTask.IsCompleted;
        public CombatManager Combat => _combat;

        public event Action<string>? OnLog;

        // Config
        public double HpRestPercent { get; set; } = 30.0;
        public double MpRestPercent { get; set; } = 10.0;
        public int LoopDelayMs { get; set; } = 300;
        public bool AutoLoot { get; set; } = true;
        public bool AutoRevive { get; set; } = true;
        public bool UsePathfinding { get; set; } = false; // disabled until we have map data

        public BotEngine(GameState game, PacketSender sender)
        {
            _game = game;
            _sender = sender;
            _combat = new CombatManager(game, sender);
            _combat.OnLog += msg => OnLog?.Invoke(msg);
            _combat.EnsureBasicAttack();
        }

        public void SetMapGrid(MapGrid grid) => _mapGrid = grid;

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

                    // Dead — auto revive
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

                    // Rest if resources low
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

                    // Find target
                    var target = _combat.FindTarget();
                    if (target == null)
                    {
                        State = BotState.Idle;
                        await Task.Delay(LoopDelayMs, token);
                        continue;
                    }

                    double dist = target.DistanceTo(_game.Self.X, _game.Self.Y);
                    double attackRange = _combat.GetMaxSkillRange();

                    if (dist > attackRange)
                    {
                        // Walk toward target
                        State = BotState.Walking;

                        if (UsePathfinding && _mapGrid != null)
                        {
                            var path = Pathfinder.FindPathToRange(_mapGrid, _game.Self.X, _game.Self.Y, target.X, target.Y, attackRange - 1);
                            if (path.Found)
                            {
                                var waypoints = path.GetWaypoints(3);
                                foreach (var wp in waypoints)
                                {
                                    if (token.IsCancellationRequested) break;
                                    _sender.Walk(wp.X, wp.Y, _game.Self.Speed);
                                    await Task.Delay(600, token);
                                }
                            }
                            else
                            {
                                // Fallback to direct walk
                                _sender.Walk(target.X, target.Y, _game.Self.Speed);
                                await Task.Delay(800, token);
                            }
                        }
                        else
                        {
                            _sender.Walk(target.X, target.Y, _game.Self.Speed);
                            Log($"Walking to M{target.VNum} dist={dist:F0}");
                            await Task.Delay(800, token);
                        }
                    }
                    else
                    {
                        // In range — attack
                        State = BotState.Attacking;
                        bool attacked = _combat.Attack(target);

                        if (!attacked)
                        {
                            // No skills available, wait
                            await Task.Delay(500, token);
                            continue;
                        }

                        await Task.Delay(1200, token);

                        // Loot if target died
                        if (AutoLoot && (target.IsDead || target.HpPercent <= 0))
                        {
                            await TryLoot(target, token);
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

        private async Task TryLoot(Entity deadTarget, CancellationToken token)
        {
            State = BotState.Looting;
            // Walk to where the monster died and try to pick up items
            _sender.Walk(deadTarget.X, deadTarget.Y, _game.Self.Speed);
            await Task.Delay(800, token);

            // Try picking up any ground items near the dead monster
            var nearbyItems = _game.GroundItems.Values
                .Where(i => Math.Abs(i.X - deadTarget.X) <= 3 && Math.Abs(i.Y - deadTarget.Y) <= 3)
                .ToList();

            foreach (var item in nearbyItems)
            {
                _sender.PickUp(item.EntityId);
                await Task.Delay(300, token);
            }
        }

        private void Log(string msg) => OnLog?.Invoke($"[Bot] {msg}");
    }
}
