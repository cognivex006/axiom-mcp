# Changelog

User-visible changes, newest first. Download wheels and checksum records from [GitHub Releases](https://github.com/orchex006/axiom-mcp/releases).

## Unreleased

Changes after the latest release are recorded here.

## 0.1.6

### Documentation

- Remove internal development identifiers from public guides, tool tables, release descriptions and packaged documentation. Describe tools and query operations by their names.
- Correct stale registration status: both public transports register all six graph tools. Explain discovery, authentication and graph readiness separately.
- Organize release changes by version and preserve historical development records internally.

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
