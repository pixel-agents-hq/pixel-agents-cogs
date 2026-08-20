# pico

An LLM-backed Discord presence that decides whether to react, then acts only via tools.

Pico watches messages in servers where it's enabled. For each message it first decides
*whether* to react at all (a cheap rule-based check, falling back to one LLM
classification call only for genuinely ambiguous cases), and if it decides to react, it
may only act by calling tools -- never by sending raw LLM text directly. Iteration 1
ships exactly one tool: replying through corridor.

## Installing

Requires [`corridor`](../corridor) (auto-loaded via `required_cogs`):

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs pico
[p]load pico
```

## Configuring

Pico needs a bot owner to configure its LLM connection before it will do anything --
`llm key` and `llm model` have no default and Pico stays silent until both are set:

```
[p]pico llm endpoint https://litellm.nntin.xyz/   # already the default
[p]pico llm key <virtual key>                      # required, no default
[p]pico llm model <model name>                     # required, no default
```

Then a server admin opts their server in (default off):

```
[p]pico enabled true
```

## Commands

| Command | Scope | Description |
|---|---|---|
| `[p]pico llm endpoint <url>` | Bot owner | Set the LiteLLM proxy base URL |
| `[p]pico llm key <key>` | Bot owner | Set the LiteLLM virtual key (deletes your message) |
| `[p]pico llm model <model>` | Bot owner | Set the model name passed to the LLM endpoint |
| `[p]pico maxtoolcalls <n>` | Bot owner | Set the max tool calls Pico may make per turn (default 5) |
| `[p]pico prompt set <text>` | Bot owner | Set Pico's system prompt |
| `[p]pico prompt reset` | Bot owner | Reset Pico's system prompt to the default |
| `[p]pico prompt show` | Bot owner | Show Pico's current system prompt |
| `[p]pico enabled <true\|false>` | Server admin | Enable/disable Pico for this server (default off) |
| `[p]pico status` | Anyone | Show Pico's current settings (LLM key masked) |

## How it decides to react

See [the evaluation gate in `application/gate_service.py`](application/gate_service.py)
for the exact rules: a reply to one of Pico's own messages or a direct `@mention`
responds without an LLM call; a message that merely contains the word "pico" is
ambiguous and gets one LLM classification call; anything else is ignored.

## Docs

See [`docs/corridor.md`](../docs/corridor.md) for how `required_cogs` and corridor's
dependency-loading work in general.
