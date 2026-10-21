# Installation and upgrade

Use the [Axiom CLI installation guide](https://github.com/cognivex006/axiom-cli/blob/main/docs/INSTALL.md) for the complete ecosystem. The CLI provisions the graph engine, MCP runtime and skills bundle. Registering a project and connecting an assistant are separate setup steps.

## Prerequisites

Standalone MCP requires Python 3.13 and a published graph from `axiom-graphd`. The portable wheel supports Windows x64, Linux x64 and Intel Mac. Apple silicon runtime verification remains deferred. Dependency versions are declared in `pyproject.toml` and installed with the wheel.

MCP does not require an application compiler or start the graph daemon for read-only queries. The engine needs an analyzer for the source language; continuous graph refresh requires its daemon and watcher.

## Install a standalone wheel

Download the wheel and `SHA256SUMS` from the same [release](https://github.com/cognivex006/axiom-mcp/releases). Verify the wheel's SHA-256 against that file before installation. Replace `<version>` with the downloaded release version.

Windows PowerShell:

```powershell
Get-FileHash .\axiom_mcp-<version>-py3-none-any.whl -Algorithm SHA256
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\axiom_mcp-<version>-py3-none-any.whl
.\.venv\Scripts\axmp.exe version
```

macOS or Linux:

```sh
shasum -a 256 axiom_mcp-<version>-py3-none-any.whl
python3.13 -m venv .venv
.venv/bin/python -m pip install ./axiom_mcp-<version>-py3-none-any.whl
.venv/bin/axmp version
```

On Linux, `sha256sum` is an alternative checksum command. Use the created environment's Python for every launch below; shell activation is optional. A successful wheel install does not establish credentials, a registered solution or graph readiness.

## Connect your assistant

Build the selected solution's first graph with the engine. For automatic updates after a save, keep the graph daemon running in the authorized instance. Use the [graph skills workflow](https://github.com/cognivex006/axiom-skills/blob/main/docs/guides/graph-lifecycle.md) if your assistant has the Axiom plugin.

Configure these values in the host's MCP launch environment:

| Variable | Purpose |
|---|---|
| `AXIOM_HOME` | Absolute instance home shared with the graph engine; contains registered repository bindings |
| `AXIOM_MCP_TOKEN_REGISTRY` | Private registry of caller credential references and scopes |
| `AXIOM_MCP_TOKEN_REFERENCE` | JSON reference to the stdio caller's private credential |
| `AXIOM_CONTROL_URL` | Optional loopback daemon control URL for control operations |
| `AXIOM_CONTROL_TOKEN_REFERENCE` | Optional private reference for daemon control access |

An example read-only caller registry entry is:

```json
{"tokens":[{"kind":"file","path":"<absolute-private-credential-file>","token_id":"assistant-read","audience":"axiom-mcp","capabilities":["read"],"solution_ids":["my-solution"]}]}
```

The credential file must exist and contain the credential chosen by the operator. This example declares a reference; it does not create a credential. Keep the registry and referenced file private. Add project scope when access should be narrower than the solution. Do not use a daemon-control credential as an MCP credential.

The host's stdio launch configuration uses an executable, an argument array and scoped environment values. Adapt its outer configuration format to your assistant:

```json
{
  "command":"<absolute-path-to-installed-axmp>",
  "args":["stdio","--name","axmp"],
  "env":{
    "AXIOM_HOME":"<absolute-instance-home>",
    "AXIOM_MCP_TOKEN_REGISTRY":"<absolute-private-registry-file>",
    "AXIOM_MCP_TOKEN_REFERENCE":"{\"kind\":\"file\",\"path\":\"<absolute-private-credential-file>\"}"
  }
}
```

Restart the host's MCP connection after changing launch settings. Discovery should show six tools. Verify authenticated access with `graph_status` for the registered solution, then a bounded `graph_query`. Discovery without a successful authorized call is not graph readiness.

Use [stdio](stdio-transport.md) for a host-owned child process or [HTTP](http-transport.md) for an explicit local server. [Locked entrypoints](locked-entrypoints.md) support reviewed launch documents for versioned installations.

## Verify and diagnose

```sh
axmp version
axmp doctor
axmp doctor --json
```

Run these with the installed environment's console entrypoint. Doctor checks runtime, SDK, protocol, version dimensions, credentials and readiness. Exit `4` means the setup is not ready; exit `5` indicates unusable credentials. Follow [version and doctor](cli-version-and-doctor.md) for the other exit codes.

| Problem | Action |
|---|---|
| `UNAUTHENTICATED` | Check the private credential reference and its registration |
| `FORBIDDEN` | Check the requested capability and selected scope |
| `NOT_FOUND` | Check authorized solution/project registration and the selected target |
| `SNAPSHOT_UNAVAILABLE` | Build or publish the selected graph with the engine |
| `DAEMON_UNAVAILABLE` | Check the control connection and daemon; valid snapshot reads can still work |
| `NOT_READY` with a job | Poll the returned job within a deadline; do not claim freshness while pending |

## Update and rollback

For a CLI-managed installation, use `axm update` and follow its printed plan. The [ecosystem update guide](https://github.com/cognivex006/axiom-cli/blob/main/docs/INSTALL.md) documents bootstrap scripts and repair behavior.

`axmp update check` reports MCP availability. `axmp update apply --plan <plan.json> --approve-digest <digest>` validates the reviewed plan and delegates it to the installed core updater. See [update-plan delegation](update-plan-delegation.md). MCP tool calls do not download or apply updates.

For a standalone virtual environment, create a new environment for the verified new wheel, check its configuration and switch the host's launch executable after validation. Keep the previous environment for rollback; stop the old host child before removing it. Do not pip-upgrade a running gateway in place.

For operator-managed version directories, [release packaging](release-packaging.md) documents lockfiles and rollback. To remove a CLI-managed ecosystem, use `axm uninstall` and review its scope. MCP itself has no uninstall subcommand.

## Build from source

Contributor setup and required checks are in [Development](../Development.md). Build the wheel with:

```sh
python -m build --wheel --no-isolation
```

Run this in a Python 3.13 environment with the project's build dependencies installed. The wheel includes the package README; the full documentation is available from the public repository.
