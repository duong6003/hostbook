#!/usr/bin/env python3
"""Resolve verified, disposable store registrations without opening live state."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import time
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
            for name in ("store_read.py", "store_login.py", "store_vault.py", "store_receive.py", "store_checkout.py", "store_journal.py", "store_publish.py"):
                shutil.copy2(source / "lib" / name, app / "lib" / name)
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

    def test_legacy_writers_reject_managed_checkout_before_bootstrap(self):
        app = Path(__file__).resolve().parents[1] / "hostbook"
        store = self.root / "shared a"
        (self.root / "home").mkdir()
        block = self.root / "input"
        block.write_text("Host new\n  HostName 192.0.2.10\n")
        env = dict(os.environ, HOME=str(self.root / "home"), HOSTBOOK_DIR=str(store),
                   HOSTBOOK_STATE=str(self.root / "legacy state"), HOSTBOOK_LANG="en")
        for key in ("SSH_AUTH_SOCK", "SSH_AGENT_PID"):
            env.pop(key, None)
        before = snapshot(self.root)
        for action in (["_write", "team", str(block)], ["_bg_sync"], ["_vault_init"],
                       ["_rename", "old", "new"], ["_import_write", str(block)]):
            result = subprocess.run([app, *action], env=env, stdin=subprocess.DEVNULL,
                                    capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 65, action)
            self.assertEqual(snapshot(self.root), before, action)
        # A broken identity file must not reopen the legacy path.
        (store / ".hostbook/identity.json").unlink()
        before = snapshot(self.root)
        result = subprocess.run(["/bin/bash", "-c", '. "$1" _write team "$2"', "test", app, block],
                                env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 65)
        self.assertEqual(snapshot(self.root), before)

    def test_legacy_refusal_does_not_load_translations_from_caller(self):
        app = Path(__file__).resolve().parents[1] / "hostbook"
        elsewhere = self.root / "caller"
        translations = elsewhere / "lang"
        translations.mkdir(parents=True)
        (translations / "en.sh").write_text("L_err_legacy_store='caller supplied translation'\n")
        env = dict(os.environ, HOME=str(elsewhere), HOSTBOOK_DIR=str(self.root / "shared a"),
                   HOSTBOOK_LANG="en", HOSTBOOK_LANG_DIR=str(translations))
        for command in ([app, "_write"], ["/bin/bash", "-c", '. "$1" _write', "./caller", app]):
            result = subprocess.run(command, cwd=elsewhere, env=env, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 65)
            self.assertNotIn(b"caller supplied translation", result.stderr)

    def test_refusing_source_preserves_existing_store_context(self):
        app = Path(__file__).resolve().parents[1] / "hostbook"
        legacy = self.root / "legacy"
        legacy.mkdir()
        env = dict(os.environ, HOME=str(legacy), HOSTBOOK_DIR=str(legacy),
                   HOSTBOOK_LIB_DIR=str(app.parent / "lib"), HOSTBOOK_LANG_DIR=str(app.parent / "lang"))
        library = self.root / "hostbook-library"
        library.write_text(app.read_text().split("# ---------- entry point ----------")[0])
        input_file = self.root / "input"
        input_file.write_text("Host db\n")
        marked = self.root / "shared a"
        before = snapshot(marked)
        script = ('. "$1" || exit 1; HOSTBOOK_DIR=$2; . "$1" && exit 1; '
                  '[ "$DIR" = "$HOME" ] && write_host project "$3"')
        rc = subprocess.run(["/bin/bash", "-c", script, "test", library, marked, input_file],
                            cwd=marked, env=env, capture_output=True, timeout=5)
        self.assertEqual(rc.returncode, 0, rc.stderr)
        self.assertEqual((legacy / "project.conf").read_bytes(), input_file.read_bytes())
        self.assertEqual(snapshot(marked), before)

    def prepare_receive(self):
        store = self.root / "shared a"
        remote, author = self.root / "team.git", self.root / "author"
        env = dict(PATH=f"{Path(sys.executable).parent}:/usr/bin:/bin", HOME=str(self.root), HOSTBOOK_STATE=str(self.state),
                   XDG_CONFIG_HOME=str(self.root / "config"), HOSTBOOK_SSH_CONFIG=str(self.root / "ssh.conf"),
                   GIT_CONFIG_NOSYSTEM="1",
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_ALLOW_PROTOCOL="file", GIT_AUTHOR_NAME="fixture",
                   GIT_COMMITTER_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.test",
                   GIT_COMMITTER_EMAIL="fixture@example.test")
        def git(repo, *args):
            return subprocess.run(["/usr/bin/git", "-C", repo, *args], env=env, capture_output=True,
                                  check=True, timeout=10).stdout.strip().decode()
        git(store, "init", "-q", "--template=")
        git(store, "symbolic-ref", "HEAD", "refs/heads/main")
        source = store / "team.conf"
        source.write_text("Host db\n  HostName 192.0.2.1\n")
        identity_path = store / ".hostbook/identity.json"
        identity = json.loads(identity_path.read_bytes())
        identity["sources"] = [{"path": "team.conf", "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]
        identity["hosts"] = [{"host_id": new_id(), "name": "db", "bindings": [
            {"path":"team.conf", "host_ordinal":1, "token_ordinal":1}]}]
        identity_path.write_text(json.dumps(identity))
        git(store, "add", ".")
        git(store, "commit", "-qm", "Accepted")
        base = git(store, "rev-parse", "HEAD")
        git(self.root, "init", "-q", "--bare", "--template=", remote)
        git(store, "push", "-q", str(remote), "HEAD:refs/heads/main")
        git(self.root, "clone", "-qb", "main", "--no-local", str(remote), author)
        (author / "team.conf").write_text("Host db\n  HostName 192.0.2.2\n")
        identity["generation"] += 1
        identity["sources"][0]["sha256"] = hashlib.sha256((author / "team.conf").read_bytes()).hexdigest()
        (author / ".hostbook/identity.json").write_text(json.dumps(identity))
        git(author, "commit", "-qam", "Edit db")
        git(author, "push", "-q", str(remote), "HEAD:refs/heads/main")
        tip = git(author, "rev-parse", "HEAD")
        entry = self.entries[1]
        target = self.state / "stores/v1" / entry["store_id"] / "connection/3/target.json"
        target.parent.mkdir(parents=True, mode=0o700)
        for parent in (target.parent, target.parent.parent, target.parent.parent.parent):
            parent.chmod(0o700)
        target.write_text(json.dumps({"version": 1, "store_id": entry["store_id"],
                                    "target_id": entry["connection"]["target_id"],
                                    "connection_generation": 3, "ref": "refs/heads/main",
                                    "uid": os.geteuid(), "bare_repository": asdict(directory_binding(remote))},
                                   default=os.fspath))
        target.chmod(0o600)
        app = self.installed_launcher()
        request = {"account": asdict(self.account), "store_id": entry["store_id"],
                   "generations": asdict(self.generations)}
        def receive():
            return subprocess.run([app, "_store_receive"], cwd=self.root, env=dict(env,
                                  HOSTBOOK_DIR=str(store)), input=json.dumps(request, default=os.fspath).encode(),
                                  capture_output=True, timeout=15)
        return SimpleNamespace(store=store, remote=remote, author=author, base=base, tip=tip,
                               target=target, git=git, receive=receive, app=app, env=dict(env, HOSTBOOK_DIR=str(store)))

    def test_installed_callback_receives_exact_branch_into_clean_shared_store(self):
        f = self.prepare_receive()
        before_remote = snapshot(f.remote)
        before_b = snapshot(self.root / "shared b")
        result = f.receive()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["received"], f.tip)
        self.assertNotEqual(f.base, f.tip)
        self.assertEqual(f.git(f.store, "rev-parse", "HEAD"), f.tip)
        self.assertEqual((f.store / "team.conf").read_bytes(), (f.author / "team.conf").read_bytes())
        self.assertEqual(f.git(f.store, "status", "--porcelain"), "")
        f.git(f.store, "fsck", "--full", "--strict")
        self.assertEqual(snapshot(f.remote), before_remote)
        self.assertEqual(snapshot(self.root / "shared b"), before_b)

    def test_receive_uses_the_registered_reader_for_ssh_and_credentials(self):
        f = self.prepare_receive()
        path = f.author / "team.conf"
        manifest = f.author / ".hostbook/identity.json"
        def publish(text):
            path.write_text(text)
            metadata = json.loads(manifest.read_bytes())
            metadata["generation"] += 1
            metadata["sources"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest.write_text(json.dumps(metadata))
            f.git(f.author, "commit", "-qam", "Change fixture settings")
            f.git(f.author, "push", "-q", str(f.remote), "HEAD:refs/heads/main")
        for invalid in ("# secret: bad name\nHost db\n", "Host db\n  Port fixture-invalid\n"):
            publish(invalid)
            before = snapshot(f.store)
            result = f.receive()
            self.assertEqual(result.returncode, 65, result.stderr)
            self.assertEqual(snapshot(f.store), before)
        valid = "# secret: login\nHost db\n  HostName 192.0.2.2\n  IdentityFile ~/ssh-hosts/secrets/login.pub\n"
        publish(valid)
        result = f.receive()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((f.store / "team.conf").read_text(), valid)

    def test_receive_blocks_dirty_checkout_and_unverified_binding(self):
        f = self.prepare_receive()
        def refused():
            before = snapshot(f.store)
            result = f.receive()
            self.assertEqual(result.returncode, 65, result.stderr)
            self.assertEqual(snapshot(f.store), before)
            self.assertEqual(f.git(f.remote, "rev-parse", "refs/heads/main"), f.tip)
        unrelated = f.store / "notes"
        unrelated.write_text("untracked")
        refused()
        f.git(f.store, "add", "notes")
        unrelated.write_text("unstaged over staged")
        refused()
        f.git(f.store, "reset", "--", "notes")
        unrelated.unlink()
        binding = json.loads(f.target.read_bytes())
        for key, value in (("target_id", new_id()), ("ref", "refs/heads/other"), ("connection_generation", 4)):
            f.target.write_text(json.dumps(dict(binding, **{key: value})))
            refused()
        f.target.unlink()
        refused()

    def test_receive_preserves_accepted_snapshot_on_invalid_incoming(self):
        f = self.prepare_receive()
        path = f.author / "team.conf"
        path.write_text("Host db\n  HostName 192.0.2.33\n")
        f.git(f.author, "commit", "-qam", "Stale sidecar")
        f.git(f.author, "push", "-q", str(f.remote), "HEAD:refs/heads/main")
        before = snapshot(f.store)
        result = f.receive()
        self.assertEqual(result.returncode, 65, result.stderr)
        self.assertEqual(snapshot(f.store), before)

    def test_receive_preserves_data_when_fenced_interrupted_or_diverged(self):
        f = self.prepare_receive()
        before = snapshot(f.store)
        control = self.recovery / "control"
        control.mkdir(mode=0o700)
        intent = control / new_id()
        intent.mkdir(mode=0o700)
        self.assertEqual(f.receive().returncode, 65)
        self.assertEqual(snapshot(f.store), before)
        intent.rmdir()
        pending = f.target.parent / "receive.pending"
        pending.write_text("prior local receive requires reconciliation")
        self.assertEqual(f.receive().returncode, 65)
        self.assertEqual(snapshot(f.store), before)
        self.assertEqual(pending.read_text(), "prior local receive requires reconciliation")
        pending.unlink()
        f.git(f.store, "commit", "--allow-empty", "-qm", "Independent local commit")
        diverged = snapshot(f.store)
        self.assertEqual(f.receive().returncode, 65)
        self.assertEqual(snapshot(f.store), diverged)

    def test_receive_finds_pending_from_an_older_connection(self):
        f = self.prepare_receive()
        older = f.target.parent.parent / "2"
        older.mkdir(mode=0o700)
        marker = older / "receive.pending"
        marker.write_text("previous connection requires reconciliation")
        before = snapshot(f.store)
        result = f.receive()
        self.assertEqual(result.returncode, 65)
        self.assertEqual(snapshot(f.store), before)
        self.assertEqual(marker.read_text(), "previous connection requires reconciliation")

    def test_receive_marker_is_complete_before_discovery(self):
        import store_receive
        f = self.prepare_receive()
        marker = f.target.parent.parent.parent / "receive.pending"
        def interrupt(fd):
            if not marker.exists():
                raise OSError("simulated flush failure")
        with patch.object(store_receive, "sync_local", side_effect=interrupt):
            with self.assertRaises((OSError, StoreContextError)):
                store_receive.receive(lambda: self.account, self.entries[1]["store_id"], self.generations)
        self.assertFalse(marker.exists())
        self.assertFalse((f.target.parent / "receive.pending").exists())
        self.assertEqual(f.git(f.store, "rev-parse", "HEAD"), f.base)

    def check_receive_binding_replacement(self, git_only):
        import store_receive
        f = self.prepare_receive()
        publish = store_receive.publish_receive_marker
        replacement = f.store / ".git" if git_only else f.store
        before = snapshot(f.store)
        def replace_after_reservation(fd, record):
            publish(fd, record)
            original = self.root / "original"
            replacement.rename(original)
            shutil.copytree(original, replacement)
        with patch.object(store_receive, "publish_receive_marker", side_effect=replace_after_reservation):
            with self.assertRaises(StoreContextError):
                store_receive.receive(lambda: self.account, self.entries[1]["store_id"], self.generations)
        self.assertEqual(snapshot(f.store), before)
        self.assertTrue((f.target.parent.parent.parent / "receive.pending").is_file())

    def test_receive_never_writes_replacement_checkout(self):
        self.check_receive_binding_replacement(False)

    def test_receive_never_writes_replacement_git_directory(self):
        self.check_receive_binding_replacement(True)

    def test_receive_counts_unchanged_tracked_data_before_any_effect(self):
        import store_checkout
        import store_receive
        f = self.prepare_receive()
        base_size = sum((f.store / path).stat().st_size for path in
                        ("team.conf", ".hostbook/identity.json", "secrets/vault.pub"))
        source = f.author / "team.conf"
        source.write_text(source.read_text() + "# Some additional incoming bytes\n")
        manifest = f.author / ".hostbook/identity.json"
        metadata = json.loads(manifest.read_bytes())
        metadata["generation"] += 1
        metadata["sources"][0]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(metadata))
        f.git(f.author, "commit", "-qam", "Increase host configuration length")
        f.git(f.author, "push", "-q", str(f.remote), "HEAD:refs/heads/main")
        before = snapshot(f.store)
        with patch.object(store_checkout, "MAX_STORE_BYTES", base_size + 1):
            with self.assertRaisesRegex(StoreContextError, "size limit"):
                store_receive.receive(lambda: self.account, self.entries[1]["store_id"], self.generations)
        self.assertEqual(snapshot(f.store), before)
        self.assertFalse((f.target.parent.parent.parent / "receive.pending").exists())

    def transition_record(self, store, phases):
        ident = new_id()
        root = self.recovery / "control"
        root.mkdir(mode=0o700, exist_ok=True)
        folder = root / ident
        folder.mkdir(mode=0o700)
        (folder / "records").mkdir(mode=0o700)
        owner = {"owner_id": new_id(), "process_id": 1, "process_start_id": new_id(), "boot_id": new_id()}
        detail = {"old_binding_id": new_id(), "proposed_binding_id": new_id()}
        for sequence, phase in enumerate(phases, 1):
            current = {"version": 1, "record_id": ident, "sequence": sequence}
            record = dict(current, format="hostbook-control", device_id=self.account.device_id,
                          root_id=self.account.recovery_root_id, previous_sequence=sequence-1, store_id=store,
                          owner=owner, operation="registration-transition", detail=dict(detail, phase=phase))
            path = folder / "records" / f"{sequence}.json"
            path.write_text(json.dumps(record))
            path.chmod(0o600)
        path = folder / "current.json"
        path.write_text(json.dumps(current))
        path.chmod(0o600)
        return folder

    def test_receive_obeys_only_applicable_unsettled_controls(self):
        f = self.prepare_receive()
        self.transition_record(self.entries[2]["store_id"], ["fenced"])
        self.transition_record(self.entries[1]["store_id"], ["fenced", "applied"])
        result = f.receive()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.transition_record(self.entries[1]["store_id"], ["fenced"])
        result = f.receive()
        self.assertEqual(result.returncode, 65, result.stderr)

    def test_receive_preserves_other_stores_transfer_and_blocks_unowned_recovery(self):
        f = self.prepare_receive()
        ident = new_id()
        folder = self.recovery / "transactions" / ident
        for d in (folder.parent, folder, folder / "records"):
            d.mkdir(mode=0o700)
        selector = {"version": 1, "transaction_id": ident, "sequence": 1}
        stores = [dict(store_id=self.entries[i]["store_id"], role=role, snapshot_id=new_id(),
                       checkout_generation=1, connection_generation=1, identity_generation=1, vault_generation=1)
                  for i, role in ((0, "source"), (2, "destination"))]
        record = dict(selector, format="hostbook-transaction", device_id=self.account.device_id,
                      root_id=self.account.recovery_root_id, previous_sequence=0, operation="transfer",
                      checkpoint="prepared", owner=dict(owner_id=new_id(), process_id=1,
                      process_start_id=new_id(), boot_id=new_id()), stores=stores,
                      hosts=[dict(store_id=stores[0]["store_id"], host_id=new_id())], payload_id=new_id(),
                      recovery_generation=1, effects=[], attempts=[], collection=dict(decision_id=None,
                      payload_state="retained", payload_ids=[], recovery_generations=[]))
        for path, obj in ((folder / "current.json", selector), (folder / "records/1.json", record)):
            path.write_text(json.dumps(obj))
            path.chmod(0o600)
        result = f.receive()
        self.assertEqual(result.returncode, 0, result.stderr)
        before = snapshot(f.store)
        record["device_id"] = new_id()
        (folder / "records/1.json").write_text(json.dumps(record))
        result = f.receive()
        self.assertEqual(result.returncode, 65, result.stderr)
        self.assertEqual(snapshot(f.store), before)

    def test_receive_preserves_an_editor_save_between_validation_and_install(self):
        import store_checkout
        import store_receive
        f = self.prepare_receive()
        source = f.store / "team.conf"
        human = b"Host db\n  HostName 192.0.2.99\n"
        install, check = store_checkout.install_sources, store_checkout.check_contents
        saved = False
        def editor(fd, name, blob, *args, **kwargs):
            nonlocal saved
            result = check(fd, name, blob, *args, **kwargs)
            if name == "team.conf" and not saved:
                other = f.store / "editor-save"
                other.write_bytes(human)
                other.replace(source)
                saved = True
            return result
        def interleaved(*args, **kwargs):
            with patch.object(store_checkout, "check_contents", side_effect=editor):
                return install(*args, **kwargs)
        with patch.object(store_checkout, "install_sources", side_effect=interleaved):
            with self.assertRaises(StoreContextError):
                store_receive.receive(lambda: self.account, self.entries[1]["store_id"], self.generations)
        self.assertTrue(saved)
        self.assertEqual(source.read_bytes(), human)
        self.assertEqual(f.git(f.store, "rev-parse", "HEAD"), f.base)
        self.assertTrue((f.target.parent.parent.parent / "receive.pending").is_file())

    def test_receive_preserves_filemode_change(self):
        f = self.prepare_receive()
        f.git(f.store, "config", "core.filemode", "true")
        (f.store / "team.conf").chmod(0o755)
        before = snapshot(f.store)
        result = f.receive()
        self.assertEqual(result.returncode, 65)
        self.assertEqual(snapshot(f.store), before)
        self.assertFalse((f.target.parent.parent.parent / "receive.pending").exists())

    def test_receive_refuses_untracked_empty_directory_incoming_file_collision(self):
        f = self.prepare_receive()
        extra = f.author / "other.conf"
        extra.write_text("Host other\n  HostName 192.0.2.3\n")
        manifest = f.author / ".hostbook/identity.json"
        metadata = json.loads(manifest.read_bytes())
        metadata["generation"] += 1
        metadata["sources"].append(dict(path=extra.name, sha256=hashlib.sha256(extra.read_bytes()).hexdigest()))
        metadata["hosts"].append(dict(host_id=new_id(), name="other", bindings=[
            dict(path=extra.name, host_ordinal=1, token_ordinal=1)]))
        manifest.write_text(json.dumps(metadata))
        f.git(f.author, "add", ".")
        f.git(f.author, "commit", "-qm", "New source")
        f.git(f.author, "push", "-q", str(f.remote), "HEAD:refs/heads/main")
        (f.store / extra.name).mkdir()
        before = snapshot(f.store)
        self.assertEqual(f.receive().returncode, 65)
        self.assertEqual(snapshot(f.store), before)
        self.assertFalse((f.target.parent.parent.parent / "receive.pending").exists())

    def test_recovery_labels_cannot_settle_earlier_effects_or_unselected_migration(self):
        import store_journal
        def history(operation, states, selected, effects, collection):
            parent = self.recovery / "transactions"
            parent.mkdir(mode=0o700, exist_ok=True)
            ident = new_id()
            folder = parent / ident
            folder.mkdir(mode=0o700)
            (folder / "records").mkdir(mode=0o700)
            source, destination = self.entries[1]["store_id"], self.entries[2]["store_id"]
            scope = [dict(store_id=s, role=r, snapshot_id=new_id(), checkout_generation=1,
                     connection_generation=1, identity_generation=1, vault_generation=1)
                     for s, r in ((source, "source"), (destination, "destination"))]
            owner = dict(owner_id=new_id(), process_id=1, boot_id=new_id(), process_start_id=new_id())
            rec = dict(format="hostbook-transaction", version=1, transaction_id=ident,
                       device_id=self.account.device_id, root_id=self.account.recovery_root_id, operation=operation,
                       owner=owner, stores=scope, hosts=[dict(store_id=source, host_id=new_id())], attempts=[],
                       payload_id=new_id(), recovery_generation=1)
            for seq, state in enumerate(states, 1):
                file = folder / "records" / f"{seq}.json"
                file.write_text(json.dumps(dict(rec, checkpoint=state, sequence=seq, previous_sequence=seq-1,
                                               effects=effects if seq > 1 else [], collection=collection)))
                file.chmod(0o600)
            selector = folder / "current.json"
            selector.write_text(json.dumps(dict(version=1, transaction_id=ident, sequence=selected)))
            selector.chmod(0o600)
            return folder
        fd = os.open(self.recovery, store_context.DIRECTORY_FLAGS)
        self.addCleanup(os.close, fd)
        def idle(index):
            store_journal.require_idle(fd, self.account, self.entries[index]["store_id"])
        retained = dict(decision_id=None, payload_state="retained", payload_ids=[], recovery_generations=[])
        folder = history("migration", ["prepared", "cancelled"], 1, [], retained)
        with self.assertRaises(StoreContextError):
            idle(0)
        shutil.rmtree(folder)
        folder = history("migration", ["prepared", "staged", "cancelled", "cancelled"], 4, [], retained)
        with self.assertRaises(StoreContextError):
            idle(0)
        shutil.rmtree(folder)
        effect = dict(effect_id=new_id(), store_id=self.entries[1]["store_id"], intent_id=new_id(),
                      evidence_id=None, outcome="intended")
        for terminal in ("cancelled", "rolled-back"):
            folder = history("transfer", ["prepared", "destination-saved", terminal], 3, [effect], retained)
            with self.assertRaises(StoreContextError):
                idle(1)
            idle(0)
            shutil.rmtree(folder)
        invalid_collection = dict(retained, payload_ids=[new_id()])
        folder = history("transfer", ["prepared", "cancelled"], 2, [], invalid_collection)
        with self.assertRaises(StoreContextError):
            idle(1)
        idle(0)
        shutil.rmtree(folder)
        history("transfer", ["prepared", "cancelled"], 2, [], retained)
        idle(1)

    def test_receive_installs_only_new_objects_and_preserves_native_reflogs(self):
        f = self.prepare_receive()
        known = {line.split()[0] for line in f.git(f.store, "rev-list", "--objects", f.base).splitlines()}
        packs = f.store / ".git/objects/pack"
        previous = set(packs.glob("*.idx")) if packs.is_dir() else set()
        for change in range(2):
            if change:
                source = f.author / "team.conf"
                source.write_text("Host db\n  HostName 192.0.2.8\n")
                path = f.author / ".hostbook/identity.json"
                meta = json.loads(path.read_bytes())
                meta["generation"] += 1
                meta["sources"][0]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
                path.write_text(json.dumps(meta))
                f.git(f.author, "commit", "-qam", "Second host-only edit")
                f.git(f.author, "push", "-q", str(f.remote), "HEAD:refs/heads/main")
            tip = f.git(f.author, "rev-parse", "HEAD")
            result = f.receive()
            self.assertEqual(result.returncode, 0, result.stderr)
            fresh = set(packs.glob("*.idx")) - previous
            self.assertEqual(len(fresh), 1)
            listing = f.git(f.store, "verify-pack", "-v", str(next(iter(fresh))))
            objects = {line.split()[0] for line in listing.splitlines() if len(line.split()[0]) == 40}
            self.assertIn(tip, objects)
            self.assertFalse(known & objects)
            for ref in ("HEAD", "refs/heads/main"):
                self.assertEqual(f.git(f.store, "reflog", "show", "-1", "--format=%H", ref), tip)
            f.git(f.store, "fsck", "--full", "--strict")
            known.update(objects)
            previous.update(fresh)

    def test_installed_callback_publishes_one_conditional_registered_host_edit(self):
        import store_read
        f = self.prepare_receive()
        self.assertEqual(f.receive().returncode, 0)
        entry = self.entries[1]
        store_id = entry["store_id"]
        request = {"account": asdict(self.account), "store_id": store_id,
                   "generations": asdict(self.generations), "address": "192.0.2.88"}
        app = f.app
        def edit():
            row = store_read.refresh_routes(lambda: self.account, store_id, self.generations).strip().split("\t")
            return subprocess.run([app, "_store_edit_address", row[1], row[3]], cwd=self.root, env=f.env,
                                  input=json.dumps(request, default=os.fspath).encode(),
                                  capture_output=True, timeout=30)
        before = snapshot(f.store)
        self.assertEqual(edit().returncode, 65)
        self.assertEqual(snapshot(f.store), before)
        self.assertEqual(f.git(f.remote, "rev-parse", "refs/heads/main"), f.tip)
        entry["effective_mode"] = "two-way"
        self.save_registry()
        result = edit()
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["outcome"], "accepted")
        tip = f.git(f.remote, "rev-parse", "refs/heads/main")
        self.assertEqual(f.git(f.remote, "rev-parse", tip + "^"), f.tip)
        self.assertEqual(f.git(f.remote, "show", tip + ":team.conf"), "Host db\n  HostName 192.0.2.88")
        self.assertEqual(f.git(f.store, "rev-parse", "HEAD"), tip)
        self.assertEqual(f.git(f.store, "status", "--porcelain"), "")
        control = self.recovery / "control" / record["attempt_id"]
        first = json.loads((control / "records/1.json").read_bytes())
        self.assertEqual(first["detail"]["outcome"], "reserved")
        self.assertEqual(first["detail"]["base_oid"], f.tip)
        self.assertEqual(first["detail"]["proposed_oid"], tip)
        selector = json.loads((control / "current.json").read_bytes())
        last = json.loads((control / "records" / f"{selector['sequence']}.json").read_bytes())
        self.assertEqual((last["detail"]["sender_state"], last["detail"]["outcome"]), ("quiescent", "accepted"))

    def prepare_edit(self):
        import store_publish
        import store_read
        f = self.prepare_receive()
        self.assertEqual(f.receive().returncode, 0)
        entry = self.entries[1]
        entry["effective_mode"] = "two-way"
        self.save_registry()
        store_id = entry["store_id"]
        load = lambda: self.account
        row = store_read.refresh_routes(load, store_id, self.generations).strip().split("\t")
        def edit():
            return store_publish.publish_address(load, store_id, self.generations, row[1], row[3], "192.0.2.88")
        def publish(text, add_host=False):
            path = f.author / "team.conf"
            path.write_text(text)
            manifest = f.author / ".hostbook/identity.json"
            meta = json.loads(manifest.read_bytes())
            meta["generation"] += 1
            meta["sources"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            if add_host:
                meta["hosts"].append(dict(name="web", host_id=new_id(), bindings=[
                    dict(path="team.conf", host_ordinal=2, token_ordinal=1)]))
            manifest.write_text(json.dumps(meta))
            f.git(f.author, "commit", "-qam", "Independent remote fixture change")
            f.git(f.author, "push", "-q", str(f.remote), "HEAD:refs/heads/main")
        return f, edit, publish

    def test_edit_preserves_independent_remote_host_and_unrelated_local_work(self):
        f, edit, publish = self.prepare_edit()
        publish("Host db\n  HostName 192.0.2.2\nHost web\n  HostName 192.0.2.44\n", add_host=True)
        prior = f.git(f.author, "rev-parse", "HEAD")
        notes = f.store / "notes"
        notes.write_text("staged")
        f.git(f.store, "add", "notes")
        notes.write_text("unstaged")
        before = snapshot(f.store)
        result = edit()
        self.assertEqual(result["outcome"], "accepted")
        self.assertEqual(result["local"], "reconciliation-required")
        self.assertEqual(snapshot(f.store), before)
        tip = f.git(f.remote, "rev-parse", "refs/heads/main")
        self.assertEqual(f.git(f.remote, "rev-parse", tip + "^"), prior)
        self.assertEqual(f.git(f.remote, "show", tip + ":team.conf"),
                         "Host db\n  HostName 192.0.2.88\nHost web\n  HostName 192.0.2.44")
        self.assertNotIn("notes", f.git(f.remote, "ls-tree", "-r", "--name-only", tip).splitlines())

    def test_edit_refuses_same_host_revision_even_if_address_changes_back(self):
        f, edit, publish = self.prepare_edit()
        publish("Host db\n  HostName 192.0.2.33\n")
        publish("Host db\n  HostName 192.0.2.2\n")
        before, remote = snapshot(f.store), snapshot(f.remote)
        with self.assertRaisesRegex(StoreContextError, "host revision changed"):
            edit()
        self.assertEqual(snapshot(f.store), before)
        self.assertEqual(snapshot(f.remote), remote)
        self.assertFalse((self.recovery / "control").exists())
        self.assertFalse((self.recovery / "edits").exists())

    def test_edit_refuses_changed_bound_credential_across_blank_comment_lines(self):
        import store_publish
        import store_read
        f, _, publish = self.prepare_edit()
        store = self.entries[1]["store_id"]
        load = lambda: self.account
        def bound(name):
            return "# secret: " + name + "\n\n# local note\nHost db\n  HostName 192.0.2.2\n"
        publish(bound("old-login"))
        self.assertEqual(f.receive().returncode, 0)
        row = store_read.refresh_routes(load, store, self.generations).strip().split("\t")
        publish(bound("new-login"))
        before, remote = snapshot(f.store), snapshot(f.remote)
        with self.assertRaisesRegex(StoreContextError, "host revision changed"):
            store_publish.publish_address(load, store, self.generations, row[1], row[3], "192.0.2.88")
        self.assertEqual(snapshot(f.store), before)
        self.assertEqual(snapshot(f.remote), remote)
        self.assertFalse((self.recovery / "control").exists())

    def test_edit_rejected_at_send_retains_exact_candidate_without_local_adoption(self):
        import store_publish
        f, edit, publish = self.prepare_edit()
        send = store_publish.send
        def race(*args):
            publish("Host db\n  HostName 192.0.2.77\n")
            return send(*args)
        before = snapshot(f.store)
        with patch.object(store_publish, "send", side_effect=race):
            result = edit()
        self.assertEqual(result["outcome"], "not-accepted")
        self.assertEqual(snapshot(f.store), before)
        self.assertEqual(f.git(f.remote, "show", "refs/heads/main:team.conf"), "Host db\n  HostName 192.0.2.77")
        bundle = self.recovery / "edits" / result["attempt_id"] / "proposal.bundle"
        self.assertTrue(bundle.is_file())
        self.assertEqual(bundle.stat().st_mode & 0o777, 0o600)

    def test_settle_lost_acknowledgement_against_original_ref_after_restart(self):
        import store_publish
        f, edit, _ = self.prepare_edit()
        send = store_publish.send
        def lose_ack(*args):
            self.assertEqual(send(*args), "accepted")
            return "uncertain"
        before = snapshot(f.store)
        with patch.object(store_publish, "send", side_effect=lose_ack):
            result = edit()
        self.assertEqual(result["outcome"], "uncertain")
        with self.assertRaisesRegex(StoreContextError, "unresolved recovery"):
            edit()
        entry = self.entries[1]
        attempt = result["attempt_id"]
        entry["connection"]["ref"] = "refs/heads/replacement"
        entry["connection_generation"] += 1
        self.save_registry()
        f.git(f.remote, "update-ref", entry["connection"]["ref"], f.tip)
        request = json.dumps({"account":asdict(self.account),"store_id":entry["store_id"],
                              "generations":asdict(replace(self.generations, connection=4))}, default=os.fspath)
        def settle():
            return subprocess.run([f.app, "_store_settle", attempt], cwd=self.root, env=f.env,
                                  input=request, text=True, capture_output=True, timeout=30)
        receipt = self.recovery / "edits" / attempt
        with (receipt / "lease").open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertNotEqual(settle().returncode, 0)
        resolved = settle()
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        self.assertEqual(json.loads(resolved.stdout)["outcome"], "accepted")
        self.assertEqual(snapshot(f.store), before)
        self.assertEqual(f.git(f.remote,"rev-parse",entry["connection"]["ref"]), f.tip)
        self.assertEqual(json.loads(settle().stdout)["outcome"], "accepted")

    def test_settle_absent_unknown_candidate_preserves_uncertainty_and_bundle(self):
        import store_publish
        f, edit, _ = self.prepare_edit()
        before = snapshot(f.store)
        with patch.object(store_publish, "send", return_value="uncertain"):
            result = edit()
        args = (lambda: self.account, self.entries[1]["store_id"], self.generations, result["attempt_id"])
        self.assertEqual(store_publish.settle(*args)["outcome"], "uncertain")
        self.assertEqual(snapshot(f.store), before)
        self.assertEqual(f.git(f.remote, "rev-parse", "refs/heads/main"), f.tip)
        self.assertTrue((self.recovery / "edits" / result["attempt_id"] / "proposal.bundle").is_file())

    def test_receive_rejects_a_linked_tracked_file(self):
        f = self.prepare_receive()
        os.link(f.store / "secrets/vault.pub", self.root / "other-public-key")
        before = snapshot(f.store)
        result = f.receive()
        self.assertEqual(result.returncode, 65, result.stderr)
        self.assertEqual(snapshot(f.store), before)

    def test_receive_rejects_an_unbound_source_retained_by_sidecar_edit(self):
        f = self.prepare_receive()
        self.assertEqual(f.receive().returncode, 0)
        manifest = f.author / ".hostbook/identity.json"
        meta = json.loads(manifest.read_bytes())
        meta.update(generation=3, hosts=[], sources=[])
        manifest.write_text(json.dumps(meta))
        f.git(f.author, "commit", "-qam", "Drop live source from identity")
        f.git(f.author, "push", "-q", str(f.remote), "HEAD:refs/heads/main")
        before = snapshot(f.store)
        result = f.receive()
        self.assertEqual(result.returncode, 65, result.stderr)
        self.assertEqual(snapshot(f.store), before)

    def test_ssh_view_bounds_generated_configuration_across_files(self):
        import store_read
        part = b"ProxyJump [fe80::1%en0],a,b,c\n"
        self.assertLess(2 * len(part), 80)
        with patch.object(store_read, "MAX_ROUTE_BYTES", 80):
            with self.assertRaisesRegex(StoreContextError, "size limit"):
                with store_read.ssh_view([part, part], (None, None, None)):
                    pass

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
    HostName "{endpoint}"
    User "login-{index}@example.org"
    ProxyJump "local-hop"
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
                                 (endpoint, f"login-{index}@example.org", "2222", "local-hop"))
                defaults.write_text(defaults.read_text().replace("approved-hop", "changed-hop"))
                stale = self.route_callback(context, "_store_preview", listed_id, stamp)
                self.assertEqual(stale.returncode, 65)
                self.assertIn("selection changed", stale.stderr)
                self.assertEqual(stale.stdout, "")
                defaults.write_text(defaults.read_text().replace("changed-hop", "approved-hop"))

        renamed = self.write_route(1, host_id, "Host renamed\n    HostName 198.51.100.10\n", "renamed")
        self.assertIsNotNone(shared_a_stamp)
        stale = self.route_callback(renamed, "_store_preview", host_id, shared_a_stamp)
        self.assertEqual(stale.returncode, 65)
        listing = self.route_callback(renamed, "_store_hosts")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertEqual(listing.stdout.split("\t")[:3], [renamed.store_id, host_id, "renamed"])

    def test_installed_callback_connects_through_the_selected_local_jump(self):
        self.launcher = self.installed_launcher()
        host_id = new_id()
        with socket.socket() as hop, socket.socket() as target:
            hop.bind(("127.0.0.1", 0))
            hop.listen()
            hop.settimeout(5)
            target.bind(("127.0.0.1", 0))
            target.listen()
            target.setblocking(False)
            context = self.write_route(1, host_id, f"""Host db
    HostName 127.0.0.1
    Port {target.getsockname()[1]}
    User destination
    ProxyJump "ops+relay@approved-hop"
""")
            local = self.state / f"stores/v1/{context.store_id}/ssh/config"
            local.parent.mkdir(parents=True, mode=0o700)
            local.parent.parent.chmod(0o700)
            known_hosts = local.parent / "known_hosts"
            known_hosts.touch(mode=0o600)
            local.write_text(f"""Host approved-hop
    HostName 127.0.0.1
    User jump
    Port {hop.getsockname()[1]}
Host *
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
            with self.assertRaises(BlockingIOError):
                target.accept()

            local.write_text(local.read_text().replace("User jump", "User stale"))
            stale = self.route_callback(context, "_store_connect", host_id, stamp)
            self.assertEqual(stale.returncode, 65)
            self.assertIn("selection changed", stale.stderr)

    def test_registered_connect_requires_protected_store_key_trust_before_ssh(self):
        from store_read import connect_route, refresh_routes

        host_id = new_id()
        context = self.write_route(1, host_id, "Host db\n    HostName 192.0.2.5\n")
        load = lambda: self.account
        real_run = subprocess.run
        launches = []

        def intercept(command, *args, **kwargs):
            if command[0] != "/usr/bin/ssh" or "-G" in command:
                return real_run(command, *args, **kwargs)
            launches.append(command)
            settings = real_run([*command[:3], "-G", *command[3:]], capture_output=True,
                                text=True, check=True, timeout=5).stdout
            fields = dict(line.split(" ", 1) for line in settings.splitlines())
            expected_tags = {"stricthostkeychecking": "true", "updatehostkeys": "false"}
            for key, value in expected_tags.items():
                self.assertIn(fields[key], (value, "yes" if value == "true" else "no"))
            self.assertEqual(Path(fields["userknownhostsfile"]).read_bytes(), b"local-trust-fixture\n")
            self.assertEqual(Path(fields["globalknownhostsfile"]).read_bytes(), b"global-trust-fixture\n")
            return subprocess.CompletedProcess(command, 0)

        def listing():
            return refresh_routes(load, context.store_id, self.generations).strip().split("\t")[3]

        with patch("subprocess.run", side_effect=intercept):
            with self.assertRaisesRegex(StoreContextError, "requires store-local known_hosts"):
                connect_route(load, context.store_id, self.generations, host_id, listing())
            self.assertEqual(launches, [])

            ssh = self.state / f"stores/v1/{context.store_id}/ssh"
            ssh.mkdir(mode=0o700)
            known_hosts = ssh / "known_hosts"
            known_hosts.write_bytes(b"local-trust-fixture\n")
            known_hosts.chmod(0o600)
            globals_ = ssh / "global_known_hosts"
            globals_.write_bytes(b"global-trust-fixture\n")
            globals_.chmod(0o600)
            self.assertEqual(connect_route(load, context.store_id, self.generations, host_id, listing()), 0)
            self.assertEqual(len(launches), 1)

    def test_registered_vault_tools_cannot_resolve_into_the_selected_checkout(self):
        from store_login import verified_age_tools

        checkout = self.resolve(1).checkout
        package = checkout / "1.0" / "bin"
        package.mkdir(parents=True)
        with tempfile.TemporaryDirectory(prefix="hb-tools-", dir="/tmp") as installed:
            directory = Path(installed).resolve()
            for program in ("age", "age-keygen", "age-plugin-batchpass"):
                tool = package / program
                tool.write_text("#!/bin/sh\nexit 1\n")
                tool.chmod(0o700)
                (directory / program).symlink_to(tool)
            with patch("store_login.age_installations", return_value=((directory, checkout),)):
                with self.assertRaisesRegex(StoreContextError, "in an approved tool directory"):
                    verified_age_tools(self.account)

    def test_registered_vault_tools_reject_group_writable_install_ancestry(self):
        from store_login import verified_age_tools

        with tempfile.TemporaryDirectory(prefix="hb-tools-", dir="/tmp") as installed:
            directory = Path(installed).resolve() / "install" / "bin"
            directory.mkdir(parents=True)
            directory.parent.chmod(0o770)
            for program in ("age", "age-keygen", "age-plugin-batchpass"):
                tool = directory / program
                tool.write_text("#!/bin/sh\nexit 1\n")
                tool.chmod(0o700)
            with patch("store_login.age_installations", return_value=((directory, None),)), \
                    patch("store_login.tool_ancestry", return_value=(directory, directory.parent)):
                with self.assertRaisesRegex(StoreContextError, "in an approved tool directory"):
                    verified_age_tools(self.account)

    def test_public_connect_execs_the_registered_launcher_in_place(self):
        fixture_bin = self.root / "bin-stub"
        fixture_bin.mkdir()
        interpreter = fixture_bin / "python3"
        interpreter.write_text('#!/bin/sh\nprintf "%s\\n" "$$"\n')
        interpreter.chmod(0o700)
        launcher = self.installed_launcher()
        env = {"PATH": f"{fixture_bin}:/bin:/usr/bin", "HOME": str(self.root / "fixture-home")}
        with subprocess.Popen([launcher, "store-connect"], stdout=subprocess.PIPE, env=env) as worker:
            stdout, _ = worker.communicate(timeout=5)
            self.assertEqual(worker.returncode, 0)
            self.assertEqual(int(stdout), worker.pid)

    def test_private_agent_exits_after_its_launcher_is_killed(self):
        lib_dir = Path(__file__).resolve().parents[1] / "lib"
        script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from store_login import ssh_agent
with ssh_agent(Path(sys.argv[2])):
    print("ready", flush=True)
    sys.stdin.read()
"""
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.root / "fixture-home")}
        for signum in (signal.SIGTERM, signal.SIGKILL):
            with self.subTest(signum=signum):
                with tempfile.TemporaryDirectory(prefix="hb-agent-", dir="/tmp") as folder:
                    agent = Path(folder) / "agent"
                    worker = subprocess.Popen([sys.executable, "-B", "-c", script, str(lib_dir), str(agent)],
                                              start_new_session=True, stdin=subprocess.PIPE,
                                              stdout=subprocess.PIPE, text=True, env=env)
                    try:
                        self.assertTrue(select.select([worker.stdout], [], [], 5)[0])
                        self.assertEqual(worker.stdout.readline().strip(), "ready")
                        self.assertTrue(agent.is_socket())
                        worker.send_signal(signum)
                        worker.wait(timeout=5)
                        deadline = time.monotonic() + 3
                        while agent.exists() and time.monotonic() < deadline:
                            time.sleep(0.02)
                        self.assertFalse(agent.exists())
                    finally:
                        worker.stdin.close()
                        worker.stdout.close()
                        if worker.poll() is None or agent.exists():
                            try:
                                os.killpg(worker.pid, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                        worker.wait(timeout=5)

    def test_registered_connect_offers_the_key_from_the_selected_store_only(self):
        from store_read import connect_route, refresh_routes

        paths = [shutil.which(tool) for tool in ("age", "age-keygen", "age-plugin-batchpass")]
        if not all(paths):
            self.skipTest("requires age, age-keygen and age-plugin-batchpass")
        tools = Path(paths[0]).parent
        agent_tools = ("/usr/bin/ssh-agent", "/usr/bin/ssh-add", "/usr/bin/ssh-keygen")
        if not all(os.access(tool, os.X_OK) for tool in agent_tools):
            self.skipTest("requires system OpenSSH key and agent tools")
        fixture_env = {"PATH": str(tools), "HOME": str(self.root / "fixture-home")}
        passphrase = b"disposable vault unlock"
        host_id = new_id()
        load = lambda: self.account

        def tool(name, *args, data=None, env=fixture_env, pass_fds=()):
            return subprocess.run([tools / name, *args], input=data, env=env, pass_fds=pass_fds,
                                  capture_output=True, check=True, timeout=30).stdout

        for index in range(3):
            with self.subTest(store=index):
                context = self.write_route(index, host_id, f"""# secret: login
Host db
    HostName 192.0.2.{index + 10}
    User fixture
    IdentityFile ~/ssh-hosts/secrets/login.pub
    IdentitiesOnly yes
""")
                ssh = self.state / f"stores/v1/{context.store_id}/ssh"
                ssh.mkdir(parents=True, mode=0o700)
                ssh.parent.chmod(0o700)
                (ssh / "known_hosts").touch(mode=0o600)
                identity = tool("age-keygen")
                recipient = tool("age-keygen", "-y", data=identity)
                (context.vault / "vault.pub").write_bytes(recipient)
                reader, writer = os.pipe()
                try:
                    with os.fdopen(writer, "wb") as stream:
                        stream.write(passphrase)
                    envelope = tool("age", "-e", "-j", "batchpass", data=identity, pass_fds=(reader,),
                                    env=dict(fixture_env, AGE_PASSPHRASE_FD=str(reader)))
                finally:
                    os.close(reader)
                (context.vault / "vault.key.age").write_bytes(envelope)
                (context.vault / "vault.recovery.age").write_bytes(envelope)
                key = self.root / f"key-{index}"
                subprocess.run([agent_tools[2], "-q", "-t", "ed25519", "-N", "", "-f", key],
                               env=fixture_env, check=True, timeout=10)
                public_key = (key.with_suffix(".pub")).read_bytes()
                (context.vault / "login.pub").write_bytes(public_key)
                (context.vault / "login.meta").write_bytes(b"type=key\n")
                ciphertext = tool("age", "-e", "-r", recipient.decode().strip(), data=key.read_bytes())
                (context.vault / "login.age").write_bytes(ciphertext)
                stamp = refresh_routes(load, context.store_id, self.generations).strip().split("\t")[3]
                commands = context.checkout / "fake-bin"
                commands.mkdir()
                marker = self.root / f"checkout-tool-{index}-ran"
                for program in ("age", "age-keygen", "age-plugin-batchpass"):
                    fake = commands / program
                    fake.write_text(f"#!/bin/sh\n: > '{marker}'\nexit 97\n")
                    fake.chmod(0o700)
                before = snapshot(self.root)

                sockets = []

                def launch(config, alias):
                    result = subprocess.run(["/usr/bin/ssh", "-F", config, "-G", alias], env=fixture_env,
                                            capture_output=True, check=True, timeout=5)
                    settings = [line.split(b" ", 1) for line in result.stdout.splitlines()]
                    details = dict(settings)
                    self.assertEqual(details[b"hostname"], f"192.0.2.{index + 10}".encode())
                    self.assertTrue(details[b"identityagent"].startswith(b"/tmp/hb-route-"))
                    sockets.append(Path(os.fsdecode(details[b"identityagent"])))
                    self.assertEqual(details[b"identitiesonly"], b"yes")
                    public_files = [Path(os.fsdecode(value)) for key, value in settings
                                    if key == b"identityfile" and value != b"none"]
                    self.assertEqual(len(public_files), 1)
                    self.assertEqual(public_files[0].read_bytes().split()[:2], public_key.split()[:2])
                    keys = subprocess.run([agent_tools[1], "-L"], input=b"", capture_output=True, check=True,
                                          env=dict(fixture_env, SSH_AUTH_SOCK=os.fsdecode(details[b"identityagent"])),
                                          timeout=5).stdout
                    self.assertEqual(len(keys.splitlines()), 1)
                    self.assertEqual(keys.split()[:2], public_key.split()[:2])
                    return 0

                override = {"PATH": f"{commands}:{os.environ['PATH']}"} if index == 0 else {}
                with patch.dict(os.environ, override):
                    with patch("store_read.launch_ssh", side_effect=launch) as transport:
                        self.assertEqual(connect_route(load, context.store_id, self.generations, host_id, stamp,
                                                       read_passphrase=lambda: passphrase), 0)
                        transport.assert_called_once()
                self.assertFalse(marker.exists())
                self.assertEqual(len(sockets), 1)
                self.assertFalse(sockets[0].exists())
                self.assertEqual(snapshot(self.root), before)

                if index == 2:
                    with tempfile.TemporaryDirectory(prefix="hb-tools-", dir="/tmp") as installed:
                        program_dir = Path(installed).resolve()
                        binary = program_dir / "age"
                        for program in ("age", "age-keygen", "age-plugin-batchpass"):
                            path = program_dir / program
                            path.write_text("#!/bin/sh\nexit 1\n")
                            path.chmod(0o700)

                        def replace_tool():
                            binary.write_text(f"#!/bin/sh\n: > '{marker}'\nexit 97\n")
                            return passphrase

                        with patch("store_login.age_installations", return_value=((program_dir, None),)), \
                                patch("store_login.tool_ancestry", return_value=(program_dir,)):
                            with self.assertRaisesRegex(StoreContextError, "vault tools changed during unlock"):
                                connect_route(load, context.store_id, self.generations, host_id, stamp,
                                              read_passphrase=replace_tool)
                        self.assertFalse(marker.exists())

                def rotate_during_unlock():
                    (context.vault / "login.age").write_bytes(ciphertext + b"changed")
                    return passphrase

                with patch("store_read.launch_ssh", create=True) as transport:
                    with self.assertRaisesRegex(StoreContextError, "credential changed"):
                        connect_route(load, context.store_id, self.generations, host_id, stamp,
                                      read_passphrase=rotate_during_unlock)
                    transport.assert_not_called()

                if index == 1:
                    source = context.checkout / "team.conf"
                    text = source.read_text() + """    ProxyJump relay
# secret: relay
Host relay
    HostName 192.0.2.51
    User relay-user
"""
                    source.write_text(text)
                    sidecar = context.checkout / ".hostbook/identity.json"
                    identity_data = json.loads(sidecar.read_text())
                    identity_data["sources"][0]["sha256"] = hashlib.sha256(text.encode()).hexdigest()
                    identity_data["hosts"].append({"name": "relay", "host_id": new_id(), "bindings": [
                        {"path": "team.conf", "host_ordinal": 2, "token_ordinal": 1},
                    ]})
                    sidecar.write_text(json.dumps(identity_data))
                    other = self.root / "key-0"
                    (context.vault / "relay.meta").write_bytes(b"type=key\n")
                    (context.vault / "relay.pub").write_bytes(other.with_suffix(".pub").read_bytes())
                    (context.vault / "relay.age").write_bytes(ciphertext)
                    swapped = tool("age", "-e", "-r", recipient.decode().strip(), data=other.read_bytes())
                    (context.vault / "login.age").write_bytes(swapped)
                    stamp = refresh_routes(load, context.store_id, self.generations).strip().split("\n")[0].split("\t")[3]
                    with patch("store_read.launch_ssh") as transport:
                        with self.assertRaisesRegex(StoreContextError, "store public and private key do not match"):
                            connect_route(load, context.store_id, self.generations, host_id, stamp,
                                          read_passphrase=lambda: passphrase)
                        transport.assert_not_called()

                    (context.vault / "login.age").write_bytes(ciphertext)
                    (context.vault / "relay.age").write_bytes(swapped)
                    extended = text + """    ProxyJump repeated
# secret: zz-repeat
Host repeated
    HostName 192.0.2.52
    User repeated-user
"""
                    source.write_text(extended)
                    identity_data["sources"][0]["sha256"] = hashlib.sha256(extended.encode()).hexdigest()
                    identity_data["hosts"].append({"name": "repeated", "host_id": new_id(), "bindings": [
                        {"path": "team.conf", "host_ordinal": 3, "token_ordinal": 1},
                    ]})
                    sidecar.write_text(json.dumps(identity_data))
                    (context.vault / "zz-repeat.meta").write_bytes(b"type=key\n")
                    (context.vault / "zz-repeat.pub").write_bytes(public_key)
                    (context.vault / "zz-repeat.age").write_bytes(swapped)
                    stamp = refresh_routes(load, context.store_id, self.generations).strip().split("\n")[0].split("\t")[3]
                    with patch("store_read.launch_ssh") as transport:
                        with self.assertRaisesRegex(StoreContextError, "store public and private key do not match"):
                            connect_route(load, context.store_id, self.generations, host_id, stamp,
                                          read_passphrase=lambda: passphrase)
                        transport.assert_not_called()

                    source.write_text(text)
                    identity_data["sources"][0]["sha256"] = hashlib.sha256(text.encode()).hexdigest()
                    identity_data["hosts"].pop()
                    sidecar.write_text(json.dumps(identity_data))
                    stamp = refresh_routes(load, context.store_id, self.generations).strip().split("\n")[0].split("\t")[3]
                    (context.vault / "relay.meta").write_bytes(b"type=password\n")
                    (context.vault / "relay.age").unlink()

                    def identity_files(config, alias):
                        output = subprocess.run(["/usr/bin/ssh", "-F", config, "-G", alias], env=fixture_env,
                                                capture_output=True, check=True, timeout=5).stdout
                        return [line.split(b" ", 1)[1] for line in output.splitlines()
                                if line.startswith(b"identityfile ")]

                    def mixed_launch(config, alias):
                        self.assertEqual(len(identity_files(config, alias)), 2)
                        self.assertEqual(identity_files(config, "relay"), [b"none"])
                        return 0

                    with patch("store_read.launch_ssh", side_effect=mixed_launch) as transport:
                        self.assertEqual(connect_route(load, context.store_id, self.generations, host_id, stamp,
                                                       read_passphrase=lambda: passphrase), 0)
                        transport.assert_called_once()

                    (context.vault / "login.meta").write_bytes(b"type=password\n")
                    with patch("store_read.launch_ssh", return_value=0) as transport:
                        self.assertEqual(connect_route(
                            load, context.store_id, self.generations, host_id, stamp,
                            read_passphrase=lambda: self.fail("interactive login must not unlock the vault"),
                        ), 0)
                        transport.assert_called_once()

    def test_registered_key_marker_requires_a_concrete_active_host_block(self):
        from store_read import refresh_routes

        context = self.write_route(1, new_id(), "# secret: login\nHost db other#tag !db\n")
        with self.assertRaisesRegex(StoreContextError, "credential binding requires an unambiguous managed Host"):
            refresh_routes(lambda: self.account, context.store_id, self.generations)

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

    def test_installed_callback_requires_refresh_if_the_derived_cache_is_corrupt(self):
        self.launcher = self.installed_launcher()
        host_id = new_id()
        context = self.write_route(1, host_id, "Host db\n")
        rows = self.route_callback(context, "_store_hosts")
        self.assertEqual(rows.returncode, 0, rows.stderr)
        stamp = rows.stdout.strip().split("\t")[3]
        cache = context.index.parent / "routes.json"
        data = json.loads(cache.read_text())
        del data["hosts"][host_id]["hostname"]
        for broken in ([], data):
            cache.write_text(json.dumps(broken))
            result = self.route_callback(context, "_store_preview", host_id, stamp)
            self.assertEqual(result.returncode, 65)
            self.assertIn("refresh the store hosts", result.stderr)
            self.assertEqual(result.stdout, "")

    def test_route_refresh_reports_when_system_openssh_is_unavailable(self):
        from store_read import refresh_routes

        context = self.write_route(1, new_id(), "Host db\n")
        with patch("store_read.SSH", str(self.root / "not-installed-ssh")):
            with self.assertRaisesRegex(StoreContextError, "requires system OpenSSH"):
                refresh_routes(lambda: self.account, context.store_id, self.generations)

    def test_route_snapshot_limits_total_source_bytes_before_publishing(self):
        from store_read import refresh_routes

        context = self.write_route(1, new_id(), "Host db\n")
        sidecar = context.checkout / ".hostbook/identity.json"
        data = json.loads(sidecar.read_text())
        contents = b"Host relay\n"
        (context.checkout / "relay.conf").write_bytes(contents)
        data["sources"].append({"path": "relay.conf", "sha256": hashlib.sha256(contents).hexdigest()})
        data["hosts"].append({"host_id": new_id(), "name": "relay", "bindings": [
            {"path": "relay.conf", "host_ordinal": 1, "token_ordinal": 1},
        ]})
        sidecar.write_text(json.dumps(data))
        load = lambda: self.account
        refresh_routes(load, context.store_id, self.generations)
        original = (context.index.parent / "routes.json").read_bytes()

        with patch("store_read.MAX_ROUTE_BYTES", 16, create=True):
            with self.assertRaisesRegex(StoreContextError, "SSH sources exceed size limit"):
                refresh_routes(load, context.store_id, self.generations)
        self.assertEqual((context.index.parent / "routes.json").read_bytes(), original)

    def test_scoped_proxyjump_preserves_realm_and_interface_qualified_hops(self):
        from store_read import credential_targets, effective, ssh_view

        binary = self.root / "jump-client"
        binary.mkdir()
        stub = binary / "ssh"
        captured = binary / "arguments"
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$HOSTBOOK_JUMP_ARGUMENTS"\nexit 255\n')
        stub.chmod(0o700)
        env = {"PATH": f"{binary}:/usr/bin:/bin", "HOME": str(self.root / "fixture-home"),
               "SHELL": "/bin/sh", "HOSTBOOK_JUMP_ARGUMENTS": str(captured)}
        for spec, logical, alias, user, forward in (
            ("operator@example.org@relay", "operator@example.org@relay", "relay", "operator@example.org", None),
            ("ssh://operator%40example.org@relay", "operator@example.org@relay", "relay",
             "operator@example.org", None),
            ("[fe80::1%en0]", "[fe80::1%en0]", "fe80::1%en0", None, None),
            ("[fe80::1%en0],relay", "[fe80::1%en0],relay", "relay", None, "[fe80::1%%en0]"),
        ):
            with self.subTest(spec=spec), socket.socket() as guard:
                guard.bind(("127.0.0.1", 0))
                source = f"""Host db
 HostName 127.0.0.1
 Port {guard.getsockname()[1]}
 User target
 ProxyJump {spec}
Host relay
 User operator@example.org
""".encode()
                with ssh_view([source], (None, None, None)) as config:
                    route = effective(config, "db")
                    self.assertEqual(route["proxyjump"], logical)
                    with patch("store_read.effective", wraps=effective) as resolve:
                        expected = {"db": "target-key"}
                        if alias == "relay":
                            expected["relay"] = "hop-key"
                        self.assertEqual(credential_targets(
                            config, "db", route, {"db": "target-key", "relay": "hop-key"}
                        ), expected)
                        if "fe80::1" in spec:
                            self.assertIn("fe80::1%en0", [call.args[1] for call in resolve.call_args_list])
                    captured.unlink(missing_ok=True)
                    result = subprocess.run(["ssh", "-F", config, "db"], executable="/usr/bin/ssh",
                                            stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=5)
                    self.assertEqual(result.returncode, 255)
                    args = captured.read_text().splitlines()
                    self.assertEqual(args[-1], alias)
                    if user:
                        self.assertEqual(args[args.index("-l") + 1], user)
                    if forward:
                        self.assertEqual(args[args.index("-J") + 1], forward)

    def test_scoped_proxyjump_limits_generated_config_growth(self):
        from store_read import ssh_view

        chain = ",".join(["[fe80::1%en0]"] + ["relay"] * 5)
        source = f"Host db\n ProxyJump {chain}\nHost web\n ProxyJump {chain}\n".encode()
        with patch("store_read.JSON_LIMIT", 200):
            with self.assertRaisesRegex(StoreContextError, "SSH jump configuration exceeds size limit"):
                with ssh_view([source], (None, None, None)):
                    pass

    def test_scoped_proxyjump_rejects_unapproved_percent_escapes(self):
        from store_read import ssh_view

        for spec in ("operator%40example.org@relay", "ssh://operator%24example.org@relay",
                     "[fe80::1%%en0]", "[fe80::1%en0%tail]", "[fe80::1%en/0]",
                     "ssh://[fe80::1%25en0]"):
            with self.subTest(spec=spec), self.assertRaisesRegex(StoreContextError, "invalid SSH directive"):
                with ssh_view([f"Host db\n ProxyJump {spec}\n".encode()], (None, None, None)):
                    pass

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
