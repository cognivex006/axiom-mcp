# stdio transport

Use stdio when your assistant launches MCP as its child process. The gateway registers all six graph tools and keeps protocol messages on stdout; diagnostics go to stderr.

## Launch

Use the installed environment's Python:

```sh
axmp stdio --name axmp
```

The host should provide the executable and argv directly rather than constructing a shell command. Configure the instance home, caller registry and credential reference in its environment as shown in [Installation and upgrade](install-and-upgrade.md#connect-your-assistant).

The process runs until its input closes or it is stopped. It does not install a background service. The host owns its child-process lifecycle.

## Verify the connection

1. Complete the MCP initialization handshake and discover the six [graph tools](reference/mcp.md).
2. Call `graph_status` with an authorized registered `solution_id`.
3. Call `graph_query` with a small search and inspect the returned generation and coverage.

Discovery works without credentials, but tool calls then refuse `UNAUTHENTICATED`. A valid launch and protocol handshake do not prove that graph data is available. Read-only queries can use valid published snapshots while the daemon is offline.

## Protocol and diagnostics

Stdout contains newline-delimited JSON-RPC frames only. Do not merge stderr into stdout in a host launch configuration. Startup messages, diagnostics and shutdown counters belong on stderr.

The implementation redirects accidental text writes to diagnostics while allowing the MCP SDK's protocol buffer to write frames. The SDK negotiates the protocol revision; the component's supported minimum is reported by `axmp version`.

## Troubleshooting

| Symptom | Check |
|---|---|
| Executable not found | Absolute path to the installed Python environment |
| Initialization fails | Host argv/environment and stderr diagnostics |
| Discovery works, calls refuse authentication | Credential reference resolves and is registered for the MCP audience |
| Calls return missing scope or snapshot | Same instance home/bindings as the engine, authorized solution and published graph |
| Protocol parser sees ordinary text | Separate stdout and stderr; avoid shell banners or wrappers that print to stdout |

See [errors and redaction](errors-and-redaction.md) and [locked launch documents](locked-entrypoints.md) for integration details.
