"""Owned local HTTP gateways: named instances, graceful idle/owner exit and supervision."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ctypes
import hashlib
import json
import math
import os
import re
import secrets
import select
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn

from axiom_mcp import http, sdk_compat, security, version
from axiom_mcp.guard.engine import LockMode, load_backend
from axiom_mcp.guard.errors import GuardBusy, GuardError
from axiom_mcp.registry import axiom_home
from axiom_mcp.retention import GenerationRetention
from axiom_mcp.runtime import Runtime

DEFAULT_IDLE_MINUTES = 180.0
PREFERRED_LOCAL_PORT = 8765


class GatewayConflict(ValueError):
    """A named instance or owned metadata conflicts with the requested operation."""


class GatewayOperationError(RuntimeError):
    """A valid gateway operation could not become ready."""


class GatewayParser(argparse.ArgumentParser):
    json_requested = False

    def error(self, message: str) -> None:
        if self.json_requested:
            print(json.dumps(dict(code=2, status="validation_error", message=message)))
            raise SystemExit(2)
        super().error(message)


def failure(args: argparse.Namespace, code: int, message: str) -> int:
    if args.json and not getattr(args, "notice_emitted", False):
        status = {2: "validation_error", 4: "not_ready", 6: "conflict", 7: "busy"}[code]
        print(json.dumps(dict(code=code, status=status, message=message, instance=args.instance)))
    else:
        print(f"axmp: {message}", file=sys.stderr)
    return code


def directory(name: str, *, create: bool = True) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", name):
        raise ValueError("instance must be a portable lowercase name")
    home = axiom_home()
    path = home / "gateways" / name
    for ancestor in (home, home / "gateways", path):
        if ancestor.is_symlink() or ancestor.is_junction():
            raise ValueError("gateway ownership paths cannot be links")
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def read_record(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError("gateway record cannot be a link")
    try:
        if path.stat().st_size > 64 * 1024:
            raise ValueError("gateway metadata exceeds budget")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("gateway record must be an object")
        return value
    except FileNotFoundError:
        return {}


def validate_record_owner(path: Path) -> None:
    if path.is_symlink() or path.is_junction():
        raise ValueError("gateway record cannot be a link")
    if path.exists():
        previous = read_record(path)
        if path.name == "state.json" and previous.get("document_kind") != "axiom-local-gateway":
            raise GatewayConflict("unowned instance metadata was preserved") from None
        if path.name == "stop.json" and (
            set(previous) != {"nonce"}
            or not isinstance(previous.get("nonce"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", previous["nonce"])
        ):
            raise GatewayConflict("unowned stop metadata was preserved")


def write_record(path: Path, value: dict) -> None:
    validate_record_owner(path)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8))
    try:
        with temporary.open("x", encoding="utf-8") as f:
            os.chmod(temporary, 0o600)
            json.dump(value, f)
        # Normal Windows readers briefly deny delete-sharing. Keep the old
        # atomic record intact and recheck ownership before every attempt.
        deadline = time.monotonic() + 1.0
        while True:
            validate_record_owner(path)
            try:
                os.replace(temporary, path)
                break
            except OSError as error:
                remaining = deadline - time.monotonic()
                if (
                    sys.platform != "win32"
                    or getattr(error, "winerror", None) not in (5, 32, 33)
                    or remaining <= 0
                ):
                    raise
                time.sleep(min(0.025, remaining))
    finally:
        if sys.exception() is not None:
            # A cleanup denial must not hide the original publication failure.
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
        else:
            temporary.unlink(missing_ok=True)


class Ownership:
    """Kernel lock, retained lock file; process death releases it without PID guessing."""

    def __init__(self, path: Path):
        if path.is_symlink():
            raise ValueError("gateway lock cannot be a link")
        self.handle = load_backend().open(path)
        try:
            self.handle.acquire(LockMode.EXCLUSIVE)
        except BaseException:
            self.handle.close()
            raise

    def close(self) -> None:
        try:
            self.handle.release()
        finally:
            self.handle.close()


class OwnerWatch:
    """Retain a kernel process identity so later PID reuse cannot extend ownership."""

    def __init__(self, pid: int):
        if pid <= 0:
            raise ValueError("owner PID must be positive")
        self.kind: str
        self.handle: Any
        if sys.platform == "win32":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            self.kernel = kernel
            self.handle = kernel.OpenProcess(0x100000, 0, pid)
            if not self.handle:
                raise GatewayOperationError("owner process cannot be observed")
            self.kind = "windows"
        elif hasattr(os, "pidfd_open"):
            self.handle = os.pidfd_open(pid)
            self.kind = "pidfd"
        elif hasattr(select, "kqueue"):
            self.handle = select.kqueue()
            try:
                self.handle.control(
                    [
                        select.kevent(
                            pid,
                            filter=select.KQ_FILTER_PROC,
                            flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                            fflags=select.KQ_NOTE_EXIT,
                        )
                    ],
                    0,
                    0,
                )
            except BaseException:
                self.handle.close()
                raise
            self.kind = "kqueue"
        else:
            raise ValueError("owner process observation is unsupported on this OS")

    def alive(self) -> bool:
        if self.kind == "windows":
            return self.kernel.WaitForSingleObject(self.handle, 0) == 258
        if self.kind == "pidfd":
            return not select.select([self.handle], [], [], 0)[0]
        return not self.handle.control(None, 1, 0)

    def close(self) -> None:
        if self.kind == "windows":
            self.kernel.CloseHandle(self.handle)
        elif self.kind == "pidfd":
            os.close(self.handle)
        else:
            self.handle.close()


@dataclass
class Activity:
    idle_seconds: float
    max_inflight: int = 8
    last: float = field(default_factory=time.monotonic)
    active: int = 0
    closing: bool = False

    def expired(self, now: float) -> bool:
        return self.idle_seconds > 0 and self.active == 0 and now - self.last >= self.idle_seconds


class ActivityMiddleware:
    def __init__(self, app: Any, activity: Activity):
        self.app, self.activity = app, activity

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") != "/mcp":
            return await self.app(scope, receive, send)
        if self.activity.closing or self.activity.active >= self.activity.max_inflight:
            await send({"type": "http.response.start", "status": 503, "headers": []})
            await send({"type": "http.response.body", "body": b"gateway busy or shutting down"})
            return
        # An idle SSE backchannel is a passive connection, not active tool work.
        if scope.get("method", "POST") != "POST":
            return await self.app(scope, receive, send)
        self.activity.active += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.activity.active -= 1


def signature(args: argparse.Namespace) -> str:
    value = {
        key: getattr(args, key)
        for key in (
            "instance",
            "name",
            "host",
            "port",
            "allow_host",
            "allow_origin",
            "owner_pid",
            "idle_timeout_minutes",
            "cache_mib",
            "cache_entries",
            "max_inflight",
        )
    }
    value.update(
        runtime=str(Path(sys.executable).resolve()),
        version=version.VERSION,
        credentials=os.environ.get("AXIOM_MCP_TOKEN_REGISTRY", ""),
        control=os.environ.get("AXIOM_CONTROL_URL", ""),
        control_reference=os.environ.get("AXIOM_CONTROL_TOKEN_REFERENCE", ""),
    )
    # Only the final private configuration digest is retained. Never log secrets
    # or per-secret hashes; changing a reference's contents must require restart.
    credentials = []
    registry = os.environ.get("AXIOM_MCP_TOKEN_REGISTRY")
    if registry:
        try:
            with Path(registry).open("rb") as stream:
                raw = stream.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError()
            document = json.loads(raw)
            entries = document["tokens"]
            if not isinstance(entries, list):
                raise ValueError()
            if any(not isinstance(e, dict) for e in entries):
                raise ValueError()
            credentials = [
                credential_identity(security.token_reference_from_config(e)) for e in entries
            ]
            value["registry_content"] = hashlib.sha256(raw).hexdigest()
        except (OSError, ValueError, KeyError, TypeError, security.SecurityError):
            raise security.SecurityError(
                "VALIDATION_ERROR",
                "credential registry is unreadable or invalid; existing gateway preserved",
            ) from None
    value["resolved_credentials"] = credentials
    reference = os.environ.get("AXIOM_CONTROL_TOKEN_REFERENCE")
    if reference:
        try:
            value["resolved_control"] = credential_identity(
                security.token_reference_from_config(json.loads(reference))
            )
        except (ValueError, TypeError, AttributeError, security.SecurityError):
            raise security.SecurityError(
                "VALIDATION_ERROR",
                "invalid control credential reference; existing gateway preserved",
            ) from None
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def credential_identity(reference: security.TokenReference) -> str | None:
    """Private fingerprint only; neither secrets nor per-secret digests are reported."""
    if isinstance(reference, security.FileTokenReference):
        try:
            with reference.path.open("rb") as stream:
                raw = stream.read(65537)
        except OSError:
            return None
        if len(raw) > 65536:
            raise ValueError("credential file exceeded limit")
        text = raw.decode("utf-8").splitlines()
        value = (text[0].strip() or None) if text else None
    else:
        value = reference.resolve()
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def inspect(name: str) -> dict:
    root = directory(name, create=False)
    if not (root / "gateway.lock").exists():
        return {"running": False, "instance": name}
    try:
        lock = Ownership(root / "gateway.lock")
    except GuardBusy:
        record = read_record(root / "state.json")
        if record and record.get("document_kind") != "axiom-local-gateway":
            raise GatewayConflict("unowned instance metadata was preserved") from None
        return {**record, "running": True, "instance": name}
    else:
        lock.close()
        return {"running": False, "instance": name}


def instances() -> list[dict]:
    """Observe retained owners in this home without creating instance directories."""
    base = directory("default", create=False).parent
    if not base.exists():
        return []
    rows = []
    for candidate in sorted(base.iterdir()):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", candidate.name):
            continue
        if candidate.is_symlink() or candidate.is_junction() or not candidate.is_dir():
            continue
        if not (candidate / "state.json").exists() and not (candidate / "gateway.lock").exists():
            continue
        try:
            record = read_record(candidate / "state.json")
            if record and record.get("document_kind") != "axiom-local-gateway":
                continue
            observed = inspect(candidate.name)
            rows.append({**record, **observed})
        except (OSError, ValueError, GuardError):
            rows.append(
                dict(instance=candidate.name, running=None, error="ownership_metadata_unavailable")
            )
    return rows


def public_instance(record: dict) -> dict:
    """Never print ownership nonces, signatures or arbitrary metadata fields."""
    result = {"instance": record["instance"], "running": record["running"]}
    for key in ("pid", "port", "idle_timeout_minutes"):
        value = record.get(key)
        if (type(value) is int and abs(value) < 2**63) or (
            type(value) is float and math.isfinite(value)
        ):
            result[key] = value
    for key in ("host", "version", "error"):
        value = record.get(key)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:+-]{1,253}", value):
            result[key] = value
    result["listening"] = record["running"] is True and record.get("listening") is True
    return result


def emit_inventory(records: list[dict], *, json_output: bool) -> None:
    rows = [public_instance(record) for record in records]
    if json_output:
        print(
            json.dumps(
                dict(
                    instances=rows,
                    running_count=sum(r["running"] is True for r in rows),
                    unknown_count=sum(r["running"] is None for r in rows),
                )
            )
        )
        return
    if not rows:
        print("No known MCP gateway instances in this AXIOM_HOME.")
        return
    print("INSTANCE  STATE  PID  HOST:PORT  VERSION  IDLE-MINUTES")
    for row in rows:
        state = {True: "running", False: "stopped", None: "unknown"}[row["running"]]
        print(
            f"{row['instance']}  {state}  {row.get('pid', '-')}  "
            f"{row.get('host', '-')}:{row.get('port', '-')}  "
            f"{row.get('version', '-')}  {row.get('idle_timeout_minutes', '-')}"
        )
    print("Stop one: axm mcp stop --instance NAME")


def bind_socket(args: argparse.Namespace) -> socket.socket:
    """The serving process retains the socket; no probe/close/rebind race."""
    ports = [PREFERRED_LOCAL_PORT, 0] if args.port is None else [args.port]
    # Resolve localhost to a numeric loopback before binding; never trust DNS
    # prefix matching or permit a resolver to select a non-loopback address.
    host = "127.0.0.1" if args.host.lower() == "localhost" else args.host
    for index, port in enumerate(ports):
        try:
            return socket.create_server(
                (host, port),
                family=socket.AF_INET6 if ":" in args.host else socket.AF_INET,
            )
        except OSError as exc:
            import errno

            if index + 1 == len(ports) or exc.errno not in {errno.EADDRINUSE, errno.EACCES}:
                raise
    raise AssertionError("socket selection exhausted")


def effective_policy(args: argparse.Namespace, port: int) -> tuple[list[str], list[str]]:
    """Defaults are exact loopback origins; explicit policies are never expanded."""
    if (not args.allow_host or not args.allow_origin) and not http.is_loopback_host(args.host):
        raise ValueError("non-loopback binds require explicit --allow-host and --allow-origin")
    host = "127.0.0.1" if args.host.lower() == "localhost" else args.host
    authority = f"[{host}]" if ":" in host else host
    authority += f":{port}"
    return args.allow_host or [authority], args.allow_origin or [f"http://{authority}"]


def emit_instance(record: dict, *, json_output: bool) -> None:
    public = public_instance(record)
    if json_output:
        print(json.dumps(public))
        return
    print(f"Axiom MCP {public.get('version', version.VERSION)}")
    if not public.get("running"):
        print(f"Gateway ({public['instance']}): stopped")
        print("Next: axm mcp start")
        return
    state = "listening" if public.get("listening") else "starting"
    authority = public.get("host", "127.0.0.1")
    if ":" in authority:
        authority = f"[{authority}]"
    print(f"Gateway ({public['instance']}): {state}")
    print(f"Endpoint: http://{authority}:{public.get('port', '-')}/mcp")
    print("Graph access: checked separately; run axm mcp doctor")
    print(f"Stop: axm mcp stop --instance {public['instance']}")


async def run_server(args: argparse.Namespace, root: Path) -> None:
    requested_signature = signature(args)
    runtime = Runtime.configured()
    runtime.retention = GenerationRetention(args.cache_mib * 1024 * 1024, args.cache_entries)
    owner = None
    sock = None
    try:
        if signature(args) != requested_signature:
            raise GatewayConflict(
                "credential configuration changed during startup; retry the requested configuration"
            )
        owner = OwnerWatch(args.owner_pid) if args.owner_pid else None
        sock = bind_socket(args)
        sock.setblocking(False)
        actual_port = sock.getsockname()[1]
        allowed_hosts, allowed_origins = effective_policy(args, actual_port)
        server = sdk_compat.build_server(
            args.name, allowed_hosts=allowed_hosts, allowed_origins=allowed_origins
        )
        from axiom_mcp.runtime import compose

        compose(server, runtime, transport="http")
        settings = http.HttpTransportSettings(host=args.host, port=actual_port)
        gateway = http.build_gateway(
            server,
            settings=settings,
            readiness=runtime.readiness,
            security=security.SecurityPolicy.build(
                allowed_hosts=allowed_hosts, allowed_origins=allowed_origins
            ),
            authenticator=runtime.tokens,
        )
        activity = Activity(args.idle_timeout_minutes * 60, args.max_inflight)
        runtime.activity_complete = lambda: setattr(activity, "last", time.monotonic())
        config = http.uvicorn_config(ActivityMiddleware(gateway.app, activity), settings)
        config.timeout_graceful_shutdown = 30
        config.limit_concurrency = args.max_inflight * 2
        worker = uvicorn.Server(config)
        nonce = secrets.token_hex(32)
        record = dict(
            document_kind="axiom-local-gateway",
            schema_version=1,
            pid=os.getpid(),
            instance=args.instance,
            host=args.host,
            port=sock.getsockname()[1],
            version=version.VERSION,
            signature=requested_signature,
            nonce=nonce,
            idle_timeout_minutes=args.idle_timeout_minutes,
            launch_id=args.launch_id,
        )
        write_record(root / "state.json", record)

        async def monitor_loop() -> None:
            while not worker.started and not worker.should_exit:
                await asyncio.sleep(0.05)
            if worker.started:
                write_record(root / "state.json", {**record, "listening": True})
                authority = f"[{args.host}]" if ":" in args.host else args.host
                notice = dict(
                    status="listening",
                    instance=args.instance,
                    url=f"http://{authority}:{record['port']}/mcp",
                    idle_timeout_minutes=args.idle_timeout_minutes,
                    scope="process; query/control readiness are separate",
                )
                if args.json:
                    print(json.dumps(notice), flush=True)
                    args.notice_emitted = True
                else:
                    print(
                        f"Axiom MCP {version.VERSION}\n"
                        f"HTTP server listening: {notice['url']}\n"
                        f"Gateway: {args.instance}; "
                        f"idle timeout={args.idle_timeout_minutes:g} minutes\n"
                        "Foreground mode; this terminal stays open "
                        "while MCP clients connect.\n"
                        f"Process health: {notice['url'].removesuffix('/mcp')}/healthz\n"
                        f"Query/control readiness: "
                        f"{notice['url'].removesuffix('/mcp')}/readyz\n"
                        f"Status: axm mcp status --instance {args.instance}\n"
                        "Listening does not verify client credentials or graph access; "
                        "run axm mcp doctor and an authenticated graph query.\n"
                        "Waiting for clients. Stop this foreground server with Ctrl+C.",
                        file=sys.stderr,
                        flush=True,
                    )
            while not worker.should_exit:
                await asyncio.sleep(0.25)
                try:
                    stop = read_record(root / "stop.json")
                except (ValueError, OSError):
                    stop = {}
                if (
                    stop.get("nonce") == nonce
                    or (owner and not owner.alive())
                    or activity.expired(time.monotonic())
                ):
                    activity.closing = True
                    worker.should_exit = True
                    return

        lifecycle_failed = False

        async def monitor() -> None:
            nonlocal lifecycle_failed
            try:
                await monitor_loop()
            except (OSError, ValueError):
                lifecycle_failed = True
                activity.closing = True
                worker.should_exit = True

        task = asyncio.create_task(monitor())
        try:
            await worker.serve(sockets=[sock])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if lifecycle_failed:
            raise ValueError("gateway lifecycle state is unusable; the owned server stopped")
    finally:
        if sock:
            sock.close()
        if owner:
            owner.close()
        runtime.close()


def parser() -> GatewayParser:
    p = GatewayParser(prog="axmp", description=__doc__)
    p.add_argument("action", choices=["serve", "start", "list", "status", "stop"])
    p.add_argument("--instance", help="exact instance; start/serve default to default")
    p.add_argument("--name", default="axmp")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument(
        "--port", type=int, help="exact port; default tries 8765 then an available loopback port"
    )
    p.add_argument("--allow-host", action="append", default=[])
    p.add_argument("--allow-origin", action="append", default=[])
    p.add_argument("--idle-timeout-minutes", type=float, default=DEFAULT_IDLE_MINUTES)
    p.add_argument("--owner-pid", type=int)
    p.add_argument("--cache-mib", type=int, default=64)
    p.add_argument("--cache-entries", type=int, default=32)
    p.add_argument("--max-inflight", type=int, default=8)
    p.add_argument("--json", action="store_true")
    p.add_argument("--launch-id", default=secrets.token_hex(16), help=argparse.SUPPRESS)
    return p


def main(argv: list[str] | None = None) -> int:
    words = list(sys.argv[1:] if argv is None else argv)
    options = parser()
    options.json_requested = "--json" in words
    args = options.parse_args(words)
    if args.host.lower() == "localhost":
        args.host = "127.0.0.1"
    try:
        if args.action == "list" or (args.action == "status" and args.instance is None):
            if args.action == "list" and args.instance is not None:
                raise ValueError("list takes no --instance; use status --instance NAME")
            emit_inventory(instances(), json_output=args.json)
            return 0
        selected = None
        if args.action == "stop" and args.instance is None:
            observed = instances()
            running = [row for row in observed if row["running"] is True]
            uncertain = [row for row in observed if row["running"] is None]
            if len(running) > 1 or uncertain:
                names = ", ".join(row["instance"] for row in [*running, *uncertain])
                raise GatewayConflict(
                    f"Instance selection required ({names}); nothing was stopped. "
                    "Run axm mcp list, then axm mcp stop --instance NAME"
                )
            if not running:
                message = "No running MCP gateway in this AXIOM_HOME; use axm mcp list to inspect."
                if args.json:
                    print(json.dumps(dict(running=False, instance=None, message=message)))
                else:
                    print(message)
                return 0
            selected = running[0]
            args.instance = selected["instance"]
        if args.instance is None:
            args.instance = "default"
        if args.action in {"status", "stop"}:
            result = inspect(args.instance)
            if selected is not None and any(
                selected.get(key) != result.get(key) for key in ("nonce", "pid", "launch_id")
            ):
                raise GatewayConflict(
                    "Instance owner changed while selecting; retry explicit status"
                )
            was_running = result["running"]
            if args.action == "stop" and was_running:
                if not result.get("nonce"):
                    raise GatewayOperationError("gateway is starting; retry status before stopping")
                root = directory(args.instance, create=False)
                write_record(root / "stop.json", {"nonce": result["nonce"]})
                for _ in range(140):
                    current = inspect(args.instance)
                    if not current["running"] or current.get("nonce") != result["nonce"]:
                        break
                    time.sleep(0.25)
                else:
                    raise TimeoutError("gateway is draining; no foreign process was killed")
                result = inspect(args.instance)
                if result["running"]:
                    raise GatewayConflict(
                        "The original owner exited but a replacement is running; "
                        "the replacement was not stopped. Inspect the instance before retrying"
                    )
            emit_instance(result, json_output=args.json)
            if args.action == "stop" and not args.json:
                print(
                    f"axmp: {'Stop completed for' if was_running else 'No running gateway named'} "
                    f"{args.instance}; use axm mcp list to inspect remaining instances.",
                    file=sys.stderr,
                )
            return 0
        if (
            not math.isfinite(args.idle_timeout_minutes)
            or args.idle_timeout_minutes < 0
            or min(args.cache_mib, args.cache_entries, args.max_inflight) < 1
        ):
            raise ValueError("idle and cache/concurrency budgets are invalid")
        http.HttpTransportSettings(host=args.host, port=0 if args.port is None else args.port)
        # Validate the policy boundary now, derive the real port only after binding.
        effective_policy(args, 0)
        root = directory(args.instance)
        if args.action == "start":
            active = inspect(args.instance)
            if active["running"]:
                if active.get("signature") != signature(args):
                    raise GatewayConflict("instance is owned by another launch configuration")
                emit_instance(active, json_output=args.json)
                return 0
            words = list(sys.argv[1:] if argv is None else argv)
            words[0] = "serve"
            launch_id = secrets.token_hex(16)
            words.extend(["--launch-id", launch_id])
            options: dict[str, Any] = dict(
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            else:
                options["start_new_session"] = True
            isolated = ["-I"] if sys.flags.isolated else []
            proc = subprocess.Popen(
                [sys.executable, *isolated, "-m", "axiom_mcp.gateway", *words], **options
            )
            for _ in range(120):
                if proc.poll() is not None:
                    raise GatewayOperationError(
                        "gateway failed to start; run axmp serve to see diagnostics"
                    )
                active = inspect(args.instance)
                if (
                    active["running"]
                    and active.get("launch_id") == launch_id
                    and active.get("listening")
                ):
                    # Readiness is reported separately; a process lock is not a query proof.
                    emit_instance(active, json_output=args.json)
                    return 0
                time.sleep(0.1)
            raise TimeoutError("gateway startup timed out; inspect axm mcp status before retrying")
        lock = Ownership(root / "gateway.lock")
        try:
            asyncio.run(run_server(args, root))
        finally:
            lock.close()
        return 0
    except KeyboardInterrupt:
        return 0
    except GuardBusy:
        return failure(args, 6, "instance is already running; use another --instance/--port")
    except GatewayConflict as exc:
        return failure(args, 6, str(exc))
    except TimeoutError as exc:
        return failure(args, 7, str(exc))
    except (ValueError, http.HttpTransportError) as exc:
        return failure(args, 2, str(exc))
    except (OSError, GatewayOperationError, security.SecurityError, GuardError) as exc:
        return failure(args, 4, str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
