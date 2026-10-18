"""Doctor observes only a unique managed loopback /readyz, never process health."""

import http.server
import json
import threading

import pytest

from axiom_mcp import cli, gateway


@pytest.mark.parametrize(
    "query,control", [(True, False), (False, True), (True, True), (False, False)]
)
def test_real_managed_probe_preserves_plane_separation(monkeypatch, query, control):
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            body = json.dumps(
                {"query": {"available": query}, "control": {"available": control}}
            ).encode()
            self.send_response(200 if query and control else 503)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(
        gateway,
        "instances",
        lambda: [dict(running=True, host="127.0.0.1", port=server.server_port)],
    )
    try:
        report = cli.managed_readiness()
        assert report.query.available is query and report.control.available is control
        assert requests == [("/readyz", None)]
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [dict(running=None)],
        [dict(running=True), dict(running=True)],
        [dict(running=True, host="127.attacker.example", port=12345)],
        [dict(running=True, host="192.168.1.1", port=12345)],
    ],
)
def test_ambiguous_or_nonloopback_instances_cannot_claim_readiness(monkeypatch, rows):
    monkeypatch.setattr(gateway, "instances", lambda: rows)
    report = cli.managed_readiness()
    assert not report.query.available and not report.control.available
