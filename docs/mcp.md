# MCP server — agent access to the live game

`ntpl mcp` exposes the packet engine to an AI agent over the Model Context
Protocol (stdio, JSON-RPC 2.0). The agent can read the world state, move the
character through the client's own MoveTo, attack, cast, pick up drops, send
raw packets and wait for the server's confirmations.

```bash
PYTHONPATH=linux/host python3 -m ntpl mcp            # stdio; broker on 47822
PYTHONPATH=linux/host python3 -m ntpl mcp --listen 0 # no broker (agent only)
```

No third-party packages: the MCP layer is standard-library JSON-RPC.

## One DLL host, one broker

The injected DLL accepts **one host** and silently replaces the previous one.
The MCP server starts a broker that owns that single connection and
rebroadcasts the packet stream to local TCP clients on port **47822**. Start
the GUI against the broker:

```bash
linux/scripts/gui.sh --port 47822
```

Do **not** run the GUI (or a monitor) on 47821 while the MCP server runs: it
would replace the broker and the two would keep kicking each other. If you
stop the MCP server, restart the GUI on its default port.

The broker forwards `SEND|…` and raw commands from every client to the DLL,
but blocks `QUIT` so one observer cannot unhook the logger for everyone. It
replays the last `STATUS` line to a new client.

## Claude Code configuration

`.mcp.json` in the project root (already committed):

```json
{
  "mcpServers": {
    "nostale": {
      "command": "python3",
      "args": ["-m", "ntpl", "mcp"],
      "env": {"PYTHONPATH": "linux/host"}
    }
  }
}
```

Start the game and inject the DLL as usual first; the broker retries until
`127.0.0.1:47821` appears.

## Tools

| Tool | Action | Confirmation |
|---|---|---|
| `game_state` | map/size, position, HP/MP, resting, skills, counts, portals | — |
| `game_entities` | live entities (kind 1/2/3) with vnum, name, distance | — |
| `game_drops` | ground items with id, item vnum, owner | — |
| `game_map` | grid size and walkable % | — |
| `game_portals` | `gp` list + learned crossings for a map | — |
| `game_path` | 8-direction A* cell path | — |
| `game_spells` | learned slots with Skill.dat range/cast/CD/MP | — |
| `game_move` | `MOVE\|x y` (client MoveTo, animated) | own `walk` SEND / position |
| `game_travel` | A* + short MoveTo legs | per leg |
| `game_attack` | `ncif` + `u_s <slot> 3 <id>` | server `su` |
| `game_cast` | `ncif` + `u_s <slot> <type> <id>` | server `su` |
| `game_cast_spell` | same by Skill.dat vnum, slot resolved from `ski`/learning | server `su` |
| `game_send` | one raw packet | — |
| `game_select` | `ncif <type> <id>` | — |
| `game_rest` | sit/stand toggle | `rest` echo |
| `game_pickup` | `get 1 <charId> <dropId>` | server `get` |
| `game_wait_for` | wait for an opcode/substring | the packet |
| `game_status` | connection state and last `STATUS` | — |
| `game_memory_find` | read-only `FIND` debug | `STATUS|FIND` |
| `game_memory_read` | read-only `READ` debug | `STATUS|READ` |

The **Game state** resource (`ntpl://game/state`) returns the same JSON as
`game_state`.

## Typical agent loop

1. `game_state` → map, position, HP/MP.
2. `game_entities` with `radius` → nearest monster.
3. `game_travel` to a cell near it (or `game_move` for one leg).
4. `game_attack` with its `entity_id` until it dies (`out` packet).
5. `game_drops` + `game_pickup` for loot.
6. `game_rest` when HP is low.

Movement is client-authoritative: `game_move`/`game_travel` call the client's
own MoveTo, so the game pathfinds, animates and sends its `walk`; the tool
waits for `world.position` to match. Attacks are confirmed by `su`; a
`cancel`/timeout is reported as a tool error, not silently swallowed.

## Security

- The DLL listener and the broker bind `127.0.0.1` only; nothing is exposed to
  the network.
- There is no authentication: any local process can connect to 47822. Treat
  the port as a local debugging surface.
- `game_send` and the memory tools are raw game access. The project rules
  still apply: no detection evasion, and automation is against Gameforge's
  ToS (the owner accepts that risk).

## Tests

`make -C linux host-test` covers the JSON-RPC framing, tool errors, the
broker broadcast/command forwarding and `QUIT` blocking. An end-to-end smoke
(fake DLL + real `ntpl mcp` subprocess) exercises initialize, tools/list,
state, move, attack and memory tools.
