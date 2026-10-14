# CLI: version and doctor

Inspect the installed gateway and diagnose runtime, credential and graph-readiness problems. The public CLI provides `version`, `doctor` and `update check|apply`. Unsupported subcommands exit with validation code `2`.

## Shared behaviour

- `--json` writes **one** JSON object to stdout. Nothing else is written to
  stdout in that mode, so a caller can pipe it straight into a parser.
- Without `--json` the same facts are rendered as readable lines on stdout.
- Diagnostics go to stderr. An invalid flag or an unregistered subcommand writes
  a message to stderr and exits `2`; argument parsing never silently ignores a
  flag.
- Exit codes are listed below.

## version

`version` renders the version report schema and nothing
more: exactly the eight required fields, no extra key.

```
component, version, spec_version, graph_schema, control_api,
queue_schema, build_revision, update_status
```

`build_revision` uses the configured revision, the revision stamped into a released wheel, or a checkout probe, falling back to `unknown` when none is available. `update_status` is `not_checked`: inspecting a version does not run an update check.

`version` always exits `0`.

## doctor

`doctor` exists because two very different states get conflated in practice:

- **incompatible** - the installed runtime is not the one this component was
  verified against;
- **not ready** - the runtime is fine but a data plane is not wired.

The report keeps them apart. Six sections are always present, in this order:

| Section | Reports | Fails when |
| --- | --- | --- |
| `runtime` | Python line, the pinned MCP SDK, and every pinned runtime dependency beside the installed version. | The Python line is outside the pin, the SDK is not installed or is a different version, or a pinned dependency is missing or differs. |
| `sdk_surface` | The pinned SDK names, methods, parameters and fields the component depends on. | Any locked name, parameter or field is absent from the installed SDK. |
| `protocol` | The revisions the installed SDK advertises and the minimum this component requires. | The advertisement is empty or does not include the minimum. |
| `dimensions` | The supported compatibility dimensions, including the graph schema major the gateway accepts. | A dimension is missing or unusable. |
| `credentials` | The audience, capabilities and tool-to-capability map the gateway enforces, plus the configured credential reference and, when a registry is supplied, the scope the credential actually carries. | A named reference does not resolve, or resolves to a credential the registry does not know. |
| `readiness` | Query and control plane availability, reported separately. | Either plane is unavailable. |

### Readiness is not process health

The `readiness` section states its basis explicitly:

```
"basis": "runtime_pins_and_data_plane_probes",
"process_health_is_readiness": false
```

`doctor` never issues a request to `/healthz` and never infers readiness from a
listening socket. The canonical contract distinguishes minimal process health
from query and control availability, and a supervisor that reads a listening
port as "able to answer" is the failure this section is written to prevent. The
report names both paths so the distinction is visible to whoever reads it.

### Credential scope

A reference is configuration; a token is a secret. The registry file therefore
names credentials rather than carrying them:

```json
{
  "tokens": [
    {
      "kind": "env",
      "variable": "AXIOM_MCP_TOKEN",
      "token_id": "ops-read",
      "capabilities": ["read"],
      "solution_ids": ["demo-solution"],
      "project_ids": ["auth-api"]
    }
  ]
}
```

`AXIOM_MCP_TOKEN_REFERENCE` selects the credential to diagnose and
`AXIOM_MCP_TOKEN_REGISTRY` (or `--registry`) points at the registry. A `kind` of
`file` names a path instead of a variable. Configuration that carries a literal
`token`, `value` or `secret` key is refused.

Four outcomes are reported as four different things, because they have four
different fixes:

| Report | Meaning |
| --- | --- |
| `credential_reference_not_configured` (warning) | Nothing is wired yet. That is a normal state on a fresh machine. |
| `credential_reference_unresolved` (failure, exit `5`) | A reference was named and the variable is unset or the file is missing. |
| `credential_not_registered` (failure, exit `5`) | The credential resolved but the registry does not know it. |
| resolved and registered | The report shows the granted capabilities and declared solution/project scope. |

No report ever contains a token value. Only the declared scope, a registry size
and a count are reported. A reference name that is an absolute path outside the
repository root is redacted through the same policy `axiom_mcp.errors` enforces.

### Exit codes

```
0  ready, or ready with warnings only
4  not ready  (a data plane is unavailable)
5  the configured credential is unusable or unregistered
9  incompatible (runtime, SDK surface, protocol or dimension failure)
2  invalid invocation
```

Incompatibility is checked before not-ready, so a machine with a wrong SDK
reports `9` rather than a less specific `4`.

## Installation and graph engine checks

Use `axm doctor` to check the Axiom installation, including the provisioned MCP Python runtime and the SQLite driver linked into the graph engine. SQLite readiness is based on opening a connection and executing SQL; the observed version is informational. No separate system SQLite package or matching SQLite version is required. Database schema compatibility and actual journal-mode checks still apply when opening a project graph.

Use `axmp doctor` for gateway configuration, credentials and query/control readiness. A successful installation check does not imply a configured gateway or an indexed project. If SQLite is unavailable, repair the graph engine with `axm install`; if gateway credentials or endpoints are missing, configure the specific item reported by `axmp doctor`.

The public commands are `axm`, `axsk`, `axge` and `axmp`. The CLI keeps its private installation engine in `<install-root>/bin/graphd-engine/`; AI hosts launch `axmp` and do not point at that private directory. Install and update handle verified older engine layouts. Leave project graph directories and MCP runtime generations in place.

## Contributor checks

```
python -m pytest tests -q
python -m ruff check .
python -m ruff format --check .
python release/cli_spike.py
```

The spike runs the command line as a child process and records the real exit
code and stdout of each invocation, so the console-script wiring is exercised
rather than just the module functions.
