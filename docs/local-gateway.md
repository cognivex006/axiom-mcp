# Local graph gateway

`axmp serve` and the supervisor commands below are unpublished development
additions. Published 0.1.9 supports the existing `axmp http` and `axmp stdio` forms.

## Start and stop

```powershell
axmp serve --instance workspace --host 127.0.0.1 --port 8765 --allow-host 127.0.0.1:8765 --allow-origin http://127.0.0.1:8765
```

This foreground process exits on normal terminal interruption. The same launch
options work with `axm mcp start` to run a background gateway. Inspect and stop the
owned instance with:

```powershell
axm mcp status --instance workspace
axm mcp stop --instance workspace
```

Starting an identical named background configuration reuses it. A different
configuration under that name is refused. A foreground duplicate fails without
taking over the other process. Distinct names with distinct ports, or `--port 0`
for automatic socket assignment, support separate simultaneous gateways. Inspect
status for the actual assigned port. Host/Origin allowlists stay explicit; an
allowlisted host without a port admits that host across ports.

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
