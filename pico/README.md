# pico

An LLM-backed Discord presence that decides whether to react, then acts only via tools.

Pico watches messages in servers where it's enabled. For each message it first decides
*whether* to react at all (a cheap rule-based check, falling back to one LLM
classification call only for genuinely ambiguous cases), and if it decides to react, it
may only act by calling tools -- never by sending raw LLM text directly. Pico ships
one native tool (replying through corridor), plus whatever any other cog has
registered into corridor's cross-cog tool registry -- see
[Cross-cog tools](#cross-cog-tools) below.

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

The classification call (`GateService._classify`) only runs for that one ambiguous
case -- a message matching `\bpico\b` case-insensitively that is *neither* a direct
`@mention` of Pico *nor* a reply to one of Pico's own messages. Those two cases skip
the classifier and go straight to `RESPOND`; anything not matching `\bpico\b` at all
skips it too and goes straight to `IGNORE`. So at most one classification call
happens per triggering message, and only for that one ambiguous shape.

When it does run, the classifier is a single plain (no-tools) LLM completion built
from the same `ConversationContext` already assembled for this message (same trigger
+ up-to-10-message channel history described in
[What the LLM sees](#what-the-llm-sees)), except the system message is a separate
hardcoded classifier prompt (`_CLASSIFIER_SYSTEM_PROMPT`) instead of `[p]pico
prompt`'s value, and no `tools` are sent. It runs *before* the main tool-calling
loop and is not part of it: it doesn't count against `[p]pico maxtoolcalls`, and if
it decides `RESPOND`, the tool loop that follows re-sends a fresh message list built
from the same context (with the real system prompt) rather than reusing the
classifier's call or its answer. A classifier `LLMRequestError`, or a response with
no choices, is treated as `IGNORE` (fails closed); otherwise `RESPOND` iff the
model's reply starts with "y".

This is loose free-text parsing rather than schema-constrained structured output on
purpose, not by omission: OpenAI-style `response_format: json_schema` was tried
against `chatgpt/gpt-5.4` (LiteLLM's ChatGPT-subscription/Codex-backed provider, the
model this deployment currently runs) and the backend silently ignores it -- the
outbound request does carry `text.format: {"type": "json_schema", ...}`, confirmed
via LiteLLM debug logs, but every response chunk reports `text.format: {"type":
"text"}` back and the model replies with ordinary conversational text, not JSON. No
error is raised, so a strict `model_validate_json` parse of that text would fail
every single classification call and make the ambiguous-mention gate permanently
fail closed to `IGNORE` on this backend. If a future model/provider swap does honor
`response_format`, switching the classifier to it should be revisited then.

## What the LLM sees

Pico has no persistent conversation store. Every time a message triggers it (see
above), it rebuilds the full context from scratch and throws it away once that one
LLM call/tool loop finishes -- there is no session, thread, or memory object kept
between messages.

For a triggering message, the context sent to the LLM is:

1. A system message -- either `[p]pico prompt`'s current value (main tool-calling
   call) or a separate hardcoded classifier prompt (only for the ambiguous "mentions
   the word 'pico'" gate call; see `_CLASSIFIER_SYSTEM_PROMPT` in
   [`application/gate_service.py`](application/gate_service.py)).
2. Up to the last **10** messages (`HISTORY_LIMIT` in
   [`adapters/listener.py`](adapters/listener.py)) posted in that same Discord
   *channel* before the trigger, oldest first, fetched live from
   `channel.history(limit=10, before=message)` on every trigger -- not stored or
   cached by Pico itself. Every message in that window is included regardless of
   author, including other bots' messages, and regardless of whether it was
   addressed to Pico; each is sent as `"{author display name}: {content}"` with role
   `assistant` if the author is a bot, `user` otherwise. `content` here is
   `message.content` plus a text rendering of any embeds (title, description, and
   `**name:** value` per field -- see `_message_text` in
   [`adapters/listener.py`](adapters/listener.py)), so embed-only messages -- both
   corridor's own EMBED reply mode and other bots' embeds -- don't show up as blank
   history entries. Attachments and reactions are still not included.
3. The trigger message itself, as a final `user` message.

Because history is always "whatever Discord's channel log currently shows", the
context window is a rolling one, scoped **per channel** (not per guild, and not
global) -- two channels in the same guild never share context, and a DM or a
different server is naturally isolated the same way. `[p]pico enabled` only turns
Pico on/off per guild; it doesn't create or scope any separate memory.

There is no "start a new chat" command. The only way conversation context changes is
time passing and new messages pushing old ones out of the last-10 window -- Pico's
own replies (via the reply tool) become part of that same channel history and will
be picked up as `assistant`-role context on the next trigger, same as anyone else's
messages.

Within a single trigger's tool-calling loop (`ToolLoopService.run`), each tool call
and its result gets appended to an in-memory message list so the model can see its
own prior tool calls before deciding on the next one -- but that list lives only for
the duration of that one loop and is discarded once it ends; it is never persisted
for the next triggering message. Raw assistant `content` returned alongside a tool
call is kept in that same short-lived list (so the model has continuity across its
own loop iterations) but is never sent to Discord -- the only way anything reaches
Discord is through a tool's own handler (currently only the reply tool).

## Cross-cog tools

Any other cog can turn one of its own commands into an LLM-callable tool by
applying `@corridor.domain.llm_tool(...)` directly to the command's
callback, and pico picks it up automatically -- no pico-specific
integration code needed per registering cog.
[`deskutils`](../deskutils) decorates its `time` command this way, so if
it's installed alongside pico, a user can just ask "what time is it?"
instead of running `[p]deskutils time` by hand -- and pico calling that
tool *is* running the command: same permission check, same
`corridor.send_reply` call, sent from inside the command itself, not
composed by the LLM from returned data.

Per-message, pico asks corridor for every tool the *triggering user*
(`ctx.author`) is allowed to invoke (`corridor.list_tools_for`, filtered by
the same permission groups a Discord command would check) and adapts each
into pico's own tool-calling shape
([`tools/cross_cog.py`](tools/cross_cog.py)) alongside the native reply
tool, passing this same turn's `ctx` through so the invoked command runs
exactly as it would from a real message. If a registering cog isn't
installed, corridor's registry is simply empty and pico behaves exactly as
if this feature didn't exist. See
[`docs/corridor-tool-registry-design.md`](../docs/corridor-tool-registry-design.md)
for the full design.

## Docs

See [`docs/corridor.md`](../docs/corridor.md) for how `required_cogs` and corridor's
dependency-loading work in general.
