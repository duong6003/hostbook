#!/usr/bin/env python3
"""Exercise process death and filesystem errors at isolated local cutover boundaries."""
from contextlib import nullcontext
import errno
import fcntl
import json
import os
from pathlib import Path
import platform
import selectors
import signal
import subprocess
import sys
import tempfile
from unittest.mock import patch


OLD = {"checkout": "original", "generation": 1}
NEW = {"checkout": "candidate", "generation": 2}
STORE_BYTES = {"original": b"legacy fixture\n", "candidate": b"prepared fixture\n"}
RECOVERY_BYTES = b"synthetic retained recovery marker\n"
SCRIPT = Path(__file__).resolve()


def sync_fd(fd):
    if sys.platform == "darwin":
        operation = getattr(fcntl, "F_FULLFSYNC", None)
        if operation is None:
            raise OSError(errno.ENOTSUP, "F_FULLFSYNC is unavailable")
        fcntl.fcntl(fd, operation)
    else:
        os.fsync(fd)


def sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        sync_fd(fd)
    finally:
        os.close(fd)


def write_file(path, contents):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(contents)
        stream.flush()
        sync_fd(stream.fileno())


def write_json(path, value):
    write_file(path, json.dumps(value).encode("utf-8"))


def prepare(root):
    for checkout, contents in STORE_BYTES.items():
        folder = root / checkout
        folder.mkdir(mode=0o700)
        write_file(folder / "hosts.fixture", contents)
        sync_directory(folder)
    write_file(root / "recovery.fixture", RECOVERY_BYTES)
    write_json(root / "registry.json", OLD)
    sync_directory(root)


def verify_store_files(root):
    for checkout, contents in STORE_BYTES.items():
        assert (root / checkout / "hosts.fixture").read_bytes() == contents
    assert (root / "recovery.fixture").read_bytes() == RECOVERY_BYTES


def injected_sync_error(error_number):
    if error_number is None:
        return nullcontext()
    return patch.object(sys.modules[__name__], "sync_fd",
                        side_effect=OSError(error_number, "fixture sync error"))


def apply_cutover(root, replace, fault=None, error_number=errno.EIO):
    verify_store_files(root)
    assert json.loads((root / "registry.json").read_text()) == OLD
    write_json(root / "intent.json", {"before": OLD, "after": NEW})
    sync_directory(root)
    pending = root / "registry.pending"
    replacement_started = False
    try:
        with injected_sync_error(error_number if fault == "pending-file" else None):
            write_json(pending, NEW)
        sync_directory(root)
        if replace:
            replacement_started = True
            fail_replace = patch.object(os, "replace", side_effect=OSError(errno.EIO, "fixture replace error"))
            with fail_replace if fault == "replace" else nullcontext():
                os.replace(pending, root / "registry.json")
            with injected_sync_error(error_number if fault == "registry-directory" else None):
                sync_directory(root)
    except OSError:
        return "uncertain" if replacement_started else "stopped-before-replace"
    return "replaced" if replacement_started else "pending"


def cutover(root, when):
    assert when in ("before", "after")
    assert apply_cutover(root, replace=when == "after") == ("replaced" if when == "after" else "pending")
    print("at-boundary", flush=True)
    # Keep the child alive without adding a completion record.
    sys.stdin.buffer.read(1)
    raise RuntimeError("fixture writer was not interrupted")


def restart(root):
    intent = json.loads((root / "intent.json").read_text())
    selected = json.loads((root / "registry.json").read_text())
    assert intent == {"before": OLD, "after": NEW}
    assert selected in (intent["before"], intent["after"])
    verify_store_files(root)
    print("selected-old" if selected == intent["before"] else "selected-new")


def snapshot(root):
    contents = {}
    for path in root.rglob("*"):
        if path.is_file():
            metadata = path.stat()
            contents[path.relative_to(root)] = (
                metadata.st_ino, metadata.st_mtime_ns, metadata.st_ctime_ns, path.read_bytes(),
            )
    return contents


def check(when, selected):
    with tempfile.TemporaryDirectory(prefix="hostbook-cutover-") as scratch:
        root = Path(scratch).resolve()
        env = {
            "HOME": str(root), "XDG_CONFIG_HOME": str(root / "config"),
            "HOSTBOOK_DIR": str(root / "original"), "HOSTBOOK_STATE": str(root),
            "HOSTBOOK_SSH_CONFIG": str(root / "ssh"),
        }
        command = [sys.executable, "-B", str(SCRIPT)]
        prepare(root)
        with subprocess.Popen(command + ["cutover", when], env=env, cwd=root,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE) as child:
            try:
                with selectors.DefaultSelector() as ready:
                    ready.register(child.stdout, selectors.EVENT_READ)
                    assert ready.select(timeout=5)
                    assert child.stdout.readline() == b"at-boundary\n"
            finally:
                child.kill()
                child.wait(timeout=5)
            assert child.returncode == -signal.SIGKILL

        assert (root / "registry.pending").exists() == (when == "before")
        before = snapshot(root)
        for _ in range(2):
            reopened = subprocess.run(command + ["restart"], env=env, cwd=root,
                                      check=True, capture_output=True, text=True, timeout=5)
            assert reopened.stdout.strip() == selected
            assert snapshot(root) == before


def check_cutover_error(fault, observed, outcome, error_number=errno.EIO):
    with tempfile.TemporaryDirectory(prefix="hostbook-cutover-error-") as scratch:
        root = Path(scratch).resolve()
        prepare(root)
        assert apply_cutover(root, replace=True, fault=fault, error_number=error_number) == outcome
        assert json.loads((root / "registry.json").read_text()) == observed
        assert (root / "registry.pending").exists() == (observed == OLD)
        assert json.loads((root / "intent.json").read_text()) == {"before": OLD, "after": NEW}
        verify_store_files(root)


if __name__ == "__main__":
    if sys.argv[1:] == []:
        print(f"Platform: {platform.system()} {platform.release()} ({platform.machine()})", flush=True)
        check("before", "selected-old")
        print("PASS: SIGKILL before replacement kept the original selection")
        check("after", "selected-new")
        print("PASS: SIGKILL after synced replacement kept the new selection and did not replay")
        check_cutover_error("pending-file", OLD, "stopped-before-replace")
        print("PASS: pending file EIO stopped before replacement")
        check_cutover_error("registry-directory", NEW, "uncertain")
        print("PASS: directory EIO after replacement left new binding visible and outcome uncertain")
        check_cutover_error("replace", OLD, "uncertain")
        print("PASS: failed replacement attempt was uncertain even with old binding visible")
        check_cutover_error("pending-file", OLD, "stopped-before-replace", errno.ENOTSUP)
        check_cutover_error("registry-directory", NEW, "uncertain", errno.ENOTSUP)
        print("PASS: unsupported sync blocked completion on both sides of replacement")
    else:
        root = Path.cwd()
        assert root == Path(os.environ["HOSTBOOK_STATE"])
        if sys.argv[1] == "cutover" and len(sys.argv) == 3:
            cutover(root, sys.argv[2])
        elif sys.argv[1:] == ["restart"]:
            restart(root)
        else:
            raise SystemExit("unknown fixture phase")
