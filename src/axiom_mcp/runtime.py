"""Portable composition of the existing tools, credentials and native readers."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import httpx
from mcp import types
from mcp.server.fastmcp import FastMCP

from axiom_mcp import manifest as manifests
from axiom_mcp import security
from axiom_mcp.catalog import load_solution_catalog
from axiom_mcp.control_client import ControlClientSettings, HttpControlClient
from axiom_mcp.errors import AxiomError, redact_details
from axiom_mcp.guard.engine import SolutionGuard
from axiom_mcp.lifecycle import ComponentReadiness, ReadinessReport
from axiom_mcp.query.cursor import CursorStore
from axiom_mcp.registry import RegistryError, SnapshotRegistry, load_registry
from axiom_mcp.retention import GenerationRetention
from axiom_mcp.shards import read_bounded
from axiom_mcp.tools import TOOL_SPECS, GuardedSnapshotSource, ToolContext, ToolPrincipal


def reference_value(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return security.token_reference_from_config(value).resolve()
    except (ValueError, TypeError, AttributeError) as exc:
        raise security.SecurityError("VALIDATION_ERROR", "invalid credential reference") from exc


class RegisteredSource:
    """Every read uses its registered instance's exact native guard namespace."""

    def __init__(self, registry: SnapshotRegistry, retention: GenerationRetention | None = None):
        self.registry = registry
        self.retention = retention

    def source(self, location: Any) -> GuardedSnapshotSource:
        binding = self.registry.solution(location.solution_id)
        return GuardedSnapshotSource(self.registry.guard_directory(binding.instance_id))

    def load(self, location: Any) -> Any:
        if self.retention is None:
            return self.source(location).load(location)
        source = self.source(location)
        guard = SolutionGuard(source.guard_dir, timeout_ms=source.timeout_ms)
        guard.ensure_directory()
        try:
            with guard.reader():
                pointer = manifests.load_pointer_bytes(read_bounded(location.pointer, 64 * 1024))
        finally:
            guard.close()
        return self.load_pinned(location, pointer.generation_id)

    def load_pinned(self, location: Any, generation_id: str) -> Any:
        source = self.source(location)
        if self.retention is None:
            return source.load_pinned(location, generation_id)
        # Revalidate the selected manifest under the native guard on every request.
        # Hot reads reuse parsed shards without following current.json or holding a guard.
        guard = SolutionGuard(source.guard_dir, timeout_ms=source.timeout_ms)
        guard.ensure_directory()
        try:
            with guard.reader():
                raw = read_bounded(
                    location.generation_root(generation_id) / "manifest.json", 4 * 1024 * 1024
                )
                manifest = manifests.load_manifest_bytes(raw)
                if (
                    manifest.generation_id != generation_id
                    or manifest.solution_id != location.solution_id
                    or manifest.project_id != location.project_id
                ):
                    raise manifests.DigestMismatch("selected generation identity differs")
        finally:
            guard.close()
        key = (
            str(location.root.resolve()),
            str(source.guard_dir.resolve()),
            location.solution_id,
            location.project_id,
            location.lane,
            manifests.SUPPORTED_SCHEMA_MAJOR,
            manifest.analysis_profile,
            generation_id,
        )
        cached = self.retention.get(key)
        if cached is not None:
            return cached
        loaded = source.load_pinned(location, generation_id)
        self.retention.put(key, loaded)
        return loaded


@dataclass
class Runtime:
    registry: SnapshotRegistry
    tokens: security.TokenRegistry
    control: HttpControlClient | None
    cursors: CursorStore
    retention: GenerationRetention = field(default_factory=GenerationRetention)
    activity_complete: Callable[[], None] | None = None
    registry_loader: Callable[[], SnapshotRegistry] | None = None

    @classmethod
    def configured(cls) -> Runtime:
        # Local import avoids cli -> http -> runtime -> cli initialization cycles.
        from axiom_mcp.cli import load_registry as load_tokens

        tokens = security.TokenRegistry()
        path = os.environ.get("AXIOM_MCP_TOKEN_REGISTRY")
        if path:
            tokens, errors = load_tokens(path)
            if errors:
                raise security.SecurityError("VALIDATION_ERROR", "invalid credential registry")
        registry = load_registry()
        control = None
        credential = reference_value(os.environ.get("AXIOM_CONTROL_TOKEN_REFERENCE"))
        if credential:
            scope = security.ScopedToken(
                credential,
                security.CONTROL_AUDIENCE,
                frozenset(security.KNOWN_CAPABILITIES),
                frozenset(s.solution_id for s in registry.solutions),
            )
            url = os.environ.get("AXIOM_CONTROL_URL", "http://127.0.0.1:44000")
            # Never send a control secret to an arbitrary remote origin.
            parsed = httpx.URL(url)
            if parsed.scheme != "http" or parsed.host not in {"127.0.0.1", "localhost", "::1"}:
                raise security.SecurityError("VALIDATION_ERROR", "control URL must be loopback")
            control = HttpControlClient(scope, settings=ControlClientSettings(base_url=url))
        return cls(registry, tokens, control, CursorStore(), registry_loader=load_registry)

    def registry_snapshot(self) -> SnapshotRegistry:
        if self.registry_loader is None:
            return self.registry
        try:
            return self.registry_loader()
        except (RegistryError, OSError, ValueError) as exc:
            raise AxiomError("SNAPSHOT_UNAVAILABLE", "registered bindings are unavailable") from exc

    def context(self, presented: str | None) -> ToolContext:
        token = self.tokens.authenticate(presented)
        registry = self.registry_snapshot()
        return ToolContext(
            registry=registry,
            principal=ToolPrincipal.of(token),
            source=RegisteredSource(registry, self.retention),
            control=self.control,
            cursors=self.cursors,
        )

    def readiness(self) -> ReadinessReport:
        try:
            registry = self.registry_snapshot()
        except AxiomError:
            return ReadinessReport(
                ComponentReadiness("query", False, "registry unavailable"),
                ComponentReadiness("control", False, "registry unavailable"),
            )
        query = False
        # Guarded manifest/shard verification, not merely a listening socket.
        if self.tokens.size:
            for binding in registry.solutions:
                for lane in ("live", "checkpoint"):
                    try:
                        load_solution_catalog(registry.catalog_location(binding.solution_id, lane))
                        projects = [p for r in binding.repositories for p in r.project_ids]
                        if not projects:
                            continue
                        for project in projects:
                            RegisteredSource(registry, self.retention).load(
                                registry.location(binding.solution_id, project, lane)
                            )
                        query = True
                        break
                    except (AxiomError, OSError, ValueError, RuntimeError):
                        continue
                if query:
                    break
        control = False
        if self.control:
            for binding in registry.solutions:
                try:
                    self.control.status(binding.solution_id)
                    control = True
                    break
                except AxiomError:
                    continue
        return ReadinessReport(
            ComponentReadiness("query", query, None if query else "registered query unavailable"),
            ComponentReadiness("control", control, None if control else "control unavailable"),
        )

    def close(self) -> None:
        self.retention.clear()
        if self.control:
            self.control.close()


def compose(server: FastMCP, runtime: Runtime, *, transport: str) -> FastMCP:
    """Register flat canonical arguments on the pinned SDK low-level surface."""
    from axiom_mcp.tools import query

    canonical = Path(__file__).with_name("_canonical")
    provenance = json.loads((canonical / "provenance.json").read_bytes())
    raw = (canonical / "query-request.schema.json").read_bytes()
    if (
        hashlib.sha256(raw).hexdigest()
        != provenance["assets"]["query-request.schema.json"]["sha256"]
    ):
        raise ValueError("canonical query schema hash mismatch")
    query_schema = json.loads(raw)

    schemas: dict[str, dict[str, Any]] = {}
    handlers: dict[str, Any] = {}
    for spec in TOOL_SPECS:
        module = importlib.import_module("axiom_mcp.tools." + spec.name.removeprefix("graph_"))
        keys = (
            set(query._GLOBAL_ARGUMENTS).union(*query._OPERATION_ARGUMENTS.values())
            if spec.name == "graph_query"
            else set(module._ARGUMENTS)
        )
        schemas[spec.name] = {
            "type": "object",
            "properties": {key: {} for key in sorted(keys)},
            "additionalProperties": False,
        }
        if spec.name == "graph_query":
            schemas[spec.name] = query_schema
        else:
            for key in keys:
                schemas[spec.name]["properties"][key] = query_schema["properties"].get(
                    key,
                    {"type": "boolean"}
                    if key in {"include_daemon", "check_update"}
                    else {"type": "array", "items": {"type": "string"}}
                    if key == "components"
                    else {"type": "integer", "minimum": 0}
                    if key in {"wait_ms", "wait_timeout_ms", "target_event_seq"}
                    else {"type": "string"},
                )
            schemas[spec.name]["required"] = (
                ["job_id"]
                if spec.name == "graph_job"
                else []
                if spec.name == "graph_version"
                else ["solution_id"]
            )
        handlers[spec.name] = getattr(module, spec.name)

    @server._mcp_server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=spec.name,
                description=spec.summary,
                inputSchema=schemas[spec.name],
                annotations=types.ToolAnnotations(**spec.annotations()),
            )
            for spec in TOOL_SPECS
        ]

    @server._mcp_server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        try:
            if transport == "http":
                request = server._mcp_server.request_context.request
                presented = security.parse_authorization(request.headers) if request else None
            else:
                presented = reference_value(os.environ.get("AXIOM_MCP_TOKEN_REFERENCE"))
            context = runtime.context(presented)
            handler = handlers.get(name)
            if handler is None:
                raise AxiomError("NOT_FOUND", "unknown tool")
            # Blocking query and native guard work never runs on the ASGI event loop.
            # Cancellation waits for bounded work so native guards are released.
            try:
                result = await anyio.to_thread.run_sync(lambda: handler(arguments, context))
            finally:
                if runtime.activity_complete is not None:
                    runtime.activity_complete()
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"{name}: completed")],
                structuredContent=result,
            )
        except (AxiomError, security.SecurityError) as exc:
            detail = redact_details({"code": exc.code, "message": exc.message})
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=exc.code)],
                structuredContent=detail,
                isError=True,
            )
        except Exception:  # noqa: BLE001 — never leak an unexpected credential/path exception
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="INTERNAL_ERROR")],
                structuredContent={"code": "INTERNAL_ERROR", "message": "tool execution failed"},
                isError=True,
            )

    return server
