# Streamable HTTP transport

Use HTTP for an explicitly configured local gateway. It registers the same six tools as stdio and authenticates the bearer credential of each incoming request.

## Launch

```sh
axmp http --name axmp --host 127.0.0.1 --port 8765 --allow-host 127.0.0.1:8765 --allow-origin http://127.0.0.1:8765
```

Use the installed environment's Python and configure `AXIOM_HOME`, the private caller registry and any required control connection. See [Installation and upgrade](install-and-upgrade.md#connect-your-assistant).

Host and Origin allowlists are explicit; configure the actual origin used by your client. Loopback binding is the default. A non-loopback bind requires an explicit acknowledgement and an appropriate authentication policy. The process stays in foreground with one worker; the CLI owns background service supervision.

## Endpoints

| Path | Purpose |
|---|---|
| `/mcp` | MCP Streamable HTTP endpoint |
| `/healthz` | Minimal process health; not graph readiness |
| `/readyz` | Query and control readiness reported separately; unavailable planes report `NOT_READY` |

The MCP endpoint uses the official SDK's initialization, session handling and event stream. A REST endpoint returning an initialization-shaped JSON object is not an MCP connection. Use an MCP client with the required Accept headers and session handling.

## Authentication and scope

Send the caller's MCP bearer credential on requests. The gateway checks Host and Origin before scoped authorization. A credential needs `read`, `reconcile` or `checkpoint` according to the selected tool and must cover its solution/project scope. Daemon control credentials have a different audience and cannot authorize MCP access.

Operational probes need an allowlisted Host; a successful probe does not prove a caller can query a registered graph. Verify with a real authenticated `graph_status` and bounded `graph_query` call.

See [authentication](security-host-origin-auth.md) for credential references and [tool reference](reference/mcp.md) for capability requirements.

## Troubleshooting

| Result | Check |
|---|---|
| Host/Origin refused | Exact configured allowlists and the client's actual headers |
| `UNAUTHENTICATED` | Bearer credential registration and audience |
| `FORBIDDEN` | Capability and selected solution/project scope |
| Initialization or session error | MCP client handshake, Accept headers and session handling |
| Query ready, control unavailable | Daemon/control connection; valid snapshots can still serve reads |

Blocking native guard and query work runs off the HTTP event loop. Shutdown is graceful and bounded. See [transport choices](query-transports.md) and [SDK lifecycle](sdk-mount-lifecycle.md) for implementation details.
