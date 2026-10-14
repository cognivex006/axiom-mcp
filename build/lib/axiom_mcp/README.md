# Axiom MCP

| Component | CLI Command | คำอ่าน | Responsibility |
|---|---|---|---|
| Axiom CLI | `axm` | แอ็กซ์-เอ็ม | Main CLI / Orchestrator |
| Axiom Skills | `axsk` | แอ็กซ์-สกิล | Skills Manager |
| Axiom Graph Engine | `axge` | แอ็กซ์-จี | Graph Engine |
| Axiom MCP | `axmp` | แอ็กซ์-เอ็ม-พี | MCP Server / Gateway |

Give your AI assistant scoped access to an Axiom code graph. The gateway exposes six tools over stdio or Streamable HTTP for bounded queries, graph status and authorized refresh requests.

**[Get started](https://github.com/orchex006/axiom-mcp/blob/main/docs/install-and-upgrade.md)** · **[Tools](https://github.com/orchex006/axiom-mcp/blob/main/docs/reference/mcp.md)** · **[Documentation](https://github.com/orchex006/axiom-mcp/blob/main/docs/README.md)** · **[Releases](https://github.com/orchex006/axiom-mcp/releases)**

## How it fits together

| Component | Responsibility |
|---|---|
| [Axiom CLI](https://github.com/orchex006/axiom-cli) | Install, update and diagnose the ecosystem with `axm` |
| [Graph engine](https://github.com/orchex006/axiom-graphd) | Build graphs from registered source and refresh them through its daemon's file watcher |
| Axiom MCP | Answer assistant requests from published graphs; delegate authorized control operations to the daemon |
| [Skills](https://github.com/orchex006/axiom-skills) | Teach the assistant how to build, watch and query a graph |

Build the initial graph with the engine, keep it current with the daemon, then ask the assistant to query it through MCP. MCP does not parse your source or run a file watcher. Published snapshots can support reads while the daemon is offline; automatic refresh and control operations need the daemon.

## Install

For a complete setup, use the [Axiom installation guide](https://github.com/orchex006/axiom-cli/blob/main/docs/INSTALL.md). Check the installed ecosystem with:

```sh
axm version
axm doctor
```

For standalone MCP installation, download the portable wheel and `SHA256SUMS` from the same release, verify the checksum, then install the wheel in a Python 3.13 virtual environment. See [Installation and upgrade](https://github.com/orchex006/axiom-mcp/blob/main/docs/install-and-upgrade.md) for Windows and POSIX commands.

One Python wheel supports Windows x64, Linux x64 and Intel Mac. Apple silicon runtime verification remains deferred. Python and dependency requirements are declared in `pyproject.toml`.

## Run

Use the installed environment's Python. Stdio is the usual choice when your assistant launches the gateway as a child process:

```sh
axmp stdio --name axmp
```

Configure `AXIOM_HOME`, `AXIOM_MCP_TOKEN_REGISTRY` and `AXIOM_MCP_TOKEN_REFERENCE` in the host's launch environment. Keep the instance home and repository bindings consistent with the engine. Credential configuration contains private file or environment references, not token values. Follow [Connect your assistant](https://github.com/orchex006/axiom-mcp/blob/main/docs/install-and-upgrade.md#connect-your-assistant) for an example.

Tool discovery works without credentials; tool calls require registered credentials and scope. A listed server or tool is not proof of graph access.

Streamable HTTP requires explicit Host and Origin allowlists:

```sh
axmp http --name axmp --host 127.0.0.1 --port 8765 --allow-host 127.0.0.1:8765 --allow-origin http://127.0.0.1:8765
```

HTTP listens at `/mcp` and authenticates incoming bearer credentials. Use [HTTP transport](https://github.com/orchex006/axiom-mcp/blob/main/docs/http-transport.md) for setup and probes. The CLI owns background service supervision.

## Tools

| Tool | Use it to | Required capability |
|---|---|---|
| `graph_status` | Check coverage, generations and available capabilities | `read` |
| `graph_query` | Search code or inspect context, callers, dependencies and impact | `read` |
| `graph_version` | Inspect component versions and installation commands | `read` |
| `graph_reconcile` | Request a scoped graph refresh | `reconcile` |
| `graph_job` | Inspect a refresh job or explicitly cancel it | `reconcile` |
| `graph_verify` | Request bounded verification against a source fingerprint or barrier | `checkpoint` |

The [tool reference](https://github.com/orchex006/axiom-mcp/blob/main/docs/reference/mcp.md) documents arguments, limits and errors. Queries report generation provenance, coverage and truncation. A valid snapshot does not prove that current source was re-read. `require_fresh` needs reconcile authority and may return a pending job.

## Troubleshooting

Run `axmp version` and `axmp doctor` in the installed environment. Doctor checks credentials and query/control readiness separately; a bare installation can correctly exit `4` while configuration is incomplete.

| Symptom | Check |
|---|---|
| Tools appear but calls fail | Registered caller credentials and solution/project scope |
| No graph data | Engine registration, published generations and matching `AXIOM_HOME` |
| Queries work but refresh fails | Daemon/control connection and reconcile capability |
| HTTP refuses a connection | Host/Origin allowlists and bearer authentication |
| Stdio protocol fails | Host launch executable, argv and environment; diagnostics belong on stderr |

See [version and doctor](https://github.com/orchex006/axiom-mcp/blob/main/docs/cli-version-and-doctor.md) and [errors](https://github.com/orchex006/axiom-mcp/blob/main/docs/errors-and-redaction.md) for actionable diagnostics.

## Contributing

See [Development](https://github.com/orchex006/axiom-mcp/blob/main/Development.md) for contributor checks and [Changelog](https://github.com/orchex006/axiom-mcp/blob/main/Changelog.md) for release changes. This README also ships inside the wheel; documentation links point to the public repository so they work outside a source checkout.
