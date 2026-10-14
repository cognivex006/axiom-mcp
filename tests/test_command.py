"""Canonical command routing keeps the actual gateway and diagnostic semantics."""

from __future__ import annotations

import json
from unittest.mock import patch

from axiom_mcp.command import main


def test_version_json_uses_actual_cli_and_keeps_component_identity(capsys):
    assert main(["version", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["component"] == "axiom-mcp"
    assert report["version"]


def test_help_and_unknown_command_do_not_start_a_server(capsys):
    assert main(["--help"]) == 0
    assert "axmp - Axiom MCP" in capsys.readouterr().out
    assert main(["not-a-command"]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "axmp:" in output.err


def test_default_and_explicit_stdio_forward_to_the_existing_transport():
    with patch("axiom_mcp.stdio.main", return_value=7) as run:
        assert main([]) == 7
        run.assert_called_once_with([])
    with patch("axiom_mcp.stdio.main", return_value=4) as run:
        assert main(["stdio", "--name", "selected"]) == 4
        run.assert_called_once_with(["--name", "selected"])


def test_http_keeps_transport_arguments_and_exit_code():
    with patch("axiom_mcp.http.main", return_value=5) as run:
        assert main(["http", "--allow-host", "127.0.0.1:8765"]) == 5
        run.assert_called_once_with(["--allow-host", "127.0.0.1:8765"])


def test_entrypoints_preserves_locked_launch_options_and_exit_code():
    with patch("axiom_mcp.entrypoints.main", return_value=6) as run:
        assert main(["entrypoints", "--help"]) == 6
        run.assert_called_once_with(["--help"])
