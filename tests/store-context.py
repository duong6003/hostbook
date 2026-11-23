#!/usr/bin/env python3
"""Resolve verified, disposable store registrations without opening live state."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
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

        approval = self.write_local_approval(home)
        approval_bytes = approval.read_bytes()
        positive = lookup()
        self.assertEqual(positive.returncode, 0, positive.stderr)
        self.assertEqual(json.loads(positive.stdout)["checkout"], str(self.root / "shared a"))
        self.assertEqual(approval.read_bytes(), approval_bytes)
        self.assertEqual(snapshot(self.root), before)

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
