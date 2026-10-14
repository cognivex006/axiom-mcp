# Runtime compatibility — axiom-mcp

The gateway uses Python 3.13 and pinned dependencies. Read `axmp version` for the installed runtime and compatibility dimensions; `pyproject.toml` declares the package requirements.

## Pinned surface

| Item | Pinned value | Source |
| --- | --- | --- |
| Python | `>=3.13,<3.14`  | `pyproject.toml` / `axiom_mcp.version` |
| Official MCP SDK | `mcp==1.28.1` | `pyproject.toml` / `axiom_mcp.version` |
| FastAPI / Uvicorn / Starlette | `0.139.2` / `0.51.0` / `1.3.1` | `pyproject.toml` |
| Pydantic / anyio / httpx | `2.13.4` / `4.14.2` / `0.28.1` | `pyproject.toml` |
| Minimum protocol revision | `2025-11-25` | `axiom_mcp.version.MCP_PROTOCOL_MINIMUM` |

Graph payload schema `1`, control API `1` and queue schema `1` are independent
integer-major compatibility dimensions reported by the installed package.

## SDK compatibility checks

The SDK surface used by the transports is enforced by
`tests/test_runtime_pin.py`. The spike resolved, on the installed SDK:

- `mcp.server.fastmcp.FastMCP.streamable_http_app`, `FastMCP.run_stdio_async`,
  `FastMCP.session_manager`, and the `FastMCP.__init__` parameters `lifespan`,
  `streamable_http_path`, `host`, `port`, `json_response`, `stateless_http`,
  `transport_security`;
- `mcp.server.transport_security.TransportSecuritySettings` fields
  `enable_dns_rebinding_protection`, `allowed_hosts`, `allowed_origins`;
- `mcp.server.streamable_http.SUPPORTED_PROTOCOL_VERSIONS` (advertised:
  `2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25`) and
  `mcp.types.LATEST_PROTOCOL_VERSION` (`2025-11-25`).

Legacy entry points are deliberately not assumed. `sdk_surface_reasons()`
returns a reason whenever a pinned name, parameter or field disappears, and a
boundary test feeds the spike a legacy-only SDK shape to prove the drift is
detected instead of trusted. There is no `latest` fallback for the protocol
revision: `protocol_support_reasons()` fails when the minimum is not advertised.

## Locked entrypoints

A host starts this component without a shell: `src/axiom_mcp/entrypoints.py` resolves the
active version environment the packager installed, verifies the pinned interpreter and the
pinned SDK, and renders one absolute interpreter plus an argv list. The launch environment is
built from scratch and keeps only what the pinned runtime cannot start without. See
`docs/locked-entrypoints.md` for the document, the refusal codes and the measured findings.

## Version drift policy

A different SDK patch release, a Python version outside the pinned line, or a
missing pinned name is a pin failure, not a warning:

```text
python -m pytest tests -q
```

`axiom_mcp.version.runtime_pin_reasons()` is the shared predicate that `axiom-mcp
doctor` reports. Re-pinning requires a new spike, new artifacts and an
updated `spec.lock.json` review; it is never inferred from a build timestamp.

## Platform and host support

The portable wheel supports Windows x64, Linux x64 and Intel Mac with native platform guards. Apple silicon runtime verification is deferred. MCP protocol support does not establish integration with every assistant product or version; verify discovery and an authenticated scoped query in the selected host.

Use [installation](install-and-upgrade.md) for setup and [version and doctor](cli-version-and-doctor.md) for installed-runtime diagnostics.
