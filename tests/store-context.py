#!/usr/bin/env python3
"""Resolve verified, disposable store registrations without opening live state."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import sys
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from dataclasses import asdict, replace
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import store_context
from store_context import (Account, CheckoutBinding, DirectoryBinding, Generations,
                           StoreContextError, resolve_store)


def new_id():
    return str(uuid4())


def directory_binding(path):
    entry = path.stat()
    return DirectoryBinding(path, entry.st_dev, entry.st_ino)


def snapshot(root):
    return {path.relative_to(root): (path.stat().st_mode, path.read_bytes() if path.is_file() else None)
            for path in root.rglob("*")}


class StoreContextTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="hostbook-context-", dir=Path("/tmp").resolve())
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.state = self.root / "state"
        self.recovery = self.root / "recovery"
        self.registry = self.state / "stores/v1/registry.json"
        self.registry.parent.mkdir(parents=True)
        self.state.chmod(0o700)
        self.recovery.mkdir(mode=0o700)
        isolated = patch.dict(os.environ, {
            "HOSTBOOK_DIR": str(self.root / "private"),
            "HOSTBOOK_STATE": str(self.state),
            "HOSTBOOK_SSH_CONFIG": str(self.root / "ssh.conf"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
        })
        isolated.start()
        self.addCleanup(isolated.stop)
        self.entries = []
        bindings = {}
        for name in ("private", "shared a", "shared b"):
            store_id, checkout_id, binding_id = new_id(), new_id(), new_id()
            root = self.root / name
            git = root / ".git"
            git.mkdir(parents=True)
            (git / "HEAD").write_text("ref: refs/heads/main\n")
            (root / ".hostbook").mkdir()
            (root / ".hostbook/identity.json").write_text(json.dumps({
                "format": "hostbook-store", "version": 1, "generation": 1,
                "store_id": store_id, "sources": [], "hosts": [],
            }))
            (root / "secrets").mkdir()
            (root / "secrets/vault.pub").write_text(f"synthetic {name}\n")
            shared = name != "private"
            self.entries.append({
                "store_id": store_id, "kind": "shared" if shared else "private",
                "registration_generation": 1, "checkout_generation": 2,
                "connection_generation": 3, "policy_generation": 4,
                "checkout": {"checkout_id": checkout_id, "root": str(root), "binding_id": binding_id},
                "connection": {"target_id": new_id(), "ref": "refs/heads/main"} if shared else None,
                "effective_mode": "receive-only" if shared else None, "transition_id": None,
            })
            bindings[binding_id] = CheckoutBinding(store_id, checkout_id, directory_binding(root),
                                                  directory_binding(git), directory_binding(git))
        self.account = Account(directory_binding(self.root), directory_binding(self.state),
                               directory_binding(self.recovery), new_id(), new_id(), bindings)
        self.recovery_marker = self.recovery / "root.json"
        self.save_recovery_marker()
        self.generations = Generations(1, 2, 3, 4)
        self.save_registry()

    def save_recovery_marker(self, **overrides):
        marker = {"version": 1, "device_id": self.account.device_id, "root_id": self.account.recovery_root_id}
        marker.update(overrides)
        self.recovery_marker.write_text(json.dumps(marker))

    def save_registry(self):
        self.registry.write_text(json.dumps({
            "format": "hostbook-registrations", "version": 1, "device_id": self.account.device_id,
            "recovery_root_id": self.account.recovery_root_id, "revision": 1, "stores": self.entries,
        }))

    def resolve(self, index=0, *, account=None, generations=None):
        return resolve_store(account or self.account, self.entries[index]["store_id"],
                             generations or self.generations)

    def installed_launcher(self):
        launcher = os.environ.get("HOSTBOOK_TEST_INSTALLED")
        if launcher is None:
            source = Path(__file__).resolve().parents[1]
            app = self.root / "installed app"
            (app / "lib").mkdir(parents=True)
            shutil.copy2(source / "hostbook", app / "hostbook")
            shutil.copy2(source / "lib/store_context.py", app / "lib/store_context.py")
            route_module = source / "lib/store_read.py"
            if route_module.is_file():
                shutil.copy2(route_module, app / "lib/store_read.py")
            launcher = self.root / "bin" / "hostbook"
            launcher.parent.mkdir()
            launcher.symlink_to(app / "hostbook")
        return launcher

    def write_local_approval(self, home, account=None):
        folder = home / ".config/hostbook"
        folder.mkdir(parents=True)
        approval = folder / "store-account-v1.json"
        approval.write_text(json.dumps({
            "format": "hostbook-store-account", "version": 1,
            "home": asdict(directory_binding(home)), "account": asdict(account or self.account),
        }, default=os.fspath))
        approval.chmod(0o600)
        return approval

    def test_installed_callback_uses_its_own_module_and_has_no_write_effects(self):
        launcher = self.installed_launcher()
        elsewhere = self.root / "unrelated directory"
        elsewhere.mkdir()
        (elsewhere / "lib").mkdir()
        (elsewhere / "lib/store_context.py").write_text("raise RuntimeError('wrong module')\n")
        env = dict(os.environ, HOME=str(self.root / "fixture-home"),
                   HOSTBOOK_STATE=str(self.root / "do-not-bootstrap"),
                   HOSTBOOK_LIB_DIR=str(elsewhere / "lib"), PYTHONPATH=str(elsewhere / "lib"))
        for key in ("SSH_AUTH_SOCK", "SSH_AGENT_PID"):
            env.pop(key, None)

        def call(request, cwd):
            return subprocess.run([launcher, "_store_context"], cwd=cwd, env=env,
                                  text=True, input=json.dumps(request, default=os.fspath),
                                  capture_output=True, timeout=10)

        before = snapshot(self.root)
        for index, cwd in ((0, elsewhere), (1, self.root), (2, elsewhere)):
            request = {"account": asdict(self.account), "store_id": self.entries[index]["store_id"],
                       "generations": asdict(self.generations)}
            result = call(request, cwd)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {
                "store_id": request["store_id"], "checkout": self.entries[index]["checkout"]["root"],
                "generations": request["generations"],
            })

        request["generations"]["policy"] += 1
        stale = call(request, elsewhere)
        self.assertEqual(stale.returncode, 65)
        self.assertEqual(stale.stdout, "")
        self.assertIn("stale store generations", stale.stderr)

        request["account"]["root"]["path"] = elsewhere
        outside = call(request, elsewhere)
        self.assertEqual(outside.returncode, 65)
        self.assertEqual(outside.stdout, "")
        self.assertIn("fixture account must be", outside.stderr)

        missing_pipe = subprocess.run([launcher, "_store_context"], cwd=elsewhere, env=env,
                                      stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=10)
        self.assertEqual(missing_pipe.returncode, 65)
        self.assertEqual(missing_pipe.stdout, "")
        self.assertIn("fixture request pipe is required", missing_pipe.stderr)
        self.assertEqual(snapshot(self.root), before)

    def test_local_approval_rejects_copy_to_other_home_and_insecure_mode(self):
        home = self.root / "approved home"
        home.mkdir(mode=0o700)
        approval = self.write_local_approval(home)
        before = snapshot(self.root)
        context = resolve_store(store_context.load_approved_account(home),
                                self.entries[1]["store_id"], self.generations)
        self.assertEqual(context.checkout, self.root / "shared a")
        self.assertEqual(snapshot(self.root), before)

        other = self.root / "other home"
        (other / ".config/hostbook").mkdir(parents=True)
        other.chmod(0o700)
        shutil.copy2(approval, other / ".config/hostbook/store-account-v1.json")
        with self.assertRaisesRegex(StoreContextError, "approval home binding"):
            store_context.load_approved_account(other)

        approval.chmod(0o644)
        with self.assertRaisesRegex(StoreContextError, "approval file must be private"):
            store_context.load_approved_account(home)
        approval.chmod(0o600)
        moved = approval.rename(approval.with_suffix(".copy"))
        approval.symlink_to(moved)
        with self.assertRaisesRegex(StoreContextError, "local store approval unavailable"):
            store_context.load_approved_account(home)

    @unittest.skipUnless(sys.platform == "darwin", "macOS extended ACL")
    def test_local_approval_rejects_acl_grants(self):
        home = self.root / "approved home"
        home.mkdir(mode=0o700)
        approval = self.write_local_approval(home)
        subprocess.run(["/bin/chmod", "+a", "everyone allow write", str(approval)], check=True)
        with self.assertRaisesRegex(StoreContextError, "ACL grants"):
            store_context.load_approved_account(home)

    def test_installed_local_lookup_uses_the_os_account(self):
        expected_home = os.environ.get("HOSTBOOK_TEST_ACCOUNT_HOME")
        if expected_home is None:
            self.skipTest("requires a disposable OS account")
        import pwd
        home = Path(expected_home)
        self.assertEqual(home, Path("/fixture-home"))
        self.assertEqual(home, Path(pwd.getpwuid(os.geteuid()).pw_dir))
        self.assertEqual(list(home.iterdir()), [])
        launcher = self.installed_launcher()
        elsewhere = self.root / "unrelated directory"
        elsewhere.mkdir()
        env = dict(os.environ, HOME=str(elsewhere), XDG_CONFIG_HOME=str(elsewhere))
        store_id = self.entries[1]["store_id"]

        def lookup():
            return subprocess.run([launcher, "_store_context", store_id, "1", "2", "3", "4"],
                                  cwd=elsewhere, env=env, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=10)

        before = snapshot(self.root)
        missing = lookup()
        self.assertEqual((missing.returncode, missing.stdout), (65, ""))
        self.assertIn("local store approval unavailable", missing.stderr)
        self.assertEqual(list(home.iterdir()), [])
        self.assertEqual(snapshot(self.root), before)

        config = home / ".config/hostbook"
        config.mkdir(parents=True)
        approval = config / "store-account-v1.json"
        command = [launcher, "_approve_store_account", str(self.root)]
        planned = subprocess.run(command, cwd=elsewhere, env=env, stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(planned.returncode, 0, planned.stderr)
        self.assertFalse(approval.exists())
        confirmation = json.loads(planned.stdout)["confirmation"]
        applied = subprocess.run(command + [confirmation], cwd=elsewhere, env=env,
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(len(json.loads(applied.stdout)["stores"]), 3)
        approval_bytes = approval.read_bytes()
        positive = lookup()
        self.assertEqual(positive.returncode, 0, positive.stderr)
        self.assertEqual(json.loads(positive.stdout)["checkout"], str(self.root / "shared a"))
        self.assertEqual(approval.read_bytes(), approval_bytes)
        self.assertEqual(snapshot(self.root), before)

        host_id = new_id()
        self.write_index_host(1, "db", host_id)
        indexed = subprocess.run([launcher, "_refresh_store_index", store_id, "1", "2", "3", "4"],
                                 cwd=elsewhere, env=env, stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(indexed.returncode, 0, indexed.stderr)
        rows = f"{store_id}\t{host_id}\tdb\n"
        self.assertEqual(indexed.stdout, rows)
        self.assertEqual(self.resolve(1).index.read_text(), rows)
        self.assertFalse(self.resolve(0).index.exists())
        self.assertFalse(self.resolve(2).index.exists())

        refreshed = subprocess.run([launcher, "_refresh_store_status", store_id, "1", "2", "3", "4"],
                                   cwd=elsewhere, env=env, stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        status = json.loads(refreshed.stdout)
        self.assertEqual(status["state"], "unchecked")
        self.assertEqual(json.loads(self.resolve(1).status.read_text()), status)
        self.assertFalse(self.resolve(0).status.exists())
        self.assertFalse(self.resolve(2).status.exists())

        other = json.loads(self.registry.read_text())
        other["device_id"] = new_id()
        original = self.registry.read_bytes()
        self.registry.write_text(json.dumps(other))
        copied = lookup()
        self.assertEqual((copied.returncode, copied.stdout), (65, ""))
        self.assertIn("registry account binding mismatch", copied.stderr)
        self.registry.write_bytes(original)

        checkout = self.root / "shared a"
        parked = checkout.rename(self.root / "parked")
        shutil.copytree(parked, checkout)
        swapped = lookup()
        self.assertEqual((swapped.returncode, swapped.stdout), (65, ""))
        self.assertIn("directory binding mismatch", swapped.stderr)
        self.assertEqual(approval.read_bytes(), approval_bytes)

    def test_first_approval_validates_and_saves_only_after_matching_confirmation(self):
        home = self.root / "approved home"
        home.mkdir(mode=0o700)
        config = home / ".config/hostbook"
        config.mkdir(parents=True)
        approval = config / "store-account-v1.json"
        before = snapshot(self.root)
        plan = store_context.plan_account_approval(home, self.root)
        self.assertEqual({row["store_id"] for row in plan["stores"]},
                         {entry["store_id"] for entry in self.entries})
        self.assertEqual(snapshot(self.root), before)
        with self.assertRaisesRegex(StoreContextError, "confirmation"):
            store_context.approve_account(home, self.root, "wrong confirmation")
        self.assertEqual(snapshot(self.root), before)

        result = store_context.approve_account(home, self.root, plan["confirmation"])
        self.assertEqual(len(result["stores"]), 3)
        self.assertEqual(approval.stat().st_mode & 0o777, 0o600)
        saved = store_context.load_approved_account(home)
        self.assertEqual(resolve_store(saved, self.entries[1]["store_id"], self.generations).checkout,
                         self.root / "shared a")
        self.assertEqual({path: value for path, value in snapshot(self.root).items()
                          if path != approval.relative_to(self.root)}, before)
        approved = approval.read_bytes()
        with self.assertRaisesRegex(StoreContextError, "approval already exists"):
            store_context.approve_account(home, self.root, plan["confirmation"])
        self.assertEqual(approval.read_bytes(), approved)

    def test_approval_sync_failure_is_distinguished_from_prepublication_failure(self):
        home = self.root / "approved home"
        home.mkdir(mode=0o700)
        config = home / ".config/hostbook"
        config.mkdir(parents=True)
        approval = config / "store-account-v1.json"
        plan = store_context.plan_account_approval(home, self.root)
        before = snapshot(self.root)
        with patch("store_context.sync_local", side_effect=(None, OSError("file sync failed"))):
            with self.assertRaises(OSError):
                store_context.approve_account(home, self.root, plan["confirmation"])
        self.assertEqual(snapshot(self.root), before)

        with patch("store_context.sync_local", side_effect=(None, None, OSError("directory sync failed"))):
            with self.assertRaisesRegex(StoreContextError, "outcome uncertain"):
                store_context.approve_account(home, self.root, plan["confirmation"])
        self.assertEqual(list(config.iterdir()), [approval])
        saved = approval.read_bytes()
        with self.assertRaisesRegex(StoreContextError, "approval already exists"):
            store_context.approve_account(home, self.root, plan["confirmation"])
        self.assertEqual(approval.read_bytes(), saved)

    def test_approval_refuses_replaced_checkout_before_writing(self):
        home = self.root / "approved home"
        home.mkdir(mode=0o700)
        (home / ".config/hostbook").mkdir(parents=True)
        plan = store_context.plan_account_approval(home, self.root)
        checkout = self.root / "shared a"
        parked = checkout.rename(self.root / "parked")
        shutil.copytree(parked, checkout)
        before = snapshot(self.root)
        with self.assertRaisesRegex(StoreContextError, "confirmation"):
            store_context.approve_account(home, self.root, plan["confirmation"])
        self.assertEqual(snapshot(self.root), before)

    def test_approval_refuses_shared_git_and_two_way_mode(self):
        home = self.root / "approved home"
        home.mkdir(mode=0o700)
        (home / ".config/hostbook").mkdir(parents=True)
        self.entries[1]["effective_mode"] = "two-way"
        self.save_registry()
        with self.assertRaisesRegex(StoreContextError, "receive-only"):
            store_context.plan_account_approval(home, self.root)

        self.entries[1]["effective_mode"] = "receive-only"
        self.save_registry()
        git = self.root / "shared b/.git"
        shutil.rmtree(git)
        git.write_text(f"gitdir: {self.root / 'shared a/.git'}\n")
        before = snapshot(self.root)
        with self.assertRaisesRegex(StoreContextError, "overlapping"):
            store_context.plan_account_approval(home, self.root)
        self.assertEqual(snapshot(self.root), before)

    def write_index_host(self, index, name, host_id):
        entry = self.entries[index]
        root = Path(entry["checkout"]["root"])
        source = root / "team.conf"
        contents = f"Host {name}\n  HostName 192.0.2.90\n".encode()
        source.write_bytes(contents)
        sidecar = root / ".hostbook/identity.json"
        data = json.loads(sidecar.read_text())
        data["generation"] += 1
        data["sources"] = [{"path": "team.conf", "sha256": hashlib.sha256(contents).hexdigest()}]
        data["hosts"] = [{"host_id": host_id, "name": name, "bindings": [
            {"path": "team.conf", "host_ordinal": 1, "token_ordinal": 1},
        ]}]
        sidecar.write_text(json.dumps(data))

    def test_refreshing_one_index_does_not_change_other_stores(self):
        ids = [new_id() for _ in self.entries]
        contexts = [self.resolve(index) for index in range(3)]

        def refresh(index):
            return store_context.refresh_store_index(lambda: self.account,
                self.entries[index]["store_id"], self.generations)

        for index, context in enumerate(contexts):
            self.write_index_host(index, "db", ids[index])
            refresh(index)
            self.assertEqual(context.index.read_text(), f"{context.store_id}\t{ids[index]}\tdb\n")

        self.write_index_host(1, "db-renamed", ids[1])
        before = snapshot(self.root)
        refresh(1)
        scope = contexts[1].index.relative_to(self.root)
        after = snapshot(self.root)
        self.assertEqual({path: value for path, value in after.items() if path != scope},
                         {path: value for path, value in before.items() if path != scope})
        self.assertEqual(contexts[1].index.read_text(), f"{contexts[1].store_id}\t{ids[1]}\tdb-renamed\n")

        identity = contexts[1].checkout / ".hostbook/identity.json"
        saved_identity = identity.read_text()
        changed = json.loads(saved_identity)
        changed["hosts"][0]["name"] = "does-not-exist"
        identity.write_text(json.dumps(changed))
        before = snapshot(self.root)
        with self.assertRaisesRegex(StoreContextError, "invalid or duplicate index host binding"):
            refresh(1)
        self.assertEqual(snapshot(self.root), before)
        identity.write_text(saved_identity)

        source = contexts[1].checkout / "team.conf"
        source.write_text("Host unreviewed\n")
        before = snapshot(self.root)
        with self.assertRaisesRegex(StoreContextError, "source bytes changed"):
            refresh(1)
        self.assertEqual(snapshot(self.root), before)

    def test_index_refresh_rejects_busy_and_changed_inputs(self):
        host_id = new_id()
        self.write_index_host(1, "db", host_id)
        context = self.resolve(1)
        refresh = store_context.refresh_store_index
        refresh(lambda: self.account, context.store_id, self.generations)
        before = snapshot(self.root)
        fd = os.open(context.index.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(StoreContextError, "already being refreshed"):
                refresh(lambda: self.account, context.store_id, self.generations)
        finally:
            os.close(fd)
        self.assertEqual(snapshot(self.root), before)

        self.write_index_host(1, "db-second", host_id)
        with patch("store_context.index_rows", wraps=store_context.index_rows) as read_rows:
            def load():
                if read_rows.called:
                    self.write_index_host(1, "db-third", host_id)
                return self.account

            with self.assertRaisesRegex(StoreContextError, "inputs changed"):
                refresh(load, context.store_id, self.generations)
        self.assertEqual(context.index.read_bytes(), before[context.index.relative_to(self.root)][1])
        self.assertEqual(list(context.index.parent.iterdir()), [context.index])

    def write_route(self, index, host_id, text, name="db"):
        self.write_index_host(index, name, host_id)
        context = self.resolve(index)
        contents = text.encode()
        (context.checkout / "team.conf").write_bytes(contents)
        sidecar = context.checkout / ".hostbook/identity.json"
        data = json.loads(sidecar.read_text())
        data["sources"][0]["sha256"] = hashlib.sha256(contents).hexdigest()
        sidecar.write_text(json.dumps(data))
        return context

    def route_callback(self, context, action, *args):
        request = {"account": asdict(self.account), "store_id": context.store_id,
                   "generations": asdict(context.generations)}
        cwd = self.root / "shared b"
        env = dict(os.environ, HOME=str(self.root / "fixture-home"), USER="fixture",
                   HOSTBOOK_LIB_DIR=str(cwd), PYTHONPATH=str(cwd))
        for key in ("SSH_AUTH_SOCK", "SSH_AGENT_PID"):
            env.pop(key, None)
        return subprocess.run([self.launcher, action, *args], cwd=cwd, env=env,
                              input=json.dumps(request, default=os.fspath), text=True,
                              capture_output=True, timeout=10)

    def test_installed_callback_effective_routes_keep_private_a_and_b_separate(self):
        self.launcher = self.installed_launcher()
        host_id = new_id()
        endpoints = ("192.0.2.10", "198.51.100.10", "203.0.113.10")
        shared_a_stamp = None
        for index, endpoint in enumerate(endpoints):
            with self.subTest(index=index):
                context = self.write_route(index, host_id, f"""Host db
    HostName {endpoint}
    User login-{index}
    ProxyJump local-hop
Host *
    Port 2222
""")
                defaults = self.state / f"stores/v1/{context.store_id}/ssh/config"
                defaults.parent.mkdir(parents=True, mode=0o700)
                defaults.parent.parent.chmod(0o700)
                defaults.write_text("""Host db
    HostName 192.0.2.99
    User wrong
Host local-hop
    HostName 203.0.113.1
    User approved-hop
""")
                defaults.chmod(0o600)
                listing = self.route_callback(context, "_store_hosts")
                self.assertEqual(listing.returncode, 0, listing.stderr)
                store_id, listed_id, name, stamp = listing.stdout.strip().split("\t")
                self.assertEqual((store_id, listed_id, name), (context.store_id, host_id, "db"))
                if index == 1:
                    shared_a_stamp = stamp
                result = self.route_callback(context, "_store_preview", listed_id, stamp)
                self.assertEqual(result.returncode, 0, result.stderr)
                route = json.loads(result.stdout)
                self.assertEqual((route["hostname"], route["user"], route["port"], route["proxyjump"]),
                                 (endpoint, f"login-{index}", "2222", "local-hop"))
                defaults.write_text(defaults.read_text().replace("approved-hop", "changed-hop"))
                stale = self.route_callback(context, "_store_preview", listed_id, stamp)
                self.assertEqual(stale.returncode, 65)
                self.assertIn("selection changed", stale.stderr)
                self.assertEqual(stale.stdout, "")
                defaults.write_text(defaults.read_text().replace("changed-hop", "approved-hop"))

        renamed = self.write_route(1, host_id, "Host renamed\n    HostName 198.51.100.10\n", "renamed")
        stale = self.route_callback(renamed, "_store_preview", host_id, shared_a_stamp)
        self.assertEqual(stale.returncode, 65)
        listing = self.route_callback(renamed, "_store_hosts")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertEqual(listing.stdout.split("\t")[:3], [renamed.store_id, host_id, "renamed"])

    def test_installed_callback_connects_through_the_selected_local_jump(self):
        self.launcher = self.installed_launcher()
        host_id = new_id()
        context = self.write_route(1, host_id, """Host db
    HostName 198.51.100.10
    User destination
    ProxyJump approved-hop
""")
        with socket.socket() as hop:
            hop.bind(("127.0.0.1", 0))
            hop.listen()
            hop.settimeout(5)
            local = self.state / f"stores/v1/{context.store_id}/ssh/config"
            local.parent.mkdir(parents=True, mode=0o700)
            local.parent.parent.chmod(0o700)
            known_hosts = self.root / "known_hosts"
            known_hosts.touch()
            local.write_text(f"""Host approved-hop
    HostName 127.0.0.1
    User jump
    Port {hop.getsockname()[1]}
Host *
    GlobalKnownHostsFile none
    UserKnownHostsFile "{known_hosts}"
    StrictHostKeyChecking yes
    ConnectTimeout 2
""")
            local.chmod(0o600)
            rows = self.route_callback(context, "_store_hosts")
            self.assertEqual(rows.returncode, 0, rows.stderr)
            stamp = rows.stdout.strip().split("\t")[3]
            received = []

            def server():
                try:
                    peer, _ = hop.accept()
                    with peer:
                        peer.settimeout(3)
                        received.append(peer.recv(256))
                except OSError:
                    return

            listener = Thread(target=server)
            listener.start()
            try:
                result = self.route_callback(context, "_store_connect", host_id, stamp)
            finally:
                listener.join(timeout=6)
            self.assertEqual(result.returncode, 255, result.stderr)
            self.assertEqual(len(received), 1)
            self.assertTrue(received[0].startswith(b"SSH-2.0-OpenSSH_"))

            local.write_text(local.read_text().replace("User jump", "User stale"))
            stale = self.route_callback(context, "_store_connect", host_id, stamp)
            self.assertEqual(stale.returncode, 65)
            self.assertIn("selection changed", stale.stderr)

    def test_installed_callback_rejects_duplicate_and_malformed_host_ids(self):
        self.launcher = self.installed_launcher()
        host_id = new_id()
        context = self.write_route(1, host_id, "Host db\n")
        listing = self.route_callback(context, "_store_hosts")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        stamp = listing.stdout.strip().split("\t")[3]
        sidecar = context.checkout / ".hostbook/identity.json"
        original = json.loads(sidecar.read_text())
        for invalid in (original["hosts"] * 2, [dict(original["hosts"][0], host_id="malformed")]):
            with self.subTest(invalid=invalid):
                sidecar.write_text(json.dumps(dict(original, hosts=invalid)))
                failed = self.route_callback(context, "_store_hosts")
                self.assertEqual(failed.returncode, 65)
                self.assertIn("invalid or duplicate index host", failed.stderr)
                unavailable = self.route_callback(context, "_store_preview", host_id, stamp)
                self.assertEqual(unavailable.returncode, 65)
                self.assertEqual(unavailable.stdout, "")
        sidecar.write_text(json.dumps(original))
        self.entries[1]["checkout"] = self.entries[1]["connection"] = self.entries[1]["effective_mode"] = None
        self.entries[1]["checkout_generation"] += 1
        self.entries[1]["connection_generation"] += 1
        self.save_registry()
        disconnected = self.route_callback(context, "_store_preview", host_id, stamp)
        self.assertEqual(disconnected.returncode, 65)
        self.assertIn("stale store generations", disconnected.stderr)

    def test_installed_callback_never_derives_unsafe_ssh_inputs(self):
        self.launcher = self.installed_launcher()
        for directive in ("Match exec true", "Match host db#tag exec true", "Include somewhere.conf", "ProxyCommand false",
                          "CanonicalizeHostname yes", "IdentityFile /somewhere/local.key"):
            with self.subTest(directive=directive):
                context = self.write_route(1, new_id(), f"Host db\n    {directive}\n")
                result = self.route_callback(context, "_store_hosts")
                self.assertEqual(result.returncode, 65, result.stderr)
                self.assertIn("SSH directive", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_writer_lock_and_status_are_isolated_across_processes(self):
        home = self.root / "approved home"
        home.mkdir(mode=0o700)
        self.write_local_approval(home)
        private, shared_a, shared_b = [self.resolve(index) for index in range(3)]

        def refresh(context):
            return store_context.refresh_store_status(lambda: self.account, context.store_id,
                                                      context.generations)

        refresh(private)
        initial_a = shared_a.status.parent
        script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from store_context import Generations, load_approved_account, store_writer
with store_writer(lambda: load_approved_account(Path(sys.argv[2])), sys.argv[3], Generations(1, 2, 3, 4)):
    print("held", flush=True)
    sys.stdin.read()
"""
        lib_dir = Path(__file__).resolve().parents[1] / "lib"
        with subprocess.Popen([sys.executable, "-B", "-c", script, str(lib_dir), str(home), shared_a.store_id],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as worker:
            try:
                self.assertTrue(select.select([worker.stdout], [], [], 5)[0], "writer did not acquire A")
                self.assertEqual(worker.stdout.readline().strip(), "held")
                before = snapshot(self.root)
                with self.assertRaisesRegex(StoreContextError, "store writer is busy"):
                    refresh(shared_a)
                self.assertEqual(snapshot(self.root), before)
                self.assertFalse(initial_a.exists())

                b = refresh(shared_b)
                self.assertEqual(b["store_id"], shared_b.store_id)
                self.assertEqual(json.loads(shared_b.status.read_text()), b)
                self.assertFalse(shared_a.status.exists())
                self.assertEqual(snapshot(self.root)[private.status.relative_to(self.root)],
                                 before[private.status.relative_to(self.root)])
                self.entries[1]["connection_generation"] += 1
                self.save_registry()
                current = self.resolve(1, generations=replace(self.generations, connection=4))
                self.assertEqual(current.writer_lock, shared_a.writer_lock)
                self.assertNotEqual(current.status, shared_a.status)
                with self.assertRaisesRegex(StoreContextError, "store writer is busy"):
                    refresh(current)
                self.assertFalse(current.status.exists())
            finally:
                worker.stdin.close()
                try:
                    worker.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait()
        self.assertEqual(worker.returncode, 0)

        before = snapshot(self.root)
        a = refresh(current)
        self.assertEqual(a["state"], "unchecked")
        self.assertEqual(a["connection_generation"], 4)
        self.assertEqual(json.loads(current.status.read_text()), a)
        self.assertFalse(shared_a.status.exists())
        after = snapshot(self.root)
        scope = current.status.parent.parent.relative_to(self.root)
        self.assertEqual({k: v for k, v in after.items() if k != scope and scope not in k.parents},
                         {k: v for k, v in before.items() if k != scope and scope not in k.parents})

    def test_status_refresh_rejects_connection_change_before_publication(self):
        context = self.resolve(1)
        refresh = store_context.refresh_store_status
        initial = refresh(lambda: self.account, context.store_id, context.generations)
        calls = 0

        def load():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.entries[1]["connection_generation"] += 1
                self.save_registry()
            return self.account

        with self.assertRaisesRegex(StoreContextError, "stale store generations"):
            refresh(load, context.store_id, context.generations)
        self.assertEqual(json.loads(context.status.read_text()), initial)
        self.assertEqual(list(context.status.parent.iterdir()), [context.status])
        current = self.resolve(1, generations=replace(self.generations, connection=4))
        self.assertFalse(current.status.exists())

    def test_vault_unlock_cache_is_scoped_and_rejects_rotated_envelopes(self):
        tools = [shutil.which(name) for name in ("age", "age-keygen", "age-plugin-batchpass")]
        if not all(tools):
            self.skipTest("requires age, age-keygen and age-plugin-batchpass")
        from store_vault import VaultUnlockCache

        tool_dir = Path(tools[0]).parent
        env = {"PATH": str(tool_dir), "HOME": str(self.root / "fixture-home")}
        passphrase = b"synthetic cache passphrase"
        recovery = b"synthetic recovery passphrase"

        def tool(name, args=(), data=None, *, pass_fds=(), extra_env=None):
            return subprocess.run([str(tool_dir / name), *args], input=data, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=dict(env, **(extra_env or {})),
                                  timeout=30, check=True, pass_fds=pass_fds).stdout

        def seal(identity, password):
            reader, writer = os.pipe()
            try:
                with os.fdopen(writer, "wb") as pipe:
                    pipe.write(password)
                return tool("age", ("-e", "-j", "batchpass", "-a"), identity,
                            pass_fds=(reader,), extra_env={"AGE_PASSPHRASE_FD": str(reader)})
            finally:
                os.close(reader)

        contexts = [self.resolve(index) for index in range(3)]
        identities = [tool("age-keygen") for _ in contexts]
        for context, identity in zip(contexts, identities):
            (context.vault / "vault.pub").write_bytes(tool("age-keygen", ("-y",), identity))
            (context.vault / "vault.key.age").write_bytes(seal(identity, passphrase))
            (context.vault / "vault.recovery.age").write_bytes(seal(identity, recovery))

        cache = VaultUnlockCache(tool_dir)
        load = lambda: self.account
        private, shared_a, shared_b = contexts
        before = snapshot(self.root)
        cache.unlock(load, shared_a.store_id, self.generations, passphrase)
        self.assertTrue(cache.get(load, shared_a.store_id, self.generations) == identities[1])
        self.assertIsNone(cache.get(load, private.store_id, self.generations))
        self.assertIsNone(cache.get(load, shared_b.store_id, self.generations))
        self.assertEqual(snapshot(self.root), before)
        self.assertIsNone(VaultUnlockCache(tool_dir).get(load, shared_a.store_id, self.generations))
        with self.assertRaisesRegex(StoreContextError, "stale store generations"):
            cache.get(load, shared_a.store_id, replace(self.generations, checkout=99))
        self.assertTrue(cache.get(load, shared_a.store_id, self.generations) == identities[1])

        # A copied vault with the same recipient and envelopes still gets a separate cache.
        for name in ("vault.pub", "vault.key.age", "vault.recovery.age"):
            shutil.copyfile(shared_a.vault / name, shared_b.vault / name)
        self.assertIsNone(cache.get(load, shared_b.store_id, self.generations))

        # Rewrap the same key. Keeping the same public recipient still invalidates the old cache.
        next_password = b"synthetic rotated passphrase"
        (shared_a.vault / "vault.key.age").write_bytes(seal(identities[1], next_password))
        self.assertIsNone(cache.get(load, shared_a.store_id, self.generations))
        with self.assertRaisesRegex(StoreContextError, "vault unlock failed"):
            cache.unlock(load, shared_a.store_id, self.generations, passphrase)
        self.assertIsNone(cache.get(load, shared_a.store_id, self.generations))
        cache.unlock(load, shared_a.store_id, self.generations, next_password)
        self.assertTrue(cache.get(load, shared_a.store_id, self.generations) == identities[1])
        self.assertIsNone(cache.get(load, private.store_id, self.generations))
        self.assertIsNone(cache.get(load, shared_b.store_id, self.generations))

    def test_three_contexts_are_independent_and_resolution_does_not_write(self):
        before = snapshot(self.root)
        contexts = [self.resolve(index) for index in range(3)]
        for context, entry in zip(contexts, self.entries):
            root = Path(entry["checkout"]["root"])
            self.assertEqual(context.store_id, entry["store_id"])
            self.assertEqual(context.checkout, root)
            self.assertEqual(context.git_dir, root / ".git")
            self.assertEqual(context.git_common_dir, root / ".git")
            self.assertEqual(context.vault, root / "secrets")
            self.assertEqual(context.effective_mode, entry["effective_mode"])
            namespace = self.registry.parent / entry["store_id"]
            self.assertEqual(context.vault_cache_namespace, namespace / "vault-cache" / self.account.device_id)
            self.assertEqual(context.index, namespace / "index/2/index.tsv")
            self.assertEqual(context.writer_lock, namespace / "sync.lock")
            self.assertEqual(context.status, namespace / "connection/3/status")
        self.assertEqual(snapshot(self.root), before)

    def test_unknown_and_each_stale_generation_are_rejected(self):
        before = snapshot(self.root)
        with self.assertRaisesRegex(StoreContextError, "unknown store"):
            resolve_store(self.account, new_id(), self.generations)
        with self.assertRaisesRegex(StoreContextError, "invalid expected generations"):
            self.resolve(generations=replace(self.generations, registration=True))
        names = ("registration", "checkout", "connection", "policy")
        for name in names:
            with self.subTest(generation=name), self.assertRaisesRegex(StoreContextError, "stale"):
                self.resolve(generations=replace(self.generations, **{name: 9}))
        self.assertEqual(snapshot(self.root), before)
        self.entries[1]["checkout_generation"] += 1
        self.save_registry()
        before = snapshot(self.root)
        with self.assertRaisesRegex(StoreContextError, "stale"):
            self.resolve(1)
        updated = self.resolve(1, generations=replace(self.generations, checkout=3))
        self.assertEqual(updated.index.parent.name, "3")
        self.assertEqual(updated.writer_lock.parent.name, self.entries[1]["store_id"])
        self.assertEqual(self.resolve(2).generations, self.generations)
        self.assertEqual(snapshot(self.root), before)

    def test_copied_registry_does_not_prove_account_or_state_binding(self):
        copy = self.root / "other-state"
        (copy / "stores/v1").mkdir(parents=True)
        copy.chmod(0o700)
        shutil.copyfile(self.registry, copy / "stores/v1/registry.json")
        with self.assertRaisesRegex(StoreContextError, "state binding"):
            self.resolve(account=replace(self.account, state=replace(self.account.state, path=copy)))
        new_device, new_recovery_id = new_id(), new_id()
        other_recovery = self.root / "other-recovery"
        other_recovery.mkdir(mode=0o700)
        (other_recovery / "root.json").write_text(json.dumps({
            "version": 1, "device_id": new_device, "root_id": new_recovery_id,
        }))
        other_account = replace(self.account, state=directory_binding(copy),
                                recovery=directory_binding(other_recovery), device_id=new_device,
                                recovery_root_id=new_recovery_id)
        with self.assertRaisesRegex(StoreContextError, "registry account binding mismatch"):
            self.resolve(account=other_account)

    def test_recovery_root_record_must_match_independent_account_evidence(self):
        # A copied registry still holds the right device ID and checkout IDs.
        for changes in ({"root_id": new_id()}, {"device_id": new_id()}, {"version": True}):
            with self.subTest(changes=changes):
                self.save_recovery_marker(**changes)
                before = snapshot(self.root)
                with patch("store_context.registry_entries") as read_registry:
                    with self.assertRaisesRegex(StoreContextError, "recovery root record"):
                        self.resolve()
                    read_registry.assert_not_called()
                self.assertEqual(snapshot(self.root), before)
        self.save_recovery_marker()
        self.assertEqual(self.resolve().checkout, self.root / "private")

    def test_missing_or_redirected_recovery_marker_is_rejected_without_repair(self):
        self.recovery_marker.unlink()
        before = snapshot(self.root)
        with self.assertRaisesRegex(StoreContextError, "recovery root record"):
            self.resolve()
        self.assertEqual(snapshot(self.root), before)
        self.save_recovery_marker()
        other = self.recovery_marker.rename(self.root / "copied-root.json")
        self.recovery_marker.symlink_to(other)
        with self.assertRaisesRegex(StoreContextError, "recovery root record"):
            self.resolve()

    def test_state_and_recovery_directories_must_be_private(self):
        for path in (self.state, self.recovery):
            with self.subTest(path=path.name):
                path.chmod(0o755)
                with self.assertRaisesRegex(StoreContextError, f"{path.name} directory must be private"):
                    self.resolve()
                path.chmod(0o700)

    def test_moved_checkout_and_wrong_sidecar_are_rejected(self):
        original = Path(self.entries[1]["checkout"]["root"])
        parked = original.rename(self.root / "parked")
        shutil.copytree(parked, original)
        with patch("store_context.verify_sidecar") as read_sidecar:
            with self.assertRaisesRegex(StoreContextError, "checkout binding"):
                self.resolve(1)
            read_sidecar.assert_not_called()
        shutil.rmtree(original)
        parked.rename(original)
        sidecar = original / ".hostbook/identity.json"
        sidecar.write_text(sidecar.read_text().replace(self.entries[1]["store_id"], new_id()))
        with self.assertRaisesRegex(StoreContextError, "sidecar store ID"):
            self.resolve(1)

    def test_duplicate_store_and_overlapping_checkout_paths_are_rejected(self):
        self.entries.append(self.entries[1])
        self.save_registry()
        with self.assertRaisesRegex(StoreContextError, "duplicate store"):
            self.resolve()
        self.entries.pop()
        outer = Path(self.entries[1]["checkout"]["root"])
        inner = Path(self.entries[2]["checkout"]["root"]).rename(outer / "nested")
        checkout = self.entries[2]["checkout"]
        checkout["root"] = str(inner)
        bindings = self.account.checkouts
        bindings[checkout["binding_id"]] = replace(bindings[checkout["binding_id"]],
            root=directory_binding(inner), git_dir=directory_binding(inner / ".git"),
            git_common_dir=directory_binding(inner / ".git"))
        self.save_registry()
        with self.assertRaisesRegex(StoreContextError, "overlapping"):
            self.resolve()

    def test_case_aliased_ancestor_is_detected_by_filesystem_identity(self):
        outer = Path(self.entries[1]["checkout"]["root"])
        alias = outer.with_name(outer.name.upper())
        if not alias.is_dir():
            self.skipTest("case-sensitive filesystem")
        Path(self.entries[2]["checkout"]["root"]).rename(outer / "nested")
        inner = alias / "nested"
        checkout = self.entries[2]["checkout"]
        checkout["root"] = str(inner)
        bindings = self.account.checkouts
        bindings[checkout["binding_id"]] = replace(bindings[checkout["binding_id"]],
            root=directory_binding(inner), git_dir=directory_binding(inner / ".git"),
            git_common_dir=directory_binding(inner / ".git"))
        self.save_registry()
        with self.assertRaisesRegex(StoreContextError, "overlapping"):
            self.resolve()

    def test_redirected_git_directory_does_not_match_approved_binding(self):
        checkout = Path(self.entries[2]["checkout"]["root"])
        shutil.rmtree(checkout / ".git")
        other_git = Path(self.entries[1]["checkout"]["root"]) / ".git"
        (checkout / ".git").write_text(f"gitdir: {other_git}\n")
        with self.assertRaisesRegex(StoreContextError, "Git binding"):
            self.resolve(2)

    def test_independent_linked_git_checkout_and_common_overlap(self):
        checkout = Path(self.entries[2]["checkout"]["root"])
        common = (checkout / ".git").rename(self.root / "separate-git")
        git_dir = common / "worktrees/work"
        git_dir.mkdir(parents=True)
        (checkout / ".git").write_text(f"gitdir: {git_dir}\n")
        (git_dir / "commondir").write_text("../..\n")
        binding_id = self.entries[2]["checkout"]["binding_id"]
        bindings = self.account.checkouts
        bindings[binding_id] = replace(bindings[binding_id], git_dir=directory_binding(git_dir),
                                        git_common_dir=directory_binding(common))
        self.assertEqual(self.resolve(2).git_common_dir, common)

        other_git = Path(self.entries[1]["checkout"]["root"]) / ".git"
        (git_dir / "commondir").write_text(f"{other_git}\n")
        bindings[binding_id] = replace(bindings[binding_id], git_common_dir=directory_binding(other_git))
        with self.assertRaisesRegex(StoreContextError, "overlapping"):
            self.resolve(2)

    def test_linked_store_resources_are_rejected(self):
        context = self.resolve(2)
        shutil.rmtree(context.vault)
        context.vault.symlink_to(self.resolve(1).vault, target_is_directory=True)
        with self.assertRaisesRegex(StoreContextError, "invalid store resource path"):
            self.resolve(2)
        context.vault.unlink()
        context.writer_lock.parent.mkdir()
        context.writer_lock.symlink_to(self.resolve(1).checkout, target_is_directory=True)
        with self.assertRaisesRegex(StoreContextError, "invalid store resource path"):
            self.resolve(2)

    def test_registry_requires_exact_json_fields_and_no_symlink(self):
        text = self.registry.read_text()
        self.registry.write_text(text.replace('"version": 1', '"version": 1, "version": 1'))
        with self.assertRaisesRegex(StoreContextError, "duplicate JSON"):
            self.resolve()
        outside = self.root / "registry-copy"
        outside.write_text(text)
        self.registry.unlink()
        self.registry.symlink_to(outside)
        with self.assertRaises(StoreContextError):
            self.resolve()


if __name__ == "__main__":
    unittest.main(verbosity=2)
