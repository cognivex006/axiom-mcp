# Changelog

User-visible changes, newest first. Download wheels and checksum records from [GitHub Releases](https://github.com/orchex006/axiom-mcp/releases).

## Unreleased

## 0.1.10

### New Features

- Add named local gateways with native process ownership, configurable three-hour idle exit and bounded multi-project parsed-generation retention. Preserve per-query authorization and graph provenance.

### Known Limitations

- The gateway uses authenticated MCP Streamable HTTP. Cross-solution workflows report separate generation vectors; no atomic cross-solution snapshot is claimed. Restart the owned gateway after credential/control changes. Mac ARM and separate WSL2 were not run.

## 0.1.9

### Chores

- Package the unchanged component behavior for the 0.1.9 distribution that fixes legacy CLI-store adoption. Existing protocol, graph and host capability limits remain unchanged.

## 0.1.8

### Chores

- Package the existing portable MCP protocol and query surface for the 0.1.8 distribution; runtime behavior and tool schemas remain unchanged.


## 0.1.7

### New Features

- Add `axmp` as the public MCP command: run stdio by default, select HTTP explicitly, or use version, doctor and update diagnostics. Keep authentication, scopes and protocol streams unchanged.

### Documentation

- Clarify installation versus gateway diagnostics, functional SQLite readiness and repair guidance. Document the private `bin/graphd-engine/` layout and the four public commands.

- Reorganize MCP setup around installation, assistant connection, scoped graph queries and troubleshooting. Explain the engine, watcher and MCP responsibilities and distinguish tool discovery from authenticated access.
- Remove internal tracking labels, private specification paths and obsolete development transcripts from public guides. Add operation-specific query examples and current platform guidance.

### Chores

- Extend documentation checks to reject internal invariant labels and numbered specification references in guides, release notes and wheel descriptions.

## 0.1.6

### Documentation

- Describe public graph tools by name and clarify setup, authentication and readiness in guides and packaged documentation.
- Clarify that stdio and Streamable HTTP expose all six graph tools; discovering a tool does not prove authenticated graph access.
- Group release changes by version with links to the corresponding source comparison.

### Chores

- Align the portable package version with the improved ecosystem release. Tool names, schemas, dependencies and permissions are unchanged.
- Check public documentation, generated release notes and packaged wheel README/METADATA for internal identifiers before delivery.

Full Changelog: [v0.1.5…v0.1.6](https://github.com/orchex006/axiom-mcp/compare/v0.1.5...v0.1.6)

## 0.1.5

### New Features

- Add installation commands to `graph_version`: identify `axm` and `axiom-cli`, show version/doctor/update/removal commands and provide the installation/update URLs.
- Keep `graph_version` read-only; it reports commands without installing or updating software.

Full Changelog: [v0.1.4…v0.1.5](https://github.com/orchex006/axiom-mcp/compare/v0.1.4...v0.1.5)

## 0.1.4

### Chores

- Align the package version with the ecosystem release that introduces one-line update scripts. MCP tool behavior is unchanged.

Full Changelog: [v0.1.3…v0.1.4](https://github.com/orchex006/axiom-mcp/compare/v0.1.3...v0.1.4)

## 0.1.3

### Chores

- Align the package version with the ecosystem's one-line installer release. MCP tool behavior is unchanged.

Full Changelog: [v0.1.2…v0.1.3](https://github.com/orchex006/axiom-mcp/compare/v0.1.2...v0.1.3)

## 0.1.2

### New Features

- Register `graph_status`, `graph_query`, `graph_reconcile`, `graph_job`, `graph_verify` and `graph_version` on stdio and Streamable HTTP. Each request keeps the registered caller's capability scope.
- Provide one portable Python wheel for Windows x64, Linux x64 and Intel Mac, with pinned dependency inputs for ecosystem distribution.
- Delegate an approved ecosystem update plan to the installed native core CLI and propagate its result. Ship verified evaluator assets so the installed wheel does not require a private specification checkout.

### Bug Fixes

- Expose only the `axmp` console command. Access locked launch-plan diagnostics through `axmp entrypoints`.

- Use the installed platform's native guard and registry resolution rather than a developer-machine path assumption.
- Keep configured credential-file references usable in a sanitized launch environment without copying token values.

### Known Limitations

- Native MCP protocol checks do not establish integration with a particular licensed AI-host application. Apple silicon runtime verification remains deferred.

Full Changelog: [v0.1.1…v0.1.2](https://github.com/orchex006/axiom-mcp/compare/v0.1.1...v0.1.2)

## 0.1.1

### New Features

- Publish the MCP wheel for Intel Mac testing with immutable source and downloadable asset checksum records.

### Bug Fixes

- Allow idle stdio shutdown on interrupt and handle redirected pipes/files with cancellable reads, including split UTF-8 input and EOF.

Full Changelog: [v0.1.0…v0.1.1](https://github.com/orchex006/axiom-mcp/compare/v0.1.0...v0.1.1)

## 0.1.0

### New Features

- Introduce the experimental Python gateway with stdio and Streamable HTTP transports, scoped authentication, redacted errors and installation diagnostics.
- Add bounded graph queries over pinned snapshots, native reader guards, manifest verification and generation-aware caching.
- Provide locked entrypoint plans and explicit update-plan delegation.

### Known Limitations

- This initial test release predates the complete portable six-tool composition. Use current release instructions for a new installation.
