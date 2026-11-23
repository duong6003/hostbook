#!/usr/bin/env python3
"""Exercise batchpass with an inherited pipe and synthetic recovery data."""
import os
from pathlib import Path
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile


PASSWORD = b"synthetic recovery passphrase"
WRONG = b"incorrect synthetic passphrase"
PAYLOAD = b"synthetic recovery identity\n"
SOURCE = b"original store fixture\n"
SCRIPT = Path(__file__).resolve()


def guarded_exec(tool, args):
    env = os.environb
    visible = [os.fsencode(arg) for arg in sys.argv] + list(env) + list(env.values())
    assert b"AGE_PASSPHRASE" not in env
    assert all(PASSWORD not in value and WRONG not in value and PAYLOAD not in value for value in visible)
    fd = int(env[b"AGE_PASSPHRASE_FD"])
    assert stat.S_ISFIFO(os.fstat(fd).st_mode)
    with (Path(os.environ["TMPDIR"]) / "observed").open("a", encoding="ascii") as log:
        log.write(tool + "\n")
    executable = os.environ["FIXTURE_" + tool.upper()]
    os.execv(executable, [executable, *args])


def run():
    if not (age := shutil.which("age")) or not (plugin := shutil.which("age-plugin-batchpass")):
        raise SystemExit("age and age-plugin-batchpass are required")
    with tempfile.TemporaryDirectory(prefix="hostbook-recovery-fd-") as scratch:
        root = Path(scratch).resolve()
        stub = root / "age-plugin-batchpass"
        command = [sys.executable, "-B", str(SCRIPT), "--exec"]
        stub.write_text("#!/bin/sh\nexec " + shlex.join(command + ["plugin"]) + ' "$@"\n')
        stub.chmod(0o700)
        original = root / "original"
        original.write_bytes(SOURCE)
        env = {
            "PATH": str(root), "HOME": str(root), "TMPDIR": str(root), "XDG_CONFIG_HOME": str(root / "config"),
            "HOSTBOOK_DIR": str(root / "hosts"), "HOSTBOOK_STATE": str(root / "state"),
            "HOSTBOOK_SSH_CONFIG": str(root / "ssh"), "HOSTBOOK_KEYCHAIN": f"file:{root}/keychain",
            "FIXTURE_AGE": age, "FIXTURE_PLUGIN": plugin,
        }

        def batchpass(mode, password, data):
            reader, writer = os.pipe()
            try:
                with os.fdopen(writer, "wb") as pipe:
                    pipe.write(password)
                result = subprocess.run(
                    command + ["age", mode, "-j", "batchpass", *(["-a"] if mode == "-e" else [])],
                    cwd=root, env=dict(env, AGE_PASSPHRASE_FD=str(reader)), pass_fds=(reader,),
                    input=data, capture_output=True, timeout=20,
                )
            finally:
                os.close(reader)
            assert PASSWORD not in result.stderr and WRONG not in result.stderr and PAYLOAD not in result.stderr
            return result

        sealed = batchpass("-e", PASSWORD, PAYLOAD)
        assert sealed.returncode == 0 and sealed.stdout.startswith(b"-----BEGIN AGE ENCRYPTED FILE-----")
        envelope = root / "recovery.age"
        envelope.touch(mode=0o600)
        envelope.write_bytes(sealed.stdout)

        opened = batchpass("-d", PASSWORD, envelope.read_bytes())
        assert opened.returncode == 0 and opened.stdout == PAYLOAD

        for password, ciphertext in ((WRONG, sealed.stdout), (b"", sealed.stdout), (PASSWORD, sealed.stdout[:80])):
            rejected = batchpass("-d", password, ciphertext)
            assert rejected.returncode != 0 and rejected.stdout == b""
            assert original.read_bytes() == SOURCE and envelope.read_bytes() == sealed.stdout

        assert stat.S_IMODE(envelope.stat().st_mode) == 0o600
        assert (root / "observed").read_text().splitlines()[:6] == ["age", "plugin"] * 3
        assert {path.name for path in root.iterdir()} == {"age-plugin-batchpass", "original", "recovery.age", "observed"}
        for path in root.iterdir():
            assert all(secret not in path.read_bytes() for secret in (PASSWORD, WRONG, PAYLOAD))

    print(f"Platform: {platform.system()} {platform.release()} ({platform.machine()})")
    print(subprocess.check_output([age, "--version"], env={}, text=True).strip())
    print("PASS: inherited pipe round-trip with real age and batchpass")
    print("PASS: incorrect password, empty pipe and truncated envelope preserve original and sealed copy")


if __name__ == "__main__":
    if not __debug__:
        raise SystemExit("This fixture requires Python assertions. Remove -O and PYTHONOPTIMIZE.")
    if sys.argv[1:2] == ["--exec"]:
        guarded_exec(sys.argv[2], sys.argv[3:])
    else:
        run()
