"""Native gateway processes, kernel ownership and graceful resource release."""

import asyncio
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from axiom_mcp.gateway import DEFAULT_IDLE_MINUTES, Activity, ActivityMiddleware

ROOT = Path(__file__).resolve().parents[1]
LAUNCHES = {}
OWNED = {}


def environment(home):
    env = {k: v for k, v in os.environ.items() if not k.startswith("AXIOM_")}
    env.update(AXIOM_HOME=str(home), PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")
    return env


def words(name, *extra):
    return [
        sys.executable,
        "-m",
        "axiom_mcp.gateway",
        "serve",
        "--instance",
        name,
        "--port",
        "0",
        "--allow-host",
        "127.0.0.1",
        "--allow-origin",
        "http://127.0.0.1",
        *extra,
        "--launch-id",
        secrets.token_hex(16),
    ]


def launch(home, name, *extra):
    args = words(name, *extra)
    proc = subprocess.Popen(args, env=environment(home), stderr=subprocess.PIPE)
    LAUNCHES[proc.pid] = args[-1]
    OWNED[proc.pid] = (home, name)
    return proc


def wait_record(home, name, proc):
    for _ in range(160):
        if proc.poll() is not None:
            raise AssertionError(proc.stderr.read().decode())
        try:
            doc = json.loads((home / "gateways" / name / "state.json").read_text())
            if doc.get("launch_id") == LAUNCHES[proc.pid] and doc.get("port"):
                r = httpx.get(f"http://127.0.0.1:{doc['port']}/healthz", timeout=0.5)
                if r.status_code == 200:
                    return doc
        except (OSError, ValueError, httpx.HTTPError):
            pass
        time.sleep(0.05)
    raise AssertionError("gateway did not start")


def cleanup(proc):
    if proc.pid in OWNED:
        home, name = OWNED[proc.pid]
        try:
            state = json.loads((home / "gateways" / name / "state.json").read_text())
            if state.get("launch_id") == LAUNCHES[proc.pid]:
                (home / "gateways" / name / "stop.json").write_text(
                    json.dumps({"nonce": state["nonce"]})
                )
                proc.wait(timeout=8)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@pytest.mark.parametrize("json_mode", [False, True])
def test_listening_notice_is_actionable_without_claiming_query_readiness(tmp_path, json_mode):
    args = words("diagnostics", *(["--json"] if json_mode else []))
    proc = subprocess.Popen(
        args, env=environment(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    LAUNCHES[proc.pid] = args[-1]
    OWNED[proc.pid] = (tmp_path, "diagnostics")
    try:
        record = wait_record(tmp_path, "diagnostics", proc)
        base = f"http://127.0.0.1:{record['port']}"
        assert httpx.get(f"{base}/healthz").json() == {"status": "ok"}
        assert httpx.get(f"{base}/readyz").status_code == 503
        status = subprocess.run(
            [sys.executable, "-m", "axiom_mcp.gateway", "status", "--instance", "diagnostics"],
            env=environment(tmp_path),
            capture_output=True,
            timeout=15,
        )
        assert status.returncode == 0
        assert json.loads(status.stdout)["running"] is True
    finally:
        cleanup(proc)
    stdout, stderr = proc.communicate(timeout=5)
    assert b"IncompleteFieldDefinitionWarning" not in stderr
    if json_mode:
        assert len(stdout.splitlines()) == 1
        notice = json.loads(stdout)
        assert notice["status"] == "listening" and notice["url"] == f"{base}/mcp"
        assert "readiness are separate" in notice["scope"]
        assert b"Waiting for clients" not in stderr
    else:
        assert stdout == b""
        for message in [
            "HTTP server listening:",
            f"{base}/healthz",
            f"{base}/readyz",
            "axmp status --instance diagnostics",
            "axmp doctor",
            "Listening does not verify client credentials or graph access",
            "Waiting for clients",
            "Ctrl+C",
        ]:
            assert message.encode() in stderr


def test_inventory_and_bare_stop_select_only_a_unique_running_owner(tmp_path):
    env = environment(tmp_path)

    def call(action, *extra, code=0):
        result = subprocess.run(
            [sys.executable, "-m", "axiom_mcp.command", action, *extra, "--json"],
            env=env,
            capture_output=True,
            timeout=40,
        )
        assert result.returncode == code, result.stdout + result.stderr
        assert len(result.stdout.splitlines()) == 1
        assert result.stderr == b""
        return json.loads(result.stdout)

    assert call("list")["instances"] == []
    assert call("status")["instances"] == []
    assert call("stop")["instance"] is None
    assert not (tmp_path / "gateways").exists()
    first = launch(tmp_path, "workspace")
    second = None
    try:
        record = wait_record(tmp_path, "workspace", first)
        state_path = tmp_path / "gateways/workspace/state.json"
        for _ in range(100):
            record = json.loads(state_path.read_text())
            if record.get("listening"):
                break
            time.sleep(0.05)
        assert record.get("listening")
        record["private-extra"] = "must-never-appear-in-inventory"
        state_path.write_text(json.dumps(record))
        before = state_path.read_bytes()
        inventory = call("list")
        assert inventory["running_count"] == 1
        row = inventory["instances"][0]
        assert row["instance"] == "workspace" and row["pid"] == record["pid"]
        assert row["port"] == record["port"] and row["running"] is True
        assert record["nonce"] not in json.dumps(inventory)
        assert "must-never-appear" not in json.dumps(inventory)
        assert state_path.read_bytes() == before
        stopped = call("stop")
        assert stopped["instance"] == "workspace" and stopped["running"] is False
        assert first.wait(timeout=8) == 0
        assert call("list")["running_count"] == 0
        first = launch(tmp_path, "workspace")
        wait_record(tmp_path, "workspace", first)
        second = launch(tmp_path, "other")
        wait_record(tmp_path, "other", second)
        snapshots = {p: p.read_bytes() for p in (tmp_path / "gateways").glob("*/stop.json")}
        refusal = call("stop", code=6)
        assert "workspace" in refusal["message"] and "other" in refusal["message"]
        assert "--instance" in refusal["message"]
        assert first.poll() is None and second.poll() is None
        assert {p: p.read_bytes() for p in snapshots} == snapshots
        assert not (tmp_path / "gateways/other/stop.json").exists()
        inactive = subprocess.run(
            [sys.executable, "-m", "axiom_mcp.command", "stop", "--instance", "default"],
            env=env,
            capture_output=True,
            timeout=15,
        )
        assert inactive.returncode == 0 and b"No running gateway named default" in inactive.stderr
        assert b"axm mcp list" in inactive.stderr
        assert not (tmp_path / "gateways/default").exists()
        assert first.poll() is None and second.poll() is None
        assert call("stop", "--instance", "workspace")["running"] is False
        assert first.wait(timeout=8) == 0 and second.poll() is None
        assert call("stop")["instance"] == "other"
        assert second.wait(timeout=8) == 0
    finally:
        cleanup(first)
        if second is not None:
            cleanup(second)


def test_inventory_ignores_foreign_records_and_refuses_uncertain_selection(tmp_path):
    env = environment(tmp_path)
    foreign = tmp_path / "gateways/foreign"
    foreign.mkdir(parents=True)
    (foreign / "state.json").write_text('{"document_kind":"someone-else"}')
    broken = tmp_path / "gateways/broken"
    broken.mkdir()
    (broken / "state.json").write_text("broken private record; do not echo")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    for command, code in [("list", 0), ("stop", 6)]:
        result = subprocess.run(
            [sys.executable, "-m", "axiom_mcp.command", command, "--json"],
            env=env,
            capture_output=True,
            timeout=15,
        )
        assert result.returncode == code and result.stderr == b""
        doc = json.loads(result.stdout)
        if command == "list":
            assert doc["unknown_count"] == 1
            assert [r["instance"] for r in doc["instances"]] == ["broken"]
        assert b"private record" not in result.stdout
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_named_instances_duplicate_refusal_crash_release_and_owned_stop(tmp_path):
    env = environment(tmp_path)
    first = launch(tmp_path, "first")
    second = launch(tmp_path, "second")
    try:
        a = wait_record(tmp_path, "first", first)
        b = wait_record(tmp_path, "second", second)
        assert a["port"] != b["port"] and a["idle_timeout_minutes"] == 180
        duplicate = subprocess.run(words("first"), env=env, capture_output=True, timeout=10)
        assert duplicate.returncode == 6 and first.poll() is None
        os.kill(a["pid"], signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
        first.wait(timeout=10)
        first = launch(tmp_path, "first")
        wait_record(tmp_path, "first", first)
        stop = subprocess.run(
            [sys.executable, "-m", "axiom_mcp.gateway", "stop", "--instance", "first"],
            env=env,
            capture_output=True,
            timeout=35,
        )
        assert stop.returncode == 0, stop.stderr.decode()
        first.wait(timeout=10)
        assert first.returncode == 0 and second.poll() is None
    finally:
        cleanup(first)
        cleanup(second)


def test_idle_exit_is_not_kept_alive_by_health_polls(tmp_path):
    proc = launch(tmp_path, "idle", "--idle-timeout-minutes", "0.03")
    try:
        record = wait_record(tmp_path, "idle", proc)
        deadline = time.monotonic() + 8
        while proc.poll() is None and time.monotonic() < deadline:
            try:
                httpx.get(f"http://127.0.0.1:{record['port']}/healthz", timeout=0.2)
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        assert proc.wait(timeout=3) == 0
    finally:
        cleanup(proc)


def test_owner_exit_ends_only_its_gateway(tmp_path):
    owner = subprocess.Popen([sys._base_executable, "-c", "import time; time.sleep(30)"])
    proc = launch(tmp_path, "owned", "--owner-pid", str(owner.pid))
    try:
        wait_record(tmp_path, "owned", proc)
        owner.terminate()
        owner.wait(timeout=5)
        assert proc.wait(timeout=8) == 0
    finally:
        cleanup(owner)
        cleanup(proc)


def test_active_stream_blocks_idle_and_cancellation_releases_accounting():
    assert DEFAULT_IDLE_MINUTES == 180

    async def run():
        activity = Activity(1, last=0)
        started = asyncio.Event()
        release = asyncio.Event()

        async def stream(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            started.set()
            await release.wait()

        async def no_send(message):
            pass

        middleware = ActivityMiddleware(stream, activity)
        task = asyncio.create_task(middleware({"type": "http", "path": "/mcp"}, None, no_send))
        await started.wait()
        assert activity.active == 1 and not activity.expired(100)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert activity.active == 0
        assert activity.expired(activity.last + 1)

    asyncio.run(run())


def test_passive_backchannel_does_not_keep_idle_gateway_alive():
    async def run():
        activity = Activity(1, last=0)
        started, release = asyncio.Event(), asyncio.Event()

        async def backchannel(scope, receive, send):
            started.set()
            await release.wait()

        async def send(message):
            pass

        task = asyncio.create_task(
            ActivityMiddleware(backchannel, activity)(
                {"type": "http", "path": "/mcp", "method": "GET"}, None, send
            )
        )
        await started.wait()
        assert activity.active == 0 and activity.expired(2)
        release.set()
        await task

    asyncio.run(run())


def test_background_start_reuses_owned_configuration_and_stops(tmp_path):
    env = environment(tmp_path)
    arguments = [*words("background")[3:], "--json"]
    arguments[0] = "start"
    start = subprocess.run(
        [sys.executable, "-m", "axiom_mcp.gateway", *arguments],
        env=env,
        capture_output=True,
        timeout=20,
    )
    assert start.returncode == 0, start.stderr.decode()
    first = json.loads(start.stdout)
    try:
        again = subprocess.run(
            [sys.executable, "-m", "axiom_mcp.gateway", *arguments],
            env=env,
            capture_output=True,
            timeout=20,
        )
        assert again.returncode == 0, again.stderr.decode()
        assert json.loads(again.stdout)["pid"] == first["pid"]
        different = [*arguments, "--cache-mib", "1"]
        changed = subprocess.run(
            [sys.executable, "-m", "axiom_mcp.gateway", *different],
            env=env,
            capture_output=True,
            timeout=10,
        )
        assert changed.returncode != 0
        assert json.loads(changed.stdout)["code"] == 6
    finally:
        stop = subprocess.run(
            [sys.executable, "-m", "axiom_mcp.gateway", "stop", "--instance", "background"],
            env=env,
            capture_output=True,
            timeout=35,
        )
        assert stop.returncode == 0, stop.stderr.decode()


def test_invalid_json_arguments_are_one_error_object(tmp_path):
    env = environment(tmp_path)
    for args in [
        ["serve", "--json", "--unknown"],
        ["serve", "--json", "--idle-timeout-minutes", "nan"],
    ]:
        result = subprocess.run(
            [sys.executable, "-m", "axiom_mcp.gateway", *args],
            env=env,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert json.loads(result.stdout)["code"] == 2
        assert len(result.stdout.splitlines()) == 1
