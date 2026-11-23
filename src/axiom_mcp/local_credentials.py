"""One-step local MCP credential setup (`axmp setup`, `axm mcp setup`).

Field report 2026-11-23: graph queries need at least one registered caller
credential, but nothing created one. An operator had to hand-write a token
registry, invent a secret file and export two environment variables before an
assistant could call a single tool.

This module owns a fixed, private default location under ``AXIOM_HOME``::

    <AXIOM_HOME>/config/mcp/token-registry.json
    <AXIOM_HOME>/config/mcp/credentials/local-assistant-read.token

When ``AXIOM_MCP_TOKEN_REGISTRY`` / ``AXIOM_MCP_TOKEN_REFERENCE`` are unset, the
gateway, the stdio server and doctor use these defaults if they exist. Explicit
environment values always win. The registry stores references only, never
token values, as before. Setup never edits an AI host's configuration; it prints
the one command the operator runs to connect a host.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from axiom_mcp import security
from axiom_mcp.registry import RegistryError, axiom_home, load_registry

REGISTRY_ENV = "AXIOM_MCP_TOKEN_REGISTRY"
REFERENCE_ENV = "AXIOM_MCP_TOKEN_REFERENCE"
TOKEN_ID = "local-assistant-read"
CAPABILITIES = ("read",)


def mcp_config_dir(home: Path) -> Path:
    return home / "config" / "mcp"


def default_registry_file(home: Path) -> Path:
    return mcp_config_dir(home) / "token-registry.json"


def default_token_file(home: Path) -> Path:
    return mcp_config_dir(home) / "credentials" / f"{TOKEN_ID}.token"


def _home(env: Mapping[str, str]) -> Path | None:
    try:
        return axiom_home(env=env)
    except Exception:  # noqa: BLE001 - an unresolvable home means "no default"
        return None


def effective_registry_path(env: Mapping[str, str] | None = None) -> str | None:
    """The explicit registry path, else the setup default when it exists."""
    source = os.environ if env is None else env
    explicit = (source.get(REGISTRY_ENV) or "").strip()
    if explicit:
        return explicit
    home = _home(source)
    if home is not None and default_registry_file(home).is_file():
        return str(default_registry_file(home))
    return None


def effective_reference(env: Mapping[str, str] | None = None) -> str | None:
    """The explicit stdio credential reference, else the setup default file."""
    source = os.environ if env is None else env
    explicit = (source.get(REFERENCE_ENV) or "").strip()
    if explicit:
        return explicit
    home = _home(source)
    if home is not None and default_token_file(home).is_file():
        return json.dumps({"kind": "file", "path": str(default_token_file(home))})
    return None


@dataclass(frozen=True)
class SetupPlan:
    home: Path
    registry_file: Path
    token_file: Path
    solution_ids: tuple[str, ...]
    token_exists: bool
    registry_current: bool

    @property
    def changes(self) -> bool:
        return not (self.token_exists and self.registry_current)


def _registry_document(token_file: Path, solution_ids: tuple[str, ...]) -> dict:
    return {
        "tokens": [
            {
                "kind": "file",
                "path": str(token_file),
                "token_id": TOKEN_ID,
                "audience": security.MCP_AUDIENCE,
                "capabilities": list(CAPABILITIES),
                "solution_ids": list(solution_ids),
            }
        ]
    }


def plan(env: Mapping[str, str] | None = None) -> SetupPlan:
    source = dict(os.environ if env is None else env)
    home = axiom_home(env=source)
    solutions = tuple(sorted(s.solution_id for s in load_registry(env=source).solutions))
    registry_file = default_registry_file(home)
    token_file = default_token_file(home)
    current = False
    if registry_file.is_file():
        try:
            current = json.loads(registry_file.read_text(encoding="utf-8")) == _registry_document(
                token_file, solutions
            )
        except (OSError, ValueError):
            current = False
    return SetupPlan(home, registry_file, token_file, solutions, token_file.is_file(), current)


def _write_private(path: Path, data: str, *, exclusive: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    flags |= getattr(os, "O_BINARY", 0)
    temporary = path if exclusive else path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, flags, 0o600)
    try:
        os.write(descriptor, data.encode("utf-8"))
    finally:
        os.close(descriptor)
    if not exclusive:
        os.replace(temporary, path)


def apply(setup: SetupPlan) -> SetupPlan:
    """Create the credential once and (re)write the registry scope. Never prints the secret."""
    if not setup.token_exists:
        _write_private(setup.token_file, secrets.token_urlsafe(32) + "\n", exclusive=True)
    if not setup.registry_current:
        document = _registry_document(setup.token_file, setup.solution_ids)
        _write_private(setup.registry_file, json.dumps(document, indent=2) + "\n", exclusive=False)
    return SetupPlan(
        setup.home, setup.registry_file, setup.token_file, setup.solution_ids, True, True
    )


def host_command(home: Path) -> str:
    executable = home / "bin" / ("axmp.exe" if sys.platform.startswith("win") else "axmp")
    return f'claude mcp add axiom -- "{executable}" stdio --name axmp'


def render(setup: SetupPlan, *, applied: bool) -> list[str]:
    lines = [f"MCP credential setup for {setup.home}"]
    if setup.solution_ids:
        lines.append("Read access to: " + ", ".join(setup.solution_ids))
    else:
        lines.append(
            "Read access to: no registered solutions yet (rerun after `axge solution register`)"
        )
    token = "exists" if setup.token_exists else ("created" if applied else "will be created")
    lines.append(f"Credential: {setup.token_file} ({token}; the value is never printed)")
    registry = (
        "up to date" if setup.registry_current else ("written" if applied else "will be written")
    )
    lines.append(f"Registry: {setup.registry_file} ({registry})")
    if not applied and setup.changes:
        lines.append("Nothing was changed. Apply with: axm mcp setup --apply")
    else:
        lines.append(
            "No environment variables are needed. Restart a running gateway: "
            "`axm mcp stop`, then `axm mcp start`."
        )
        lines.append("Connect Claude Code (a separate, explicit step):")
        lines.append("  " + host_command(setup.home))
    return lines


def main(argv: list[str]) -> int:
    apply_flag = "--apply" in argv
    unknown = [a for a in argv if a not in ("--apply", "--json")]
    if unknown:
        print(f"axmp setup: unknown option {unknown[0]}", file=sys.stderr)
        return 2
    try:
        before = plan()
    except RegistryError as exc:
        print(f"axmp setup: {exc}", file=sys.stderr)
        return 6
    setup = apply(before) if apply_flag else before
    # Report what this run found and did: "created"/"written" only for real changes.
    lines = render(before, applied=apply_flag)
    if "--json" in argv:
        print(
            json.dumps(
                {
                    "command": "setup",
                    "state": "applied" if apply_flag else "planned",
                    "exit_code": 0,
                    "effective_root": {"path": str(setup.home), "source": "axiom_home"},
                    "registry_file": str(setup.registry_file),
                    "token_file": str(setup.token_file),
                    "solution_ids": list(setup.solution_ids),
                    "changes_pending": (not apply_flag) and before.changes,
                }
            )
        )
    else:
        print("\n".join(lines))
    return 0
