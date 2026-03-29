# Autonomous NosTale Player — Architecture Plan

## Why NOT Use an LLM as the Core Player

| Concern | LLM (Claude/GPT) | State Machine |
|---------|-------------------|---------------|
| **Reaction time** | 1-5 seconds per decision | < 1 millisecond |
| **Cost** | $0.01-0.10 per action | Free |
| **Reliability** | May hallucinate wrong packets | Deterministic |
| **Uptime** | Rate limits, API outages | Runs forever |
| **Combat** | Way too slow to dodge/heal | Instant reaction |

**Conclusion**: The core player MUST be a fast, deterministic state machine written in C# or C++. An LLM is useful ONLY for:
- Initial protocol reverse-engineering (what we're doing now with Claude)
- Writing the bot's logic and strategies
- One-time quest planning / decision making
- Debugging when something goes wrong

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  Game Client                      │
│              (NostaleClientX.exe)                  │
├─────────────────────────────────────────────────┤
│           PacketLogger.dll (injected)             │
│   hooks recv/send → named pipes → log file        │
└──────────┬──────────────────┬────────────────────┘
           │ packets          │ commands
           ▼                  ▲
┌─────────────────────────────────────────────────┐
│              Autonomous Agent (NEW)               │
│                                                   │
│  ┌───────────┐  ┌───────────┐  ┌──────────────┐ │
│  │  Packet    │  │   Game    │  │   Action     │ │
│  │  Parser    │→ │   State   │→ │   Engine     │ │
│  └───────────┘  └───────────┘  └──────────────┘ │
│                                                   │
│  ┌───────────┐  ┌───────────┐  ┌──────────────┐ │
│  │ Combat AI │  │ Movement  │  │  Quest/Task  │ │
│  │           │  │ Pathfinder│  │  Manager     │ │
│  └───────────┘  └───────────┘  └──────────────┘ │
└─────────────────────────────────────────────────┘
           │ (optional, rare)
           ▼
┌─────────────────────────────────────────────────┐
│          Claude API (strategist, optional)        │
│   - Quest planning                                │
│   - Unknown situation handling                    │
│   - Strategy optimization                         │
└─────────────────────────────────────────────────┘
```

## Implementation Phases

### Phase 1: Protocol Documentation (CURRENT)
**Status**: In progress
**Goal**: Document every packet type by observing gameplay

- [x] Movement packets (walk, mv)
- [x] Entity packets (in, out, mv)
- [x] Stats packets (stat, cond)
- [ ] Combat packets (su, u_s, ncif, kill rewards)
- [ ] Map packets (c_map, at, portals)
- [ ] NPC interaction (npc_req, n_run, shop, buy)
- [ ] Inventory (inv, ivn, get, mvi, use item)
- [ ] Skills and buffs
- [ ] Party/group packets
- [ ] Quest packets
- [ ] Login/character selection

### Phase 2: Packet Parser & Game State Model
**Goal**: Parse raw packets into structured C# objects

```csharp
// Example of what we're building
class GameState {
    Player Self;              // position, hp, mp, level, inventory
    Dictionary<int, Entity> Entities;  // all visible entities
    int MapId;
    List<GroundItem> Drops;
    CombatState Combat;
}

class Player {
    int Id, X, Y;
    int Hp, HpMax, Mp, MpMax;
    int Level, JobLevel;
    long Xp;
    Inventory Inventory;
}

class Entity {
    int Id, Type, VNum;
    int X, Y;
    int HpPercent;
    string Name;
}
```

**Key components**:
- `PacketParser` — splits raw packet strings into typed objects
- `GameState` — maintains current world state from parsed packets
- `PacketSender` — sends formatted packets through the command file or pipe

### Phase 3: Basic Automation Modules
**Goal**: Individual behaviors that can be combined

#### 3a: Movement Engine
- Walk to coordinates (direct line)
- Pathfinding (avoid obstacles — needs map data)
- Follow entity (player/NPC)
- Flee from danger

#### 3b: Combat Engine
- Target nearest monster of type X
- Attack with skill rotation
- Heal when HP below threshold
- Use potions
- Pick up loot after kill
- Detect death and respond

#### 3c: Farm Bot
- Walk to farming area
- Find and attack monsters
- Loot drops
- Heal/rest when needed
- Return to town when inventory full
- Sell items to NPC
- Go back to farming area
- Loop forever

### Phase 4: Advanced Behaviors
- Quest completion (NPC dialog navigation)
- Party play (follow leader, assist in combat)
- Market/bazaar trading
- Equipment management
- Miniland management
- Raid participation

### Phase 5: Optional LLM Integration
- Claude API for strategic decisions when the bot encounters unknown situations
- Natural language command interface ("go farm spiders in Act 1 Map 2")
- Automatic strategy adjustment based on death/failure analysis

## Technology Choice

**Recommended: C# Console App (.NET 8)**

Why C#:
- Same language as the GUI — shared knowledge
- Fast enough for real-time packet processing
- Easy to integrate with existing named pipe infrastructure
- Strong typing for packet parsing
- Can connect to the same named pipes as the GUI, OR read/write the command/log files

**NOT recommended**:
- Python — too slow for real-time packet handling
- LLM-only — too slow, too expensive, too unreliable
- C++ — unnecessary complexity for the bot logic

## Communication Options

### Option A: File-based (simplest, works now)
```
Read:  C:\NosTalePacketLog.txt (tail -f equivalent)
Write: C:\Users\Ninuška\NosTalePacketCmd.txt
```
Pro: Already works, no code changes needed
Con: File I/O latency (~100ms), not ideal for combat

### Option B: Direct named pipe (fastest)
Create a second pipe pair for the bot, or have the bot replace the GUI's pipe connection.
Pro: Sub-millisecond latency
Con: Need to modify DLL to support multiple clients, or run without GUI

### Option C: Bot integrated into GUI (recommended)
Add a "Bot" tab/panel to the GUI that runs automation logic in-process.
Pro: Direct access to pipe, can share state with GUI display
Con: More complex GUI code

## Immediate Next Steps

1. **Finish protocol documentation** — play the game, document all packets
2. **Build PacketParser** — C# classes that parse each packet type
3. **Build GameState** — maintain live world state from parsed packets
4. **Build simple walk-to-target** — first autonomous action
5. **Build target-and-attack** — basic combat loop
6. **Combine into farm bot** — the first useful autonomous behavior
