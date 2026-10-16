"""Analyze three disposable projects with a real engine and query them through MCP."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

import anyio
from mcp import types

from axiom_mcp import security, stdio
from axiom_mcp.query.cursor import CursorStore
from axiom_mcp.registry import load_registry
from axiom_mcp.runtime import Runtime, compose


def verify(engine: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="axiom multi project ") as temporary:
        root = Path(temporary)
        repo, home = root / "repo", root / "home"
        for project in ("a", "b", "c"):
            source = repo / "src" / project / "Example.cs"
            source.parent.mkdir(parents=True)
            source.write_text(
                f"namespace Multi;\npublic class Project{project.upper()}Needle {{\n"
                " public void Run() {}\n}\n"
            )
        human = repo / "AGENTS.md"
        human.write_bytes(b"# Human fixture instructions\nPreserve this file.\n")
        before = human.read_bytes()
        config, bindings = root / "solution.json", root / "bindings.json"
        config.write_text(
            json.dumps(
                dict(
                    id="multi",
                    profile="default",
                    catalog_host_repo="primary",
                    projects=[
                        dict(id=p, repo_id="primary", path=f"src/{p}") for p in ("a", "b", "c")
                    ],
                )
            )
        )
        bindings.write_text(json.dumps(dict(bindings=dict(primary=str(repo)))))
        env = {k: v for k, v in os.environ.items() if not k.startswith("AXIOM_")}
        env["AXIOM_HOME"] = str(home)

        def engine_call(*args):
            p = subprocess.run(
                [str(engine), *map(str, args), "--json"],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert p.returncode == 0, p.stdout + p.stderr
            return json.loads(p.stdout)

        engine_call("solution", "register", "--config", config, "--bindings", bindings, "--apply")
        built = engine_call("build", "--solution", "multi")
        registry_file = root / "mcp-registry.json"
        registry_file.write_text(
            json.dumps(
                dict(
                    schema_version=1,
                    axiom_home=str(home),
                    instances=[dict(instance_id=built["instance_id"])],
                    solutions=[
                        dict(
                            solution_id="multi",
                            instance_id=built["instance_id"],
                            catalog_host_repo="primary",
                            repositories=[
                                dict(
                                    repo_id="primary",
                                    repo_root=str(repo),
                                    projects=[dict(project_id=p) for p in ("a", "b", "c")],
                                )
                            ],
                        )
                    ],
                )
            )
        )
        registry = load_registry(
            registry_file, env={"AXIOM_HOME": str(home)}, platform=sys.platform
        )
        tokens = security.TokenRegistry()
        token = secrets.token_urlsafe(32)
        os.environ["AXIOM_MULTI_PROJECT_FIXTURE_TOKEN"] = token
        tokens.register(
            security.EnvTokenReference("AXIOM_MULTI_PROJECT_FIXTURE_TOKEN"),
            security.ScopedToken(
                "fixture",
                security.MCP_AUDIENCE,
                frozenset({security.CAPABILITY_READ}),
                frozenset({"multi"}),
            ),
        )
        os.environ["AXIOM_MCP_TOKEN_REFERENCE"] = json.dumps(
            dict(kind="env", variable="AXIOM_MULTI_PROJECT_FIXTURE_TOKEN")
        )
        runtime = Runtime(registry, tokens, None, CursorStore())
        server = compose(stdio.build_stdio_server(), runtime, transport="stdio")

        async def query():
            reply = await server._mcp_server.request_handlers[types.CallToolRequest](
                types.CallToolRequest(
                    params=types.CallToolRequestParams(
                        name="graph_query",
                        arguments=dict(
                            solution_id="multi",
                            project_ids=["a", "b", "c"],
                            operation="search",
                            query="Needle",
                            max_nodes=30,
                            max_bytes=32768,
                            consistency="allow_stale",
                        ),
                    )
                )
            )
            assert not reply.root.isError, reply.root.structuredContent
            return reply.root.structuredContent

        try:
            initial = anyio.run(query)
            text = json.dumps(initial)
            assert all(f"Project{p}Needle" in text for p in "ABC"), text
            assert runtime.retention.stats()["entries"] == 3
            for _ in range(4):
                anyio.run(query)
            warm = runtime.retention.stats()
            assert warm["hits"] >= 12 and warm["misses"] == 3
            source = repo / "src/c/Example.cs"
            source.write_text(
                "namespace Multi;\npublic class ProjectCChangedNeedle {\n public void Run() {}\n}\n"
            )
            publisher = subprocess.Popen(
                [str(engine), "build", "--solution", "multi", "--project", "c", "--full", "--json"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            observations = []
            for _ in range(8):
                observations.append(anyio.run(query))
                if publisher.poll() is not None:
                    break
            stdout, stderr = publisher.communicate(timeout=60)
            writer_contention = publisher.returncode != 0
            if writer_contention:
                assert "WRITER_ALREADY_RUNNING" in stdout + stderr, stdout + stderr
                assert all(
                    reply["project_generations"] == initial["project_generations"]
                    for reply in observations
                )
                retry = subprocess.run(
                    [
                        str(engine),
                        "build",
                        "--solution",
                        "multi",
                        "--project",
                        "c",
                        "--full",
                        "--json",
                    ],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                assert retry.returncode == 0, retry.stdout + retry.stderr
            else:
                assert publisher.returncode == 0, stdout + stderr
            final = anyio.run(query)
            assert "ProjectCChangedNeedle" in json.dumps(final), (final, stdout)

            def vector(reply):
                return tuple(
                    (p["project_id"], p["generation_id"]) for p in reply["project_generations"]
                )

            expected = {vector(initial), vector(final)}
            assert all(vector(reply) in expected for reply in observations)
            assert runtime.retention.stats()["misses"] == 4
            assert human.read_bytes() == before
            result = dict(
                passed=True,
                platform=sys.platform,
                projects=["a", "b", "c"],
                engine_sha256=hashlib.sha256(engine.read_bytes()).hexdigest(),
                analyzed_real_source=True,
                authenticated_mcp_query=True,
                initial_cache_misses=3,
                warm_cache_hits=warm["hits"],
                publisher_overlap_queries=len(observations),
                writer_contention_observed=writer_contention,
                contention_refusal_preserves_generation_vector=True,
                changed_project_selects_new_generation=True,
                all_observations_pin_complete_known_vectors=True,
                unchanged_projects_reused=True,
                human_instructions_preserved=True,
                scope="Native MCP/engine fixtures; AI-host discovery is unverified",
            )
        finally:
            runtime.close()
            os.environ.pop("AXIOM_MULTI_PROJECT_FIXTURE_TOKEN", None)
            os.environ.pop("AXIOM_MCP_TOKEN_REFERENCE", None)
        assert runtime.retention.stats()["entries"] == 0
        result["close_released_cached_graphs"] = True
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.write_text(
        json.dumps(verify(args.engine.resolve()), indent=2) + "\n", encoding="utf-8"
    )
    print("PASS analyzed projects A/B/C, authenticated MCP, hot cache and concurrent publication")
