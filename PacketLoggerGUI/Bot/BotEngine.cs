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
        Dead
    }

    public class BotEngine
    {
        private readonly GameState _game;
        private readonly PacketSender _sender;
        private CancellationTokenSource? _cts;
        private Task? _runTask;

        public BotState State { get; private set; } = BotState.Idle;
        public bool IsRunning => _runTask != null && !_runTask.IsCompleted;

        public event Action<string>? OnLog;

        // Config
        public double AttackRange { get; set; } = 15.0;
        public double HpRestPercent { get; set; } = 30.0;
        public int LoopDelayMs { get; set; } = 500;

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

                    if (!_game.Self.IsAlive)
                    {
                        State = BotState.Dead;
                        Log("Character is dead!");
                        await Task.Delay(3000, token);
                        continue;
                    }

                    // Rest if HP low
                    if (_game.Self.HpPercent < HpRestPercent)
                    {
                        State = BotState.Resting;
                        Log($"HP low ({_game.Self.HpPercent:F0}%), resting...");
                        await Task.Delay(2000, token);
                        continue;
                    }

                    // Find nearest monster
                    var nearestMonster = _game.Entities.Values
                        .Where(e => e.IsMonster && e.HpPercent > 0)
                        .OrderBy(e => e.DistanceTo(_game.Self.X, _game.Self.Y))
                        .FirstOrDefault();

                    if (nearestMonster == null)
                    {
                        State = BotState.Idle;
                        await Task.Delay(LoopDelayMs, token);
                        continue;
                    }

                    double dist = nearestMonster.DistanceTo(_game.Self.X, _game.Self.Y);

                    if (dist > AttackRange)
                    {
                        // Walk toward monster
                        State = BotState.Walking;
                        _sender.Walk(nearestMonster.X, nearestMonster.Y);
                        Log($"Walking to monster {nearestMonster.VNum} at ({nearestMonster.X}, {nearestMonster.Y}) dist={dist:F1}");
                        await Task.Delay(1000, token);
                    }
                    else
                    {
                        // Attack
                        State = BotState.Attacking;
                        _sender.SelectTarget(nearestMonster.Type, nearestMonster.Id);
                        await Task.Delay(200, token);
                        _sender.BasicAttack(nearestMonster.Type, nearestMonster.Id);
                        Log($"Attacking monster {nearestMonster.VNum} (HP: {nearestMonster.HpPercent}%)");
                        await Task.Delay(1500, token);
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

        private void Log(string msg) => OnLog?.Invoke($"[Bot] {msg}");
    }
}
