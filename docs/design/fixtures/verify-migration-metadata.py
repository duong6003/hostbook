#!/usr/bin/env python3
"""Verify restored fixture bytes, mode, ownership and native ACLs before selection."""
from dataclasses import dataclass, replace
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import tempfile


FILE = "hosts.conf"
PAYLOAD = b"Host fixture\n  HostName 192.0.2.10\n"
MAC = sys.platform == "darwin"
OTHER_UID = 61001
OTHER_GID = 61002
SOURCE_UID = 61003
SOURCE_GID = 61004


@dataclass(frozen=True)
class Entry:
    mode: int
    uid: int
    gid: int
    acl: bytes
    contents: bytes


def command(env, *args):
    return subprocess.run(args, env=env, check=True, stdin=subprocess.DEVNULL,
                          capture_output=True, timeout=5).stdout


def snapshot(root, env):
    result = {}
    for name in (".", FILE):
        path = root / name
        info = path.lstat()
        expected_type = stat.S_ISDIR if name == "." else stat.S_ISREG
        if not expected_type(info.st_mode):
            raise ValueError("unexpected fixture entry type")
        if MAC:
            acl = b"\n".join(command(env, "ls", "-led", str(path)).splitlines()[1:])
        else:
            acl = command(env, "getfacl", "-cpn", str(path))
        result[name] = Entry(stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid,
                             acl, path.read_bytes() if name != "." else b"")
    return result


def add_acls(source, env):
    if MAC:
        command(env, "chmod", "+a", "everyone allow read", str(source / FILE))
        command(env, "chmod", "+a", "everyone allow list,search,file_inherit,directory_inherit",
                str(source))
    else:
        command(env, "setfacl", "-m", f"u:{OTHER_UID}:r--", str(source / FILE))
        command(env, "setfacl", "-m", f"u:{OTHER_UID}:r-x,d:u:{OTHER_UID}:r-x", str(source))


def verify_candidate(staged, selected, expected, env):
    try:
        observed = snapshot(staged, env)
    except (OSError, ValueError, subprocess.SubprocessError):
        return "unverified"
    if observed != expected:
        return "mismatch"
    staged.rename(selected)
    return "selected"


def run_case(case):
    with tempfile.TemporaryDirectory(prefix="hostbook-restore-metadata-") as scratch:
        root = Path(scratch).resolve()
        source = root / "original"
        staged = root / "staged"
        selected = root / "selected"
        env = {
            "PATH": "/usr/bin:/bin", "HOME": scratch, "LC_ALL": "C",
            "XDG_CONFIG_HOME": str(root / "config"), "HOSTBOOK_DIR": str(source),
            "HOSTBOOK_STATE": str(root / "state"), "HOSTBOOK_SSH_CONFIG": str(root / "ssh"),
        }
        source.mkdir(mode=0o750)
        (source / FILE).write_bytes(PAYLOAD)
        (source / FILE).chmod(0o640)
        add_acls(source, env)
        if not MAC and case == "complete":
            command(env, "setfacl", "-m", f"u:{os.getuid()}:r--", str(source / FILE))
            os.chown(source / FILE, SOURCE_UID, SOURCE_GID)
        before = snapshot(source, env)
        if MAC:
            assert b"everyone allow read" in before[FILE].acl
            assert b"file_inherit" in before["."].acl
        else:
            assert f"user:{OTHER_UID}:r--".encode() in before[FILE].acl
            assert f"default:user:{OTHER_UID}:r-x".encode() in before["."].acl
        command(env, "cp", "-pR", str(source), str(staged))
        assert snapshot(staged, env) == before

        expected = before.copy()
        verify_env = env
        outcome = "mismatch"
        if case == "complete":
            outcome = "selected"
        elif case == "missing-acl":
            if MAC:
                command(env, "chmod", "-N", str(staged / FILE))
            else:
                command(env, "setfacl", "-b", str(staged / FILE))
            (staged / FILE).chmod(before[FILE].mode)
            actual = snapshot(staged, env)[FILE]
            assert actual != before[FILE] and replace(actual, acl=before[FILE].acl) == before[FILE]
        elif case == "wrong-owner":
            if MAC:
                # Native macOS test has no privilege to chown to another account.
                expected[FILE] = replace(before[FILE], uid=before[FILE].uid + 1)
            else:
                os.chown(staged / FILE, OTHER_UID, -1)
                assert (staged / FILE).stat().st_uid == OTHER_UID
        elif case == "wrong-group":
            if MAC:
                expected[FILE] = replace(before[FILE], gid=before[FILE].gid + 1)
            else:
                os.chown(staged / FILE, -1, OTHER_GID)
                assert (staged / FILE).stat().st_gid == OTHER_GID
        elif case == "unavailable":
            verify_env = dict(env, PATH=str(root / "missing-tools"))
            outcome = "unverified"
        else:
            raise ValueError(case)

        assert verify_candidate(staged, selected, expected, verify_env) == outcome
        assert snapshot(source, env) == before
        if outcome == "selected":
            assert snapshot(selected, env) == before and not staged.exists()
        else:
            assert not selected.exists() and staged.is_dir()


if __name__ == "__main__":
    print(f"Platform: {platform.system()} {platform.release()} ({platform.machine()})", flush=True)
    for case in ("complete", "missing-acl", "wrong-owner", "wrong-group", "unavailable"):
        run_case(case)
        print(f"PASS: {case}", flush=True)
