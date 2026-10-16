"""The axmp MCP server, gateway and diagnostic entrypoint."""

from __future__ import annotations

import sys
from collections.abc import Sequence

USAGE = """axmp - Axiom MCP Server / Gateway

USAGE: axmp [serve|stdio|http|version|doctor|update|entrypoints] [options]

COMMANDS:
  serve    local HTTP gateway; named instance, owner and three-hour idle lifecycle
  stdio    host-owned MCP child process (default)
  http     local Streamable HTTP gateway; explicit Host/Origin allowlists required
  version  component/runtime versions
  doctor   credential and graph-readiness diagnostics
  update   check availability or delegate an approved update plan
  entrypoints  inspect and manage locked native launch plans

Run axmp <command> --help for command options.
"""


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in ("--help", "-h"):
        print(USAGE)
        return 0
    if arguments and arguments[0] in ("serve", "start", "status", "stop"):
        from axiom_mcp.gateway import main as run_gateway

        return run_gateway(list(arguments))
    if not arguments or arguments[0] == "stdio":
        from axiom_mcp.stdio import main as run

        return run(arguments[1:] if arguments else [])
    if arguments[0] == "http":
        from axiom_mcp.http import main as run

        return run(arguments[1:])
    if arguments[0] in ("version", "doctor", "update"):
        from axiom_mcp.cli import main as run

        return run(arguments)
    if arguments[0] == "entrypoints":
        from axiom_mcp.entrypoints import main as run

        return run(arguments[1:])
    print(
        "axmp: expected serve, stdio, http, version, doctor, update or entrypoints; "
        "use axmp --help",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
