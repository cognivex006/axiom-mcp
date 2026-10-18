"""Default local launch reserves its socket and never broadens explicit policy."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from axiom_mcp import gateway, http


def test_native_bare_start_reuses_owned_process_and_advertises_working_endpoint(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("AXIOM_")}
    env.update(
        AXIOM_HOME=str(tmp_path), PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src")
    )
    command = [sys.executable, "-m", "axiom_mcp.gateway"]

    def call(*args):
        return subprocess.run([*command, *args], env=env, capture_output=True, timeout=20)

    try:
        first = call("start", "--host", "localhost", "--json")
        assert first.returncode == 0, first.stderr
        record = json.loads(first.stdout)
        assert record["running"] and record["host"] == "127.0.0.1" and record["port"] > 0
        base = f"http://{record['host']}:{record['port']}"
        response = httpx.post(
            base + "/mcp",
            headers={"Origin": base},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            trust_env=False,
        )
        # The advertised authority passes policy; authentication remains required.
        assert response.status_code == 401
        assert httpx.get(base + "/healthz", trust_env=False).status_code == 200
        second = call("start", "--json")
        assert second.returncode == 0 and json.loads(second.stdout)["pid"] == record["pid"]
        conflict = call("start", "--name", "changed", "--json")
        assert conflict.returncode == 6
        assert call("status", "--instance", "default", "--json").returncode == 0
    finally:
        stopped = call("stop", "--instance", "default", "--json")
        assert stopped.returncode == 0, stopped.stderr


@pytest.mark.parametrize(
    "host",
    ["127.attacker.example", "127.0.0.1.attacker.example", "0.0.0.0", "::", "::ffff:192.168.1.1"],
)
def test_hostnames_and_public_addresses_are_not_loopback(host):
    assert not http.is_loopback_host(host)
    args = gateway.parser().parse_args(["start", "--host", host])
    with pytest.raises(ValueError, match="explicit"):
        gateway.effective_policy(args, 12345)


def test_signature_tracks_registry_and_resolved_secret_rotation(tmp_path, monkeypatch):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"tokens": [{"kind": "env", "variable": "UX_TEST_TOKEN"}]}))
    monkeypatch.setenv("AXIOM_MCP_TOKEN_REGISTRY", str(registry))
    monkeypatch.setenv("UX_TEST_TOKEN", "first-test-secret")
    args = gateway.parser().parse_args(["start"])
    first = gateway.signature(args)
    monkeypatch.setenv("UX_TEST_TOKEN", "second-test-secret")
    second = gateway.signature(args)
    monkeypatch.delenv("UX_TEST_TOKEN")
    assert len({first, second, gateway.signature(args)}) == 3
    registry.write_text("malformed")
    with pytest.raises(gateway.security.SecurityError, match="registry"):
        gateway.signature(args)


def test_requested_server_name_changes_configuration_identity():
    a = gateway.parser().parse_args(["start", "--name", "one"])
    b = gateway.parser().parse_args(["start", "--name", "two"])
    assert gateway.signature(a) != gateway.signature(b)


def test_bare_start_requests_automatic_local_configuration():
    args = gateway.parser().parse_args(["start"])
    assert args.host == "127.0.0.1"
    assert args.port is None
    assert args.allow_host == [] and args.allow_origin == []


@pytest.mark.parametrize("port", ["-1", "65536"])
@pytest.mark.parametrize("json_output", [False, True])
def test_invalid_port_is_validation_without_creating_instance(
    port, json_output, monkeypatch, capsys
):
    def unexpected_directory(*args, **kwargs):
        pytest.fail("invalid launch must not create an instance")

    monkeypatch.setattr(gateway, "directory", unexpected_directory)
    words = ["start", "--port", port] + (["--json"] if json_output else [])
    assert gateway.main(words) == 2
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    if json_output:
        assert json.loads(captured.out)["code"] == 2
    else:
        assert "port" in captured.err.lower()


def test_busy_preferred_port_is_not_closed_or_taken_over(monkeypatch):
    with socket.create_server(("127.0.0.1", 0)) as foreign:
        preferred = foreign.getsockname()[1]
        monkeypatch.setattr(gateway, "PREFERRED_LOCAL_PORT", preferred)
        args = gateway.parser().parse_args(["start"])
        with gateway.bind_socket(args) as selected:
            assert selected.getsockname()[1] != preferred
            assert foreign.fileno() >= 0
            hosts, origins = gateway.effective_policy(args, selected.getsockname()[1])
            assert hosts == [f"127.0.0.1:{selected.getsockname()[1]}"]
            assert origins == [f"http://127.0.0.1:{selected.getsockname()[1]}"]


def test_explicit_busy_port_refuses_without_ephemeral_fallback():
    with socket.create_server(("127.0.0.1", 0)) as foreign:
        args = gateway.parser().parse_args(["start", "--port", str(foreign.getsockname()[1])])
        with pytest.raises(OSError):
            gateway.bind_socket(args)
        assert foreign.fileno() >= 0


@pytest.mark.skipif(os.name != "nt", reason="native Windows exclusive socket semantics")
def test_exclusive_foreign_listener_cannot_block_automatic_port_selection(monkeypatch):
    with socket.socket() as foreign:
        foreign.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        foreign.bind(("127.0.0.1", 0))
        foreign.listen()
        preferred = foreign.getsockname()[1]
        monkeypatch.setattr(gateway, "PREFERRED_LOCAL_PORT", preferred)
        args = gateway.parser().parse_args(["start"])
        with gateway.bind_socket(args) as selected:
            assert selected.getsockname()[1] != preferred
            assert foreign.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1


def test_automatic_defaults_never_create_public_or_wildcard_origins():
    args = gateway.parser().parse_args(["start", "--host", "0.0.0.0"])
    with pytest.raises(ValueError, match="explicit"):
        gateway.effective_policy(args, 12345)
    args = gateway.parser().parse_args(
        ["start", "--allow-host", "127.0.0.1:9999", "--allow-origin", "http://127.0.0.1:9999"]
    )
    assert gateway.effective_policy(args, 12345) == (
        ["127.0.0.1:9999"],
        ["http://127.0.0.1:9999"],
    )


def test_human_state_is_concise_and_never_exposes_owned_secrets(capsys):
    record = dict(
        running=True,
        listening=True,
        instance="default",
        host="127.0.0.1",
        port=12345,
        nonce="private-nonce",
        launch_id="private-launch",
        signature="private-signature",
    )
    gateway.emit_instance(record, json_output=False)
    text = capsys.readouterr().out
    assert "Axiom MCP" in text and "listening" in text
    assert "http://127.0.0.1:12345/mcp" in text
    assert "private-" not in text
    gateway.emit_instance(record, json_output=True)
    assert "private-" not in capsys.readouterr().out
