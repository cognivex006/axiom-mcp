"""Startup works under strict warnings and explains waiting without corrupting protocol."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from axiom_mcp import stdio


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_fresh_sdk_construction_has_resolved_lifespan_and_preserves_validation(transport):
    root = Path(__file__).resolve().parents[1]
    script = """
import warnings
from contextlib import asynccontextmanager
from pydantic import ValidationError
from mcp.server.fastmcp.server import Settings
from axiom_mcp import sdk_compat, stdio
warnings.simplefilter('error')
if TRANSPORT == 'stdio':
    @asynccontextmanager
    async def lifespan(server):
        yield {'startup': 'preserved'}
    server = stdio.build_stdio_server('strict-startup', lifespan=lifespan)
    assert server.settings.lifespan is lifespan
else:
    server = sdk_compat.build_server('strict-startup')
assert Settings.__pydantic_complete__
assert server.settings.host == '127.0.0.1'
try:
    stdio.build_stdio_server('invalid-settings', port='invalid')
except ValidationError:
    pass
else:
    raise AssertionError('settings validation was bypassed')
try:
    warnings.warn('unrelated warning remains visible', UserWarning)
except UserWarning:
    pass
else:
    raise AssertionError('warnings were globally suppressed')
print('PASS')
""".replace("TRANSPORT", repr(transport))
    env = dict(os.environ, PYTHONPATH=str(root / "src"), PYTHONUTF8="1")
    env = {k: v for k, v in env.items() if not k.startswith(("FASTMCP_", "AXIOM_"))}
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "PASS" and result.stderr == ""


def test_stdio_banner_explains_client_waiting_and_separate_readiness():
    banner = "\n".join(stdio.banner_lines())
    assert "Waiting for MCP client input on stdin" in banner
    assert "until EOF" in banner and "Ctrl+C" in banner
    assert "axmp serve" in banner and "axmp doctor" in banner
    assert "No client or graph query is verified yet" in banner
