# Painter architecture

Painter is an A2A agent with a deliberately color-only office mutation
surface. Its A2A/tool-loop shape parallels Architect's; its state adapter
is a thin client of Pixelagents' shared editor aggregate.

## Overview

Painter registers an `AgentCard`/`AgentExecutor` with Corridor's shared
A2A listener at `cog_load` instead of binding a listener of its own.
Corridor mounts painter under its own path alongside every other
registered agent, and Pico picks it up automatically the moment it
registers -- no Pico-side code names Painter specifically. Every inbound
A2A message runs Painter's own bounded `ToolLoopService` against
Corridor's shared LLM connection, adapting whatever MCP tools Corridor's
agent-tool registry currently enables for Painter on top of its own five
color tools and its one read-only structural tool.

```mermaid
flowchart TB
    subgraph painter
        cogbase["adapters/cog_base.py<br/><small>composition root</small>"]
        commands["adapters/commands.py<br/><small>[p]painter ...</small>"]
        a2a["infrastructure/a2a_server.py<br/><small>build_agent_card,<br/>PainterAgentExecutor</small>"]
        toolloop["application/tool_loop_service.py<br/><small>ToolLoopService</small>"]
        paintertools["tools/painter_tools.py,<br/>consult_architect_tool.py,<br/>agent_tool_server.py"]
        layoutservice["application/painter_layout_service.py<br/><small>PainterLayoutService</small>"]
        repo["infrastructure/office_layout_repository.py"]
        architectclient["infrastructure/architect_client.py"]
    end

    corridor["corridor<br/><small>shared A2A listener +<br/>LLM connection + pub/sub bus +<br/>agent-tool registry</small>"]
    pixelagents["pixelagents<br/><small>shared Semantic IR +<br/>editor aggregate facade</small>"]
    architect["architect<br/><small>A2A, structural mutations,<br/>read-only from painter</small>"]

    cogbase --> a2a
    cogbase --> commands
    cogbase -->|register_agent at cog_load| corridor
    corridor -.->|dispatches inbound A2A| a2a
    a2a --> toolloop
    toolloop --> paintertools
    commands --> layoutservice
    paintertools --> layoutservice
    paintertools -->|consult_architect, A2A, read-only| architectclient
    architectclient -.->|A2A over HTTP| architect
    layoutservice --> repo
    repo -->|"decode/encode via<br/>Semantic IR"| pixelagents
    a2a -->|llm_settings / llm_client| corridor
    a2a -->|publish_event AgentReplied| corridor
    paintertools -->|MCP tools| corridor
```

The repository always selects `OfficeStateKind.EDITOR`. It reads the
current aggregate, decodes the layout into the shared Semantic IR
(`pixelagents.domain.Office`), applies a color mutation, encodes it, and
calls `set_office_layout`. Pixelagents preserves seats and Corridor
increments the revision atomically. There is no whole-aggregate write.

Painter has no Dashboard route, WebSocket listener, presence listener,
Discord conversation loop, or structural mutation tool. CCTV can be
absent without blocking reads or writes; it is only an observer of
persisted state, and notices a new revision independently -- Painter has
no notification hook of its own. Painter has its own hand-written
`_publish_activity` (`adapters/cog_base.py`), reporting each tool-use step
as an `AgentReplied` on Corridor's pub/sub bus, implementing the same
shape as Architect's own `_publish_activity` rather than sharing one
implementation.

## Key flows

### Pico consults painter over A2A

```mermaid
sequenceDiagram
    participant Pico as pico
    participant Corridor as corridor<br/>(shared A2A listener)
    participant Exec as painter<br/>PainterAgentExecutor
    participant TL as painter<br/>ToolLoopService
    participant Service as PainterLayoutService
    participant PA as pixelagents<br/>(editor facade)

    Pico->>Corridor: A2A message/send to /painter/
    Corridor->>Exec: dispatch to painter's executor
    Exec->>Exec: build Task, start_work()
    Exec->>TL: run(system_prompt, user_input, tools, max_tool_calls)
    loop until final text or max_tool_calls
        TL->>TL: LLM completion (corridor's shared client)
        alt model calls a color tool
            TL->>Service: describe_tile_colors / recolor_tiles / ...
            Service->>PA: load current Office, apply color change, persist
            PA-->>Service: updated OfficeState (revision += 1)
            Service-->>TL: tool Output (status/message)
            TL->>Exec: on_activity("using tool ...") -- publishes AgentReplied
        end
    end
    TL-->>Exec: ToolLoopResult(stopped_reason="final_text", text=...)
    Exec->>Corridor: updater.complete(final answer)
    Corridor-->>Pico: completed A2A Task/Message
```

### Painter consults architect for structural context

```mermaid
sequenceDiagram
    participant TL as painter<br/>ToolLoopService
    participant Tool as ConsultArchitectTool
    participant Corridor as corridor<br/>(agent directory)
    participant ArchClient as ArchitectClient
    participant Architect as architect (A2A)

    TL->>Tool: consult_architect(prompt)
    Tool->>Corridor: list_agents() -- resolve architect's current URL
    alt architect not registered
        Tool-->>TL: status="error"
    else architect registered
        Tool->>ArchClient: ask(base_url, text=prompt)
        ArchClient->>Architect: A2A message/send
        Architect-->>ArchClient: prose answer (kinds, positions, styles -- no color)
        ArchClient-->>Tool: AgentAskResult
        Tool-->>TL: answer
    end
```

Painter resolves architect's A2A URL fresh on every call rather than
caching a fixed `base_url`, so it degrades to a normal tool error -- not a
crash -- the moment architect is unloaded or unregistered. Architect can
report exact tile/furniture color too, but painter's own
`describe_tile_colors`/`describe_furniture_colors` reach the same data
directly, without a round trip through architect's tool loop.

See [`docs/painter-design.md`](../docs/painter-design.md) for painter's
full tool/schema reference, color validation model, and design rationale.
