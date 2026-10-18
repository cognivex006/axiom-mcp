# Local graph gateway

## Start and stop

```sh
axm mcp start
axm mcp status
axm mcp doctor
```

Start selects an available loopback port and prints the actual MCP endpoint.
It prefers 8765 and falls back to an operating-system-assigned port if necessary.
The serving process retains its socket throughout startup. No process using
another port is stopped or taken over.

The default Host and Origin policies name that exact loopback address and port.
Keep the endpoint printed by `start` in your client configuration. Client bearer
credentials and registered graph permissions remain required.

```sh
axm mcp stop
```

Bare stop selects only one unambiguous owned process. With several instances,
list them and select the intended name:

```sh
axm mcp list
axm mcp start --instance other
axm mcp stop --instance other
```

Repeated start reuses only an identical configuration. Changing its runtime,
name, policies or credential contents requires an explicit stop/restart. An
existing process is preserved when the requested configuration conflicts.
For a fixed port use `--port 8765`; a busy explicit port fails without fallback.
`--port 0` always requests an assigned port. Explicit Host/Origin policies are
kept exactly as supplied; they are never widened by automatic port selection.
Non-loopback operation requires explicit policies and the transport's existing
security requirements.

## Readiness and foreground operation

`axm doctor` retains its installation exit status and reports MCP separately.
`axm mcp doctor` observes a unique managed gateway's query and control readiness;
it does not start a service or send credentials. Ambiguous instances or an
unavailable probe cannot establish readiness. Credentials are checked separately
from whether a process is listening. Use `--verbose` or `--json` for details.

`/healthz` reports process health. `/readyz` reports query and control readiness
separately and may return 503 while configuration is incomplete. Verify an
authenticated `graph_status` and bounded `graph_query` for the intended projects
before relying on graph results.

```sh
axm mcp serve
```

Foreground serve keeps its terminal open while handling clients. Ctrl+C stops it.
Bare `axm mcp`, bare `axmp` and `axmp stdio` use stdio instead; they wait for the
agent's JSON-RPC input and end when its host closes stdin. `axmp` remains the
standalone entrypoint to the same component.

## Lifetime and memory

The default idle limit is **180 minutes (three hours)**. Set
`--idle-timeout-minutes N`; zero disables idle shutdown. Active tool request streams prevent idle expiry. Passive SSE backchannels,
protocol pings and health/readiness polling do not keep a gateway alive. An agent-owned HTTP process can use `--owner-pid PID`; the gateway observes
that process through a native kernel handle and drains when the owner exits.
Shared gateways omit owner binding so one agent ending does not stop other users.
`stdio` remains available for an agent-owned child with its existing EOF lifetime.

Default retention is 64 MiB of accounted parsed graph memory and 32 generations.
Configure `--cache-mib`, `--cache-entries` and `--max-inflight` (default 8). These
bound retained data and concurrent HTTP requests; in-flight response copies also
consume memory. Eviction does not change the generation used by an active query.
Shutdown clears the cache and closes runtime clients and ownership locks. A slow
request drains within the server's 30-second shutdown budget. Stop requests target
only the owned instance; the supervisor never kills a process by port or stale PID.

## Query projects A, B and C

Analyze and publish each project's graph with the engine first. Register their
solution/project bindings and authorize the caller for the selected scope. One
gateway can load multiple registered project graphs on demand and reuse parsed
generations across subsequent queries. A project's graph dataset uses the shared
supported graph schema; it is not a separate protocol version.

Within one solution, queries select multiple project IDs and pin the catalog
vector. Across solutions, the skill makes bounded calls per solution and reports
each vector. It does not invent an atomic cross-solution snapshot or relationships
the analyzer has not established. Cache keys include registered root and guard,
solution/project, lane, supported schema, profile and exact generation. Manifests
are revalidated under native guards; hot reads reuse the already verified parsed
shards. Publishing a new generation naturally selects a new cache key. Every
query still authenticates and checks project scope before reading cached data.

## Connect and verify

The endpoint is authenticated MCP Streamable HTTP at `/mcp`; `/healthz` reports
process health and `/readyz` reports query/control readiness separately. Configure
caller credentials and the agent's connection using [HTTP transport](http-transport.md).
A running process or successful plugin registration is not a graph query proof.
After idle exit, restart the gateway and reinitialize the MCP connection.
An update can install a new versioned runtime while an existing gateway finishes
on its old runtime. Use a separate instance/port for an overlapping new runtime;
stop the old owned instance explicitly after clients migrate. Unsupported schema
versions are reported as incompatible instead of being mixed into cached results.

Configured requests refresh trusted project bindings; an added registered project can be loaded while the gateway runs. Each request keeps its own binding snapshot, and malformed registry updates fail closed. Restart the owned gateway after credential or control-connection changes. New project registration does not grant additional token permissions.

## List and select instances (0.1.11 and later)

```powershell
axm mcp list
axm mcp list --json
axm mcp status
axm mcp stop --instance workspace
```

The same commands are available through `axmp`. Inventory looks only in the
selected canonical `AXIOM_HOME`; use the same home as the server. It shows known
instances with running state, PID, host/port, version and idle timeout. Retained
metadata from an exited process appears stopped. Native ownership locks determine
liveness; stale PID values are not treated as a running process. Unknown metadata
is reported without exposing private record content.

`status` without a name shows the inventory; `status --instance NAME` inspects
that exact name. Bare `stop` selects the only running instance, even when its name
is `workspace` rather than `default`. If none are running it says so. With multiple
or uncertain owners it stops nothing and asks you to select a name. An explicit
absent/stopped name reports that state with list guidance. There is no implicit
stop-all and no kill-by-port. Stop requests remain bound to the observed owner's
nonce, and a replacement process is preserved.

Use `--json` for inventory automation. It returns `instances`, `running_count`
and `unknown_count`. Existing explicitly named status/stop results remain JSON
objects with `instance` and `running`; human stop guidance goes to stderr.
