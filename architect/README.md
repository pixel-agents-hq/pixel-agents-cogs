# architect

A second, independent LLM agent -- reachable only over the A2A protocol.

Unlike [`pico`](../pico), no Discord user ever talks to architect directly.
Instead, architect runs its own bounded tool-calling loop against the same
shared LLM connection pico uses (owned by [`corridor`](../corridor)) and
exposes itself as an [A2A](https://a2a-protocol.org/) agent: an agent card
plus a JSON-RPC `message/send` endpoint, served on its own dedicated
listener (separate from Discord and from Red Dashboard). Any A2A client can
delegate a task to it and get back a plain-text answer; pico is the first
such client. Architect also serves [`pixelagents`](../pixelagents)' built
webview bundle under its own Red Dashboard route
(`/third-party/architect`), a second, independent consumer of the same
build [`floorplan`](../floorplan) already serves under
`/third-party/floorplan`.

See [`docs/architect-design.md`](../docs/architect-design.md) for the full
design, including what's still out of scope (real tool implementations,
what the webview actually displays, streaming task updates).

## Installing

Requires [`corridor`](../corridor) and [`pixelagents`](../pixelagents)
(both auto-loaded via `required_cogs`):

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs architect
[p]load architect
```

## Configuring

Architect shares pico's LLM connection -- a bot owner configures it once,
through corridor, not per-cog:

```
[p]corridor llm endpoint https://litellm.nntin.xyz/   # already the default
[p]corridor llm key <virtual key>                      # required, no default
[p]corridor llm model <model name>                     # required, no default
```

The A2A listener binds `127.0.0.1:8931` by default and starts automatically
on load/reload -- change it with `[p]architect a2a host`/`a2a port` (bot
owner only; each restarts the listener immediately). The office WebSocket
server (see "Webview" below) binds `127.0.0.1:8932` by default and also
starts automatically -- change it with `[p]architect ws host`/`ws port`
(bot owner only; unlike the A2A listener, these persist the setting and
ask you to reload the cog to rebind, matching floorplan's own `[p]floorplan
wsport` convention, since rebinding a socket server out from under
already-connected browser tabs is riskier than an explicit reload).

## Commands

| Command | Scope | Description |
|---|---|---|
| `[p]architect a2a host <host>` | Bot owner | Set the A2A listener's bind host and restart it |
| `[p]architect a2a port <port>` | Bot owner | Set the A2A listener's bind port and restart it |
| `[p]architect ws host <host>` | Bot owner | Set the office WebSocket server's bind host (reload to rebind) |
| `[p]architect ws port <port>` | Bot owner | Set the office WebSocket server's bind port (reload to rebind) |
| `[p]architect maxtoolcalls <n>` | Bot owner | Set the max tool calls architect may make per A2A turn (default 5) |
| `[p]architect prompt set <text>` | Bot owner | Set architect's system prompt |
| `[p]architect prompt reset` | Bot owner | Reset architect's system prompt to the default |
| `[p]architect prompt show` | Bot owner | Show architect's current system prompt |
| `[p]architect status` | Anyone | Show architect's current settings (LLM key masked), listener states, and webview/layout health |
| `[p]architect office describe` | Bot owner | Summarize the current office (rooms, zones, furniture, seats) |
| `[p]architect office rooms` | Bot owner | List every room |
| `[p]architect office createroom <label> <col> <row> <width> <height>` | Bot owner | Create a rectangular room |
| `[p]architect office place <kind> <style> <room_id>` | Bot owner | Place a piece of furniture (auto-positioned) in a room |
| `[p]architect office move <furniture_id> <col> <row>` | Bot owner | Move an existing furniture item |
| `[p]architect office remove <furniture_id>` | Bot owner | Remove a furniture item |
| `[p]architect office createzone <label> <color> <col> <row> <width> <height>` | Bot owner | Create a named overlay zone |

Unlike pico, there is no `[p]architect enabled` per-guild toggle: the A2A
listener, office WebSocket server, and webview are process-scoped, not
per-guild.

## Webview

Once [Red Web Dashboard](https://red-web-dashboard.readthedocs.io/en/latest/)
is loaded and `[p]pixelagents webview rebuild` has built a bundle,
architect's webview is reachable at your Dashboard's `/third-party/architect`
path -- the same bundle floorplan serves under `/third-party/floorplan`,
a second, independent route into it.

**Architect maintains its own layout, entirely independent of floorplan's.**
Running `[p]floorplan layout view <slug>` and clicking "Load into office"
only ever changes floorplan's own office -- architect stores and renders a
separate copy, seeded once from pixelagents' bundled default layout and
changed through `[p]architect office ...` commands and LLM tools (see
"Tools" below and
[`docs/architect-semantic-ir-design.md`](../docs/architect-semantic-ir-design.md)).

This independence needed a real, separate live connection, not just
separate storage: the vendored webview bundle computes its WebSocket URL
from the page's hostname alone (`wss://<host>/ws`), with no per-cog
distinction, so architect's own page would otherwise silently connect to
*whichever* cog's WebSocket server answers that shared path on your
domain (floorplan's, if both are installed). Architect runs its own office
WebSocket server on a distinct path, `/architect/ws`, and injects a small
script into its own served page that rewrites the bundle's connection to
that path before it's ever opened.

**This requires one addition to your reverse-proxy configuration** (e.g.
Traefik) beyond whatever rule already forwards floorplan's `/ws` to its
own bind: forward `/architect/ws` to architect's own `ws_host`/`ws_port`
bind the same way. Without that rule, architect's webview page loads but
never receives a live layout (the WebSocket connection simply fails) --
`[p]architect status`'s "Office WebSocket" field only reports the local
server's own bind state, not whether your proxy is routing to it
correctly.

There is no live-editable office state from the *browser* (no `/session`
ticket endpoint, no in-browser save) -- every connection is a read-only
viewer. The layout is edited server-side, via `[p]architect office ...`
commands and LLM tools, and changes broadcast to connected viewers as an
ordinary `layoutLoaded` message.

## Tools

Architect ships:

- Ten office layout tools (`describe_office`, `list_rooms`,
  `find_furniture`, `place_furniture`, `move_furniture`,
  `remove_furniture`, `create_room`, `create_zone`, `seat_occupant`,
  `vacate_seat`) operating on a Semantic IR, not raw Pixel Agents JSON --
  see [`docs/architect-semantic-ir-design.md`](../docs/architect-semantic-ir-design.md)
  and [`tools/office_tools.py`](tools/office_tools.py). The matching
  `[p]architect office describe/rooms/createroom/place/move/remove/createzone`
  Discord commands (bot owner only) call the exact same
  `OfficeLayoutService` methods.
- Two placeholder tools (`review_design`, `break_down_task`) with real
  input schemas but no real effect -- each just acknowledges the call.
  Implementing what they actually do is still out of scope; see
  [`tools/placeholder_tools.py`](tools/placeholder_tools.py).

## Talking to architect

Any A2A client can send a `message/send` request to architect's listener.
Architect runs its own bounded tool-calling loop (same shape as pico's, but
unlike pico it *can* answer with plain text -- see
[`application/tool_loop_service.py`](application/tool_loop_service.py))
against the inbound message and replies with the model's final text once it
stops calling tools. There is no persisted multi-turn conversation, same as
pico: every A2A task starts fresh from just that one message.

## Docs

See [`docs/corridor.md`](../docs/corridor.md) for how `required_cogs` and
corridor's dependency-loading work in general, and
[`docs/architect-design.md`](../docs/architect-design.md) for this cog's
own design and open follow-up work.
