"""Owned local HTTP gateways: named instances, graceful idle/owner exit and supervision."""

from __future__ import annotations

import argparse
import asyncio
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


def directory(name: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", name):
        raise ValueError("instance must be a portable lowercase name")
    home = axiom_home()
    path = home / "gateways" / name
    for ancestor in (home, home / "gateways", path):
        if ancestor.is_symlink():
            raise ValueError("gateway ownership paths cannot be links")
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


def write_record(path: Path, value: dict) -> None:
    if path.exists():
        previous = read_record(path)
        if path.name == "state.json" and previous.get("document_kind") != "axiom-local-gateway":
            raise GatewayConflict("unowned instance metadata was preserved")
        if path.name == "stop.json" and (
            set(previous) != {"nonce"}
            or not isinstance(previous.get("nonce"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", previous["nonce"])
        ):
            raise GatewayConflict("unowned stop metadata was preserved")
    temporary = path.with_name(path.name + "." + secrets.token_hex(8))
    try:
        with temporary.open("x", encoding="utf-8") as f:
            os.chmod(temporary, 0o600)
            json.dump(value, f)
        os.replace(temporary, path)
    finally:
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
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def inspect(name: str) -> dict:
    root = directory(name)
    try:
        lock = Ownership(root / "gateway.lock")
    except GuardBusy:
        record = read_record(root / "state.json")
        return {**record, "running": True, "instance": name}
    else:
        lock.close()
        return {"running": False, "instance": name}


async def run_server(args: argparse.Namespace, root: Path) -> None:
    runtime = Runtime.configured()
    runtime.retention = GenerationRetention(args.cache_mib * 1024 * 1024, args.cache_entries)
    owner = None
    sock = None
    try:
        owner = OwnerWatch(args.owner_pid) if args.owner_pid else None
        server = sdk_compat.build_server(
            args.name, allowed_hosts=args.allow_host, allowed_origins=args.allow_origin
        )
        from axiom_mcp.runtime import compose

        compose(server, runtime, transport="http")
        settings = http.HttpTransportSettings(host=args.host, port=args.port)
        gateway = http.build_gateway(
            server,
            settings=settings,
            readiness=runtime.readiness,
            security=security.SecurityPolicy.build(
                allowed_hosts=args.allow_host, allowed_origins=args.allow_origin
            ),
            authenticator=runtime.tokens,
        )
        activity = Activity(args.idle_timeout_minutes * 60, args.max_inflight)
        runtime.activity_complete = lambda: setattr(activity, "last", time.monotonic())
        config = http.uvicorn_config(ActivityMiddleware(gateway.app, activity), settings)
        config.timeout_graceful_shutdown = 30
        config.limit_concurrency = args.max_inflight * 2
        worker = uvicorn.Server(config)
        sock = socket.create_server(
            (args.host, args.port), family=socket.AF_INET6 if ":" in args.host else socket.AF_INET
        )
        sock.setblocking(False)
        nonce = secrets.token_hex(32)
        record = dict(
            document_kind="axiom-local-gateway",
            schema_version=1,
            pid=os.getpid(),
            instance=args.instance,
            host=args.host,
            port=sock.getsockname()[1],
            version=version.VERSION,
            signature=signature(args),
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
                notice = dict(
                    status="listening",
                    instance=args.instance,
                    url=f"http://{args.host}:{record['port']}/mcp",
                    idle_timeout_minutes=args.idle_timeout_minutes,
                    scope="process; query/control readiness are separate",
                )
                if args.json:
                    print(json.dumps(notice), flush=True)
                    args.notice_emitted = True
                else:
                    print(
                        f"axmp: {notice['url']} (instance {args.instance}, "
                        f"idle {args.idle_timeout_minutes:g} minutes)",
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
    p.add_argument("action", choices=["serve", "start", "status", "stop"])
    p.add_argument("--instance", default="default")
    p.add_argument("--name", default="axmp")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
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
    try:
        root = directory(args.instance)
        if args.action in {"status", "stop"}:
            result = inspect(args.instance)
            if args.action == "stop" and result["running"]:
                if not result.get("nonce"):
                    raise GatewayOperationError("gateway is starting; retry status before stopping")
                write_record(root / "stop.json", {"nonce": result["nonce"]})
                for _ in range(140):
                    if not inspect(args.instance)["running"]:
                        break
                    time.sleep(0.25)
                else:
                    raise TimeoutError("gateway is draining; no foreign process was killed")
                result = inspect(args.instance)
            result.pop("nonce", None)
            print(json.dumps(result))
            return 0
        if (
            not math.isfinite(args.idle_timeout_minutes)
            or args.idle_timeout_minutes < 0
            or min(args.cache_mib, args.cache_entries, args.max_inflight) < 1
        ):
            raise ValueError("idle and cache/concurrency budgets are invalid")
        http.HttpTransportSettings(host=args.host, port=args.port)
        if not args.allow_host or not args.allow_origin:
            raise ValueError("--allow-host and --allow-origin are required")
        if args.action == "start":
            active = inspect(args.instance)
            if active["running"]:
                if active.get("signature") != signature(args):
                    raise GatewayConflict("instance is owned by another launch configuration")
                active.pop("nonce", None)
                print(json.dumps(active))
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
                    active.pop("nonce", None)
                    print(json.dumps(active))
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
    except ValueError as exc:
        return failure(args, 2, str(exc))
    except (OSError, GatewayOperationError, security.SecurityError, GuardError) as exc:
        return failure(args, 4, str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
