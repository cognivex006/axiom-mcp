# Agent instructions — axiom-mcp

Read Development.md and a verified, immutable axiom-specs pin before changing files. This is not authorization to install, elevate permissions, create a repository, commit or push.

Take only tasks owned by `axiom-mcp`. Preserve user edits and dirty files. Do not stash/discard unrelated work or change release tags. Record six preflight and eight completion checks in the canonical spec task card and attach actual command output. Keep unperformed tests and targets explicitly unverified.

Public commands, schema/layout versions, control API, shared reader/writer guard semantics and migration decisions require spec coordination. Local implementation documentation lives here and must change with the implementation. Do not copy another component's canonical policy or guide as an editable fork.

## Terminal UX development

For CLI, installer, bootstrap, interactive setup and long-running terminal changes, consult the canonical [cli-terminal-ux skill](https://github.com/orchex006/axiom-cli/blob/main/.agents/skills/cli-terminal-ux/SKILL.md) by default. Resolve its normative contract through the verified spec pin; retain native visual evidence and existing ownership/approval/release rules. Other repositories link that source rather than maintaining edited copies.
