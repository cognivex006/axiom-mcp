"""Real registered project reads reuse verified generations without crossing scopes."""

import concurrent.futures
import hashlib
import json
import shutil

import pytest

from axiom_mcp.retention import GenerationRetention
from axiom_mcp.runtime import RegisteredSource
from axiom_mcp.tools.context import GuardedSnapshotSource
from tests.test_runtime_composition import call, configured
from tests.test_tools_support import AUTH_API, SOLUTION, WEB_APP


def test_multiple_projects_reuse_parsed_data_and_do_not_share_mutable_results(
    tmp_path, monkeypatch
):
    runtime = configured(tmp_path, monkeypatch)
    source = RegisteredSource(runtime.registry, runtime.retention)
    locations = [runtime.registry.location(SOLUTION, p, "live") for p in (AUTH_API, WEB_APP)]
    original = GuardedSnapshotSource.load_pinned
    loads = []

    def counted(self, location, generation):
        loads.append(location.project_id)
        return original(self, location, generation)

    monkeypatch.setattr(GuardedSnapshotSource, "load_pinned", counted)
    first = [source.load(location) for location in locations]
    assert [s.project_id for s in first] == [AUTH_API, WEB_APP]
    # Mutating a caller's answer cannot corrupt later answers from the cache.
    first[0].shards[0].document.clear()
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as workers:
        results = list(workers.map(source.load, locations * 4))
    assert loads == [AUTH_API, WEB_APP]
    assert all(s.shards[0].document for s in results)
    assert results[0] is results[2], "warm reads reuse immutable input without a whole-graph copy"
    with pytest.raises(TypeError, match="read-only"):
        results[0].shards[0].document.clear()
    # Read-only wrappers remain JSON-serializable for nested query attributes.
    json.dumps(results[0].shards[0].document)
    assert runtime.retention.stats()["hits"] == 8
    runtime.close()
    assert runtime.retention.stats()["entries"] == 0


def test_publisher_advance_changes_key_and_keeps_old_inflight_snapshot(tmp_path, monkeypatch):
    runtime = configured(tmp_path, monkeypatch)
    source = RegisteredSource(runtime.registry, runtime.retention)
    location = runtime.registry.location(SOLUTION, AUTH_API, "live")
    old = source.load(location)
    raw = json.loads((location.generation_root(old.generation_id) / "manifest.json").read_bytes())
    raw["analysis_profile"] = "new-profile"
    new_raw = (json.dumps(raw, sort_keys=True, separators=(",", ":")) + "\n").encode()
    generation = hashlib.sha256(new_raw).hexdigest()
    target = location.generation_root(generation)
    shutil.copytree(location.generation_root(old.generation_id), target)
    (target / "manifest.json").write_bytes(new_raw)
    from axiom_mcp.manifest import canonical_bytes

    location.pointer.write_bytes(
        canonical_bytes(
            dict(schema_version=1, generation_id=generation, manifest_sha256=generation)
        )
    )
    newer = source.load(location)
    assert newer.generation_id == generation and newer.analysis_profile == "new-profile"
    assert old.generation_id != newer.generation_id
    assert source.load_pinned(location, old.generation_id).analysis_profile == old.analysis_profile
    assert runtime.retention.stats()["entries"] == 2


def test_entry_and_parsed_memory_budgets(tmp_path, monkeypatch):
    runtime = configured(tmp_path, monkeypatch)
    runtime.retention = GenerationRetention(max_entries=1)
    source = RegisteredSource(runtime.registry, runtime.retention)
    for p in [AUTH_API, WEB_APP, AUTH_API]:
        source.load(runtime.registry.location(SOLUTION, p, "live"))
    assert runtime.retention.stats()["entries"] == 1
    assert runtime.retention.stats()["evictions"] == 2
    tiny = GenerationRetention(max_bytes=1)
    RegisteredSource(runtime.registry, tiny).load(
        runtime.registry.location(SOLUTION, AUTH_API, "live")
    )
    assert tiny.stats()["entries"] == 0
    for options in [dict(max_bytes=0), dict(max_entries=True)]:
        with pytest.raises(ValueError):
            GenerationRetention(**options)


def test_warm_cache_never_bypasses_authentication(tmp_path, monkeypatch):
    from axiom_mcp import stdio
    from axiom_mcp.runtime import compose

    runtime = configured(tmp_path, monkeypatch)
    server = compose(stdio.build_stdio_server(), runtime, transport="stdio")
    activity = []
    runtime.activity_complete = lambda: activity.append("tool completed")
    query = dict(solution_id=SOLUTION, operation="search", query="login")
    assert not call(server, "graph_query", query).isError
    assert not call(server, "graph_query", query).isError
    hits = runtime.retention.stats()["hits"]
    monkeypatch.delenv("AXIOM_MCP_TOKEN_REFERENCE")
    assert call(server, "graph_query", query).structuredContent["code"] == "UNAUTHENTICATED"
    assert runtime.retention.stats()["hits"] == hits
    assert activity == ["tool completed", "tool completed"]


def test_running_gateway_can_load_newly_registered_projects_and_refuses_broken_registry(
    tmp_path, monkeypatch
):
    from axiom_mcp import stdio
    from axiom_mcp.runtime import compose
    from tests.test_tools_support import write_registry

    runtime = configured(tmp_path, monkeypatch)
    repo = tmp_path / "registered-repo"
    registry_file = write_registry(tmp_path / "home", repo, projects=[AUTH_API])
    server = compose(stdio.build_stdio_server(), runtime, transport="stdio")
    query = dict(solution_id=SOLUTION, project_ids=[AUTH_API], operation="search", query="login")
    assert not call(server, "graph_query", query).isError
    write_registry(tmp_path / "home", repo, projects=[AUTH_API, WEB_APP])
    query["project_ids"] = [WEB_APP]
    assert not call(server, "graph_query", query).isError
    assert runtime.retention.stats()["entries"] == 2
    registry_file.write_text("{incomplete registry")
    assert call(server, "graph_query", query).structuredContent["code"] == "SNAPSHOT_UNAVAILABLE"
    assert not runtime.readiness().query.available
