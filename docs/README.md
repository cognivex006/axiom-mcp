# Axiom MCP documentation

Start with [Installation and upgrade](install-and-upgrade.md), connect your assistant, then use the [tool reference](reference/mcp.md). MCP reads graph data produced by the engine; skills provide the assistant's workflow instructions.

| I want to… | Read |
|---|---|
| Install MCP or update the ecosystem | [Installation and upgrade](install-and-upgrade.md) |
| Connect a host-managed MCP process | [stdio transport](stdio-transport.md) |
| Manage local gateway instances and graph retention | [Local gateway](local-gateway.md) |
| Run a local HTTP gateway | [HTTP transport](http-transport.md) |
| Search code and query relationships | [Tools, arguments and examples](reference/mcp.md) |
| Diagnose missing credentials or graph data | [Version and doctor](cli-version-and-doctor.md) |
| Understand authentication and permissions | [Host, Origin and scoped credentials](security-host-origin-auth.md) |
| Handle failures and redacted errors | [Errors and redaction](errors-and-redaction.md) |
| Understand snapshot freshness and races | [Snapshot consistency](guides/snapshots.md) |

## Operations and integration

- [Transport choices and read behavior](query-transports.md)
- [Locked launch documents](locked-entrypoints.md)
- [Approved update-plan delegation](update-plan-delegation.md)
- [Versioned runtime packaging](release-packaging.md)
- [Runtime compatibility](runtime-compatibility.md)

## Technical reference

These pages explain the implementation for integrators and contributors:

- [Bounded query engine](query-engine.md)
- [Snapshot registry and machine-local bindings](snapshot-registry.md)
- [Snapshot reader](snapshot-reader-core.md)
- [Manifest and shard integrity](manifest-validation.md)
- [Portable paths](portable-paths.md)
- [MCP SDK and FastAPI lifecycle](sdk-mount-lifecycle.md)

For the complete workflow, see [Build → watch → query](https://github.com/cognivex006/axiom-skills/blob/main/docs/guides/graph-lifecycle.md). Versioned changes are in [Changelog](../Changelog.md); contribution procedures are in [Development](../Development.md).
