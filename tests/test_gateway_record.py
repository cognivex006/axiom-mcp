"""Atomic state publication survives Windows readers without weakening ownership."""

import json
import sys
import threading
import time

import pytest

from axiom_mcp import gateway


def owned(n):
    return {"document_kind": "axiom-local-gateway", "n": n}


def denied(code=5):
    error = PermissionError("record busy")
    error.winerror = code
    return error


@pytest.mark.parametrize("code", [5, 32, 33])
def test_transient_windows_publication_retains_old_record_until_success(
    tmp_path, monkeypatch, code
):
    path = tmp_path / "state.json"
    gateway.write_record(path, owned(0))
    original = path.read_bytes()
    real = gateway.os.replace
    calls = []

    def publish(source, target):
        calls.append(source)
        if len(calls) == 1:
            assert path.read_bytes() == original
            raise denied(code)
        real(source, target)

    monkeypatch.setattr(gateway.sys, "platform", "win32")
    monkeypatch.setattr(gateway.os, "replace", publish)
    gateway.write_record(path, owned(1))
    assert len(calls) == 2 and json.loads(path.read_bytes()) == owned(1)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_persistent_denial_is_bounded_preserves_old_bytes_and_original_error(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    gateway.write_record(path, owned(0))
    original = path.read_bytes()
    error = denied()
    clock = iter([0.0, 0.1, 1.1])

    def publish(*_):
        raise error

    monkeypatch.setattr(gateway.sys, "platform", "win32")
    monkeypatch.setattr(gateway.os, "replace", publish)
    monkeypatch.setattr(gateway.time, "monotonic", lambda: next(clock))
    with pytest.raises(PermissionError) as caught:
        gateway.write_record(path, owned(1))
    assert caught.value is error and path.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


@pytest.mark.parametrize("platform,code", [("linux", 5), ("darwin", 32), ("win32", 87)])
def test_other_errors_are_not_retried(tmp_path, monkeypatch, platform, code):
    path = tmp_path / "state.json"
    error = denied(code)
    calls = []

    def publish(*_):
        calls.append(1)
        raise error

    monkeypatch.setattr(gateway.sys, "platform", platform)
    monkeypatch.setattr(gateway.os, "replace", publish)
    with pytest.raises(PermissionError) as caught:
        gateway.write_record(path, owned(1))
    assert caught.value is error and calls == [1] and not list(tmp_path.iterdir())


def test_owner_changed_during_retry_is_preserved(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    gateway.write_record(path, owned(0))
    calls = []

    def publish(*_):
        calls.append(1)
        path.write_text('{"human":true}')
        raise denied()

    monkeypatch.setattr(gateway.sys, "platform", "win32")
    monkeypatch.setattr(gateway.os, "replace", publish)
    with pytest.raises(gateway.GatewayConflict, match="unowned"):
        gateway.write_record(path, owned(1))
    assert path.read_text() == '{"human":true}' and calls == [1]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_link_changed_during_retry_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    gateway.write_record(path, owned(0))
    original = path.read_bytes()
    real = type(path).is_symlink
    changed = []

    def publish(*_):
        changed.append(True)
        raise denied()

    monkeypatch.setattr(gateway.sys, "platform", "win32")
    monkeypatch.setattr(gateway.os, "replace", publish)
    monkeypatch.setattr(type(path), "is_symlink", lambda p: p == path and bool(changed) or real(p))
    with pytest.raises(ValueError, match="link"):
        gateway.write_record(path, owned(1))
    assert changed == [True] and path.read_bytes() == original


def test_cleanup_failure_does_not_mask_publication_error(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    original_error = OSError("original publication failure")
    real = type(path).unlink

    def publish(*_):
        raise original_error

    def cleanup(p, *args, **kwargs):
        if p.name.startswith("state.json."):
            raise PermissionError("cleanup denied")
        return real(p, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(gateway.os, "replace", publish)
        patch.setattr(type(path), "unlink", cleanup)
        with pytest.raises(OSError) as caught:
            gateway.write_record(path, owned(1))
        assert caught.value is original_error and not path.exists()
    for temporary in tmp_path.iterdir():
        temporary.unlink()


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows delete-sharing semantics")
def test_native_windows_concurrent_readers_observe_only_complete_records(tmp_path):
    path = tmp_path / "state.json"
    gateway.write_record(path, owned(0))
    stop = threading.Event()
    observations = []
    errors = []

    def reader():
        while not stop.is_set():
            try:
                value = json.loads(path.read_bytes())
                assert value["document_kind"] == "axiom-local-gateway"
                observations.append(value["n"])
            except OSError:
                pass
            except BaseException as error:
                errors.append(error)
                break
            stop.wait(0.005)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    try:
        for n in range(40):
            gateway.write_record(path, owned(n))
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=2)
    assert observations and not errors and all(not t.is_alive() for t in threads)
    assert json.loads(path.read_bytes()) == owned(39)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows delete-sharing semantics")
def test_native_reader_handle_can_close_during_publication(tmp_path):
    path = tmp_path / "state.json"
    gateway.write_record(path, owned(0))
    handle = path.open("rb")
    closed = threading.Event()

    def release():
        time.sleep(0.1)
        handle.close()
        closed.set()

    thread = threading.Thread(target=release)
    thread.start()
    try:
        gateway.write_record(path, owned(1))
    finally:
        thread.join(timeout=2)
        handle.close()
    assert closed.is_set() and json.loads(path.read_bytes()) == owned(1)
