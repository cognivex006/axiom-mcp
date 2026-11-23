"""`axmp setup`: one-step local read credential (field report 2026-11-23)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from axiom_mcp import cli, security
from axiom_mcp import local_credentials as lc


def _env(home: Path) -> dict[str, str]:
    return {"AXIOM_HOME": str(home), "USERPROFILE": str(home), "HOME": str(home)}


def test_plan_changes_nothing(tmp_path: Path) -> None:
    setup = lc.plan(_env(tmp_path))
    assert setup.changes
    assert not setup.token_file.exists()
    assert not setup.registry_file.exists()
    assert "Nothing was changed" in "\n".join(lc.render(setup, applied=False))


def test_apply_creates_private_credential_and_reference_only_registry(tmp_path: Path) -> None:
    env = _env(tmp_path)
    lc.apply(lc.plan(env))
    token = lc.default_token_file(tmp_path).read_text(encoding="utf-8").strip()
    assert len(token) >= 40
    document = json.loads(lc.default_registry_file(tmp_path).read_text(encoding="utf-8"))
    entry = document["tokens"][0]
    assert entry["kind"] == "file" and entry["capabilities"] == ["read"]
    assert entry["audience"] == security.MCP_AUDIENCE
    assert token not in lc.default_registry_file(tmp_path).read_text(encoding="utf-8")
    assert all(token not in line for line in lc.render(lc.plan(env), applied=True))


def test_rerun_keeps_the_existing_credential(tmp_path: Path) -> None:
    env = _env(tmp_path)
    lc.apply(lc.plan(env))
    first = lc.default_token_file(tmp_path).read_bytes()
    again = lc.plan(env)
    assert not again.changes
    lc.apply(again)
    assert lc.default_token_file(tmp_path).read_bytes() == first


def test_defaults_are_used_only_when_env_is_unset(tmp_path: Path) -> None:
    env = _env(tmp_path)
    assert lc.effective_registry_path(env) is None
    assert lc.effective_reference(env) is None
    lc.apply(lc.plan(env))
    assert lc.effective_registry_path(env) == str(lc.default_registry_file(tmp_path))
    reference = json.loads(lc.effective_reference(env) or "{}")
    assert reference == {"kind": "file", "path": str(lc.default_token_file(tmp_path))}
    explicit = {**env, "AXIOM_MCP_TOKEN_REGISTRY": "C:/explicit.json"}
    assert lc.effective_registry_path(explicit) == "C:/explicit.json"


def test_doctor_sees_the_setup_credential_without_env(tmp_path: Path) -> None:
    env = _env(tmp_path)
    assert cli.credential_scope(env).reason == "credential_reference_not_configured"
    lc.apply(lc.plan(env))
    scope = cli.credential_scope(env)
    assert scope.configured and scope.resolved
    assert scope.reason != "credential_reference_not_configured"


def test_unknown_option_is_validation(capsys: pytest.CaptureFixture[str]) -> None:
    assert lc.main(["--bogus"]) == 2
    assert "unknown option" in capsys.readouterr().err


def test_daemon_shaped_registry_names_the_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "registry.json").write_text(
        '{"storage": {"journalMode": "DELETE"}}', encoding="utf-8"
    )
    for key, value in _env(tmp_path).items():
        monkeypatch.setenv(key, value)
    assert lc.main([]) == 6
    err = capsys.readouterr().err
    assert "daemon settings" in err and "axge solution register" in err
