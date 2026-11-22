#!/usr/bin/env python3
"""Check scoped OpenSSH resolution and model stale selection without a server."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile


def run():
    ssh = shutil.which("ssh")
    if ssh is None:
        raise SystemExit("OpenSSH is required")
    with tempfile.TemporaryDirectory(prefix="hostbook-ssh-", dir="/tmp") as scratch:
        root = Path(scratch).resolve()
        private = root / "private"
        shared = root / "Dev Secrets"
        private.mkdir()
        shared.mkdir()
        private_hosts = private / "hosts.conf"
        shared_hosts = shared / "hosts.conf"
        private_hosts.write_text(
            "Host db\n  HostName 192.0.2.10\n  User private-db\n  ProxyJump relay\n"
            "Host relay\n  HostName 192.0.2.1\n"
        )
        shared_hosts.write_text(
            "Host db\n  HostName 198.51.100.10\n  User shared-db\n  ProxyJump relay\n"
            "Host relay\n  HostName 198.51.100.1\n  ProxyJump corp-relay\n"
            "Host *\n  User shared-default\n  Port 2222\n"
        )
        local = root / "approved.conf"
        local.write_text("Host corp-relay\n  HostName 203.0.113.1\n  User approved-hop\n")
        known_hosts = root / "known_hosts"
        known_hosts.touch()
        no_auth = (
            'Host *\n  IdentityAgent none\n  IdentityFile none\n'
            '  ControlMaster no\n  ControlPath none\n'
            '  GlobalKnownHostsFile none\n  StrictHostKeyChecking yes\n'
            f'  UserKnownHostsFile "{known_hosts}"\n'
        )
        scoped = root / "selected.conf"
        scoped.write_text(f'Include "{local}" "{shared_hosts}"\n' + no_auth)
        aggregate = root / "aggregate.conf"
        aggregate.write_text(f'Include "{private_hosts}" "{shared_hosts}"\n' + no_auth)

        args_log = root / "proxy-args"
        stub = root / "ssh"
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$FIXTURE_PROXY_ARGS"\nexit 255\n')
        stub.chmod(0o700)
        (root / "home").mkdir()
        env = {
            "PATH": f"{root}:/usr/bin:/bin", "HOME": str(root / "home"),
            "XDG_CONFIG_HOME": str(root / "xdg"), "HOSTBOOK_DIR": str(shared),
            "HOSTBOOK_STATE": str(root / "state"), "HOSTBOOK_SSH_CONFIG": str(scoped),
            "FIXTURE_PROXY_ARGS": str(args_log), "LC_ALL": "C",
        }

        def call(config, *args, check=True):
            return subprocess.run(
                ["ssh", "-F", str(config), *args], executable=ssh, env=env,
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5, check=check,
            )

        def fields(config, alias):
            wanted = {"hostname", "user", "port", "proxyjump"}
            values = (line.split(" ", 1) for line in call(config, "-G", alias).stdout.splitlines())
            return {key: value for key, value in values if key in wanted}

        assert fields(aggregate, "db")["hostname"] == "192.0.2.10"
        assert fields(scoped, "db") == {
            "hostname": "198.51.100.10", "user": "shared-db", "port": "2222", "proxyjump": "relay",
        }
        assert fields(scoped, "relay")["hostname"] == "198.51.100.1"
        assert fields(scoped, "relay")["proxyjump"] == "corp-relay"
        assert fields(scoped, "corp-relay")["hostname"] == "203.0.113.1"

        # OpenSSH launches the synthetic proxy child. The stub captures its argv and opens no socket.
        result = call(scoped, "-o", "BatchMode=yes", "db", check=False)
        assert result.returncode == 255
        args = args_log.read_text().splitlines()
        assert args[args.index("-F") + 1] == str(scoped)
        assert args[args.index("-W") + 1] == "[198.51.100.10]:2222"
        assert args[-1] == "relay"

        # Model the #37 resolver before handing any configuration to SSH.
        key = ("dev-secrets", "shared-db-id")
        snapshot = (1, 1, 1, shared_hosts.read_bytes())
        active = {key: snapshot, ("private", "private-db-id"): (1, 1, 1, private_hosts.read_bytes())}

        def resolve(captured_key, expected):
            return scoped if active.get(captured_key) == expected else None

        assert resolve(key, snapshot) == scoped
        active[key] = (1, 1, 2, snapshot[-1])
        assert resolve(key, snapshot) is None
        shared_hosts.write_text(shared_hosts.read_text().replace("198.51.100.10", "198.51.100.11"))
        active[key] = (1, 1, 1, shared_hosts.read_bytes())
        assert resolve(key, snapshot) is None
        del active[key]
        assert resolve(key, snapshot) is None

        print(call(scoped, "-V").stderr.strip())
        print("PASS: scoped target, wildcard defaults, jump configuration and proxy argv")
        print("PASS: model rejects connection change, config change and disconnect without alias fallback")


if __name__ == "__main__":
    run()
