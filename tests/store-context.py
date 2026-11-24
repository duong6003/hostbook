#!/usr/bin/env python3
"""Resolve verified, disposable store registrations without opening live state."""
import fcntl
from contextlib import ExitStack
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
            for name in ("store_read.py", "store_login.py", "store_vault.py", "store_receive.py", "store_checkout.py",
                         "store_journal.py", "store_publish.py", "store_mode.py", "store_private.py",
                         "store_transfer.py", "store_transfer_credentials.py", "store_transfer_command.py",
                         "store_transfer_intent.py", "store_transfer_recovery.py", "store_transfer_owner.py",
                         "store_transfer_apply.py", "store_transfer_publish.py", "store_transfer_remove.py"):
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

    def test_approved_checkout_without_any_marker_refuses_legacy_raw_writers(self):
        source = Path(__file__).resolve().parents[1]
        installed = self.root / "guarded app"
        installed.mkdir()
        app = installed / "hostbook"
        disposable_account = os.environ.get("HOSTBOOK_TEST_DISPOSABLE_PASSWD_HOME") == "1"
        if disposable_account:
            import pwd
            approved_home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
            self.assertIn(approved_home,(Path("/fixture-home"),Path("/home/hostbook-fixture")))
            self.assertFalse((approved_home/".config").exists())
            self.assertTrue(Path("/.dockerenv").exists())
        else:
            approved_home = self.root/"approved-home"
            approved_home.mkdir(mode=0o700)
        approval = self.write_local_approval(approved_home)
        if disposable_account:
            self.addCleanup(approval.parent.parent.rmdir)
            self.addCleanup(approval.parent.rmdir)
            self.addCleanup(approval.unlink)
        body = (source/"hostbook").read_text()
        if not disposable_account:
            body = body.replace("local authority_home=~",'local authority_home="' + str(approved_home) + '"')
        app.write_text(body)
        app.chmod(0o700)
        shutil.copytree(source/"lib",installed/"lib")
        if not disposable_account:
            for name in ("store_context.py", "store_private.py"):
                context = installed/"lib"/name
                context.write_text(context.read_text().replace("Path(pwd.getpwuid(os.geteuid()).pw_dir)", "Path("+repr(str(approved_home))+")"))
        shutil.copytree(source/"lang",installed/"lang")
        library = installed/"copied-helpers"
        library.write_text(body.split("# ---------- entry point ----------")[0])
        shared = self.root/"shared a"
        shutil.rmtree(shared/".hostbook")
        outside = self.root/"legacy"
        outside.mkdir()
        block = self.root/"host-input"
        block.write_text("Host synthetic\n  HostName 192.0.2.9\n")
        alias = self.root/"retarget"
        alias.symlink_to(shared,target_is_directory=True)
        env = dict(os.environ, HOME=str(outside), HOSTBOOK_DIR=str(alias), HOSTBOOK_STATE=str(self.root/"legacy-state"),
                   HOSTBOOK_LIB_DIR=str(installed/"lib"), HOSTBOOK_LANG_DIR=str(installed/"lang"),
                   PATH=f"{Path(sys.executable).parent}:/usr/bin:/bin")
        prior = snapshot(shared)
        for args in (["_write","team",block],["_bg_sync"],["_vault_init"],["_rename","old","new"],["_import_write",block]):
            result = subprocess.run([app,*args],cwd=outside,env=env,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,65,(args,result.stderr))
            self.assertEqual(snapshot(shared),prior)
        env["HOSTBOOK_DIR"] = str(outside)
        commands = '. "$1"; DIR=$2; write_host touched "$3"'
        result = subprocess.run(["/bin/bash","-c",commands,"fixture",library,alias,block],cwd=outside,env=env,
                                capture_output=True,timeout=10)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(snapshot(shared),prior)
        retain = '. "$1"; HOSTBOOK_DIR=$2; . "$1" && exit 1; [ "$DIR" = "$HOME" ] && write_host old "$3"'
        kept = subprocess.run(["/bin/bash","-c",retain,"fixture",library,alias,block],cwd=outside,env=env,
                              capture_output=True,timeout=10)
        self.assertEqual(kept.returncode,0,kept.stderr)
        self.assertTrue((outside/"old.conf").exists())
        (outside/"alias.conf").symlink_to(shared/"physical.conf")
        denied = subprocess.run([app,"_write","alias",block],cwd=outside,env=env,capture_output=True)
        self.assertNotEqual(denied.returncode,0)
        self.assertEqual(snapshot(shared),prior)
        allowed = subprocess.run([app,"_write","unregistered",block],cwd=outside,env=env,capture_output=True)
        self.assertEqual(allowed.returncode,0,allowed.stderr)
        self.assertEqual((outside/"unregistered.conf").read_bytes(),block.read_bytes())
        private = self.root/"private"
        git_env = dict(env,GIT_CONFIG_NOSYSTEM="1",GIT_CONFIG_GLOBAL=os.devnull,GIT_AUTHOR_NAME="Synthetic",
                       GIT_COMMITTER_NAME="Synthetic",GIT_AUTHOR_EMAIL="test@example.test",GIT_COMMITTER_EMAIL="test@example.test")
        for args in (["init","-q","--template="],["symbolic-ref","HEAD","refs/heads/main"],["add","."],["commit","-qm","Private"]):
            subprocess.run(["/usr/bin/git","-C",private,*args],env=git_env,capture_output=True,check=True)
        env["HOSTBOOK_DIR"] = str(alias)
        for args in (["add","192.0.2.11","public-add"],["import",str(block)]):
            result = subprocess.run([app,*args],cwd=outside,env=env,capture_output=True,timeout=15)
            self.assertEqual(result.returncode,0,(args,result.stderr))
            self.assertEqual(json.loads(result.stdout)["store_id"],self.entries[0]["store_id"])
        self.assertEqual(snapshot(shared),prior)
        # Ordinary callbacks must not trust ambient interpreter resolution or mutate claimed XDG state.
        fake = self.root/"fake-tools"
        fake.mkdir()
        (fake/"python3").write_text('#!/bin/sh\ntouch "$HOME/fake-python-ran"\nexit 0\n')
        (fake/"python3").chmod(0o700)
        hostile = dict(env,PATH=str(fake)+":"+env["PATH"])
        # Production installs invoke the launcher through a symlink. Resolve it with the system tool.
        (fake/"readlink").write_text('#!/bin/sh\ntouch "$HOME/ambient-readlink-ran"\nexit 1\n')
        (fake/"readlink").chmod(0o700)
        launcher_link = outside/"hostbook"
        launcher_link.symlink_to(app)
        untrusted = subprocess.run([launcher_link,"_write","team",block],env=hostile,capture_output=True,timeout=5)
        self.assertEqual(untrusted.returncode,65)
        self.assertFalse((outside/"ambient-readlink-ran").exists())
        refused = subprocess.run([app,"_write","team",block],env=hostile,capture_output=True)
        self.assertEqual(refused.returncode,65)
        self.assertFalse((outside/"fake-python-ran").exists())
        denied = subprocess.run([app,"store-add","invalid","not-an-address"],env=hostile,capture_output=True)
        self.assertNotEqual(denied.returncode,0)
        self.assertFalse((outside/"fake-python-ran").exists())
        config_parent = approved_home/".config/hostbook"
        config_parent.chmod(0o000)
        try:
            if os.geteuid() != 0:
                denied = subprocess.run([app,"_write","team",block],env=env,capture_output=True)
                self.assertEqual(denied.returncode,65)
        finally:
            config_parent.chmod(0o700)
        routed = dict(hostile,HOSTBOOK_DIR=str(outside),XDG_STATE_HOME=str(shared),XDG_CONFIG_HOME=str(shared))
        routed.pop("HOSTBOOK_STATE",None)
        for args in (["_write","new",block],["_list"],["_header"],["_preview","h:db"],
                     ["_footer","h:db"],["_footers",str(shared),"h:db"],["_demo"]):
            subprocess.run([app,*args],env=routed,capture_output=True,timeout=15)
            self.assertEqual(snapshot(shared),prior,args)
        self.assertFalse((outside/"fake-python-ran").exists())
        checkout = self.root/"unrelated-index-checkout"
        checkout.mkdir()
        state = self.root/"unrelated-index-state"
        source_file = checkout/"team.conf"
        source_file.write_text("Host old-local\n  HostName 192.0.2.3\n")
        ssh_input = self.root/"legacy-ssh-config"
        ssh_input.write_text(f'Include "{source_file}"\n')
        disjoint = dict(env,HOSTBOOK_DIR=str(checkout),HOSTBOOK_STATE=str(state),
                        HOSTBOOK_SSH_CONFIG=str(ssh_input),XDG_CONFIG_HOME=str(self.root/"legacy-xdg"))
        initial = subprocess.run([app,"_index"],env=disjoint,capture_output=True)
        self.assertEqual(initial.returncode,0,initial.stderr)
        source_file.write_text("Host new-local\n  HostName 192.0.2.4\n")
        updated = subprocess.run([app,"_list"],env=disjoint,capture_output=True)
        self.assertEqual(updated.returncode,0,updated.stderr)
        self.assertIn(b"h:new-local",updated.stdout)
        self.assertNotIn(b"h:old-local",updated.stdout)
        ssh_input.write_text(f'Host new-local\n  User updated-account\nInclude "{source_file}"\n')
        external = subprocess.run([app,"_list"],env=disjoint,capture_output=True)
        self.assertEqual(external.returncode,0,external.stderr)
        self.assertIn(b"updated-account",(state/"index.tsv").read_bytes())
        context = installed/"lib/store_context.py"
        hidden = installed/"lib/context.paused"
        context.rename(hidden)
        try:
            warm = subprocess.run([app,"_list"],env=disjoint,capture_output=True)
            self.assertEqual(warm.returncode,0,warm.stderr)
            self.assertIn(b"h:new-local",warm.stdout)
        finally:
            hidden.rename(context)
        self.assertEqual(snapshot(shared),prior)
        for name in ("base","ours","theirs"):
            (outside/name).write_text("Host legacy\n")
        (outside/"ours.hbmerge").symlink_to(shared/"unexpected.conf")
        merged = subprocess.run([app,"_merge",outside/"base",outside/"ours",outside/"theirs","team.conf"],
                                env=dict(env,HOSTBOOK_DIR=str(outside)),capture_output=True)
        self.assertEqual(merged.returncode,0,merged.stderr)
        self.assertEqual(snapshot(shared),prior)

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

    def prepare_receive(self, index=1, installed=None):
        entry = self.entries[index]
        store = Path(entry["checkout"]["root"])
        remote, author = self.root / f"team-{index}.git", self.root / f"author-{index}"
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
        app = installed or self.installed_launcher()
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

    def test_receive_requires_the_direct_registered_branch(self):
        f = self.prepare_receive()
        ref = "refs/heads/main"
        other = "refs/heads/other"
        f.git(f.remote,"update-ref",other,f.tip)
        f.git(f.remote,"symbolic-ref",ref,other)
        before = snapshot(f.store)
        result = f.receive()
        self.assertEqual(result.returncode,65,result.stderr)
        self.assertEqual(snapshot(f.store),before)
        self.assertFalse((f.target.parent.parent.parent/"receive.pending").exists())

    def test_locked_receive_adopts_encrypted_password_records_with_existing_vault(self):
        names = ("age","age-keygen","age-plugin-batchpass","age-inspect")
        if not all(shutil.which(name) for name in names):
            self.skipTest("requires age tools including age-inspect")
        tools = Path(shutil.which("age")).parent
        env = {"PATH":str(tools),"HOME":str(self.root),"XDG_CONFIG_HOME":str(self.root/"config")}
        def age(name, args=(), data=b"", passphrase=None):
            with ExitStack() as opened:
                extra, inherited = {}, ()
                if passphrase:
                    reader, writer = os.pipe()
                    opened.callback(os.close,reader)
                    with os.fdopen(writer,"wb") as stream:
                        stream.write(passphrase+b"\n")
                    extra, inherited = {"AGE_PASSPHRASE_FD":str(reader)}, (reader,)
                return subprocess.run([str(tools/name),*args],input=data,env=dict(env,**extra),pass_fds=inherited,
                                      capture_output=True,check=True,timeout=30).stdout
        identity = age("age-keygen")
        recipient = age("age-keygen",["-y"],identity)
        vault = self.root/"shared a/secrets"
        (vault/"vault.pub").write_bytes(recipient)
        for target, password in (("vault.key.age",b"fixture unlock"),("vault.recovery.age",b"fixture recovery")):
            (vault/target).write_bytes(age("age",["-a","-e","-j","batchpass"],identity,password))
        def encrypt(data):
            return age("age",["-a","-r",recipient.strip().decode()],data)
        old = encrypt(b"synthetic initial password")
        (vault/"login.meta").write_bytes(b"type=password\n")
        (vault/"login.age").write_bytes(old)
        f = self.prepare_receive()
        remote_vault = f.author/"secrets"
        original = snapshot(f.store)
        def publish():
            f.git(f.author,"add","secrets")
            f.git(f.author,"commit","-qm","Update fixture encrypted records")
            f.git(f.author,"push","-q",str(f.remote),"HEAD:refs/heads/main")
        changed = encrypt(b"synthetic received password")
        for bad in (b"unsealed fixture",changed[:40]):
            (remote_vault/"login.age").write_bytes(bad)
            publish()
            self.assertEqual(f.receive().returncode,65)
            self.assertEqual(snapshot(f.store),original)
        (remote_vault/"login.age").write_bytes(old)
        (remote_vault/"extra.age").write_bytes(old)
        publish()
        self.assertEqual(f.receive().returncode,65)
        self.assertEqual(snapshot(f.store),original)
        (remote_vault/"extra.meta").write_bytes(b"type=password\ncreated=2026-01-02\n")
        publish()
        remote_before, other_before = snapshot(f.remote),snapshot(self.root/"shared b")
        result = f.receive()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((vault/"login.age").read_bytes(),old)
        self.assertEqual((vault/"extra.age").read_bytes(),(remote_vault/"extra.age").read_bytes())
        self.assertEqual((vault/"vault.pub").read_bytes(),recipient)
        self.assertEqual(snapshot(f.remote),remote_before)
        self.assertEqual(snapshot(self.root/"shared b"),other_before)
        working = snapshot(f.store)
        other_identity = age("age-keygen")
        other_recipient = age("age-keygen",["-y"],other_identity)
        wrong = age("age",["-a","-r",other_recipient.strip().decode()],b"synthetic wrong recipient")
        (remote_vault/"login.age").write_bytes(wrong)
        publish()
        self.assertEqual(f.receive().returncode,65)
        self.assertEqual(snapshot(f.store),working)
        import store_receive
        def receive_verified():
            return store_receive.receive(lambda:self.account,self.entries[1]["store_id"],self.generations,
                                         read_passphrase=lambda:b"fixture unlock")
        with self.assertRaises(StoreContextError):
            receive_verified()
        self.assertEqual(snapshot(f.store),working)
        (remote_vault/"login.age").write_bytes(changed)
        publish()
        self.assertEqual(f.receive().returncode,65)
        self.assertEqual(snapshot(f.store),working)
        accepted = receive_verified()
        self.assertEqual(accepted["received"],f.git(f.remote,"rev-parse","refs/heads/main"))
        self.assertEqual((vault/"login.age").read_bytes(),changed)
        self.assertIn(old,[p.read_bytes() for p in (self.recovery/"receives").glob("*/*") if p.name.isdigit()])
        prior = snapshot(f.store)
        (remote_vault/"vault.key.age").write_bytes(b"unverified replacement")
        publish()
        self.assertEqual(f.receive().returncode,65)
        self.assertEqual(snapshot(f.store),prior)

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

    def test_receive_keeps_latest_of_two_editor_saves_and_reports_recovery(self):
        import store_checkout
        import store_receive
        f = self.prepare_receive()
        source = f.store / "team.conf"
        earlier, latest = b"# first editor save\n", b"# second editor save\n"
        install, check = store_checkout.install_sources, store_checkout.check_contents
        count = 0
        def editor(parent, name, *args):
            nonlocal count
            def save(data):
                temp = f.store/"editor-save"
                temp.write_bytes(data)
                temp.replace(source)
            if name.isdigit() and count == 1:
                save(latest)
                count = 2
            result = check(parent,name,*args)
            if name == "team.conf" and count == 0:
                save(earlier)
                count = 1
            return result
        def interleaved(*args):
            with patch.object(store_checkout,"check_contents",side_effect=editor):
                return install(*args)
        with patch.object(store_checkout,"install_sources",side_effect=interleaved):
            with self.assertRaisesRegex(StoreContextError,"multiple editor saves"):
                store_receive.receive(lambda:self.account,self.entries[1]["store_id"],self.generations)
        self.assertEqual(source.read_bytes(),latest)
        saved = self.recovery/"receives"
        self.assertIn(earlier,[p.read_bytes() for p in saved.glob("*/*") if p.name.isdigit()])
        self.assertEqual(f.git(f.store,"rev-parse","HEAD"),f.base)

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

    def test_two_registered_remotes_keep_independent_publication_and_receive_modes(self):
        import store_mode
        import store_publish
        import store_read
        a = self.prepare_receive()
        b = self.prepare_receive(2,a.app)
        self.assertEqual(a.receive().returncode,0)
        load = lambda:self.account
        aid,bid = [row["store_id"] for row in self.entries[1:]]
        enabled = store_mode.change_mode(load,aid,self.generations,"two-way")
        ag = Generations(**enabled["generations"])
        row = store_read.refresh_routes(load,aid,ag).strip().split("\t")
        before_b = snapshot(b.remote)
        native_send = store_publish.send
        def receive_b(*args):
            self.assertEqual(b.receive().returncode,0)
            brow = store_read.refresh_routes(load,bid,self.generations).strip().split("\t")
            with self.assertRaisesRegex(StoreContextError,"two-way"):
                store_publish.publish_address(load,bid,self.generations,brow[1],brow[3],"192.0.2.99")
            return native_send(*args)
        with patch.object(store_publish,"send",side_effect=receive_b):
            accepted = store_publish.publish_address(load,aid,ag,row[1],row[3],"192.0.2.88")
        self.assertEqual(accepted["outcome"],"accepted")
        self.assertEqual(b.git(b.store,"rev-parse","HEAD"),b.tip)
        self.assertEqual(snapshot(b.remote),before_b)

    def test_registered_add_and_plain_import_default_to_private_from_shared_selection(self):
        f = self.prepare_receive()
        private = self.root/"private"
        f.git(private,"init","-q","--template=")
        f.git(private,"symbolic-ref","HEAD","refs/heads/main")
        f.git(private,"add",".")
        f.git(private,"commit","-qm","Received private content")
        before = [snapshot(path) for path in (f.store,self.root/"shared b",f.remote)]
        request = dict(account=asdict(self.account), store_id=self.entries[1]["store_id"],
                       generations=asdict(self.generations))
        def run(action,*args,document=None):
            packet = request if document is None else dict(request,document=document)
            return subprocess.run([f.app,action,*args],cwd=f.store,env=f.env,input=json.dumps(packet,default=os.fspath),
                                  text=True,capture_output=True,timeout=30)
        added = run("_store_add","new-private","192.0.2.66")
        self.assertEqual(added.returncode,0,added.stderr)
        self.assertEqual(json.loads(added.stdout)["store_id"],self.entries[0]["store_id"])
        imported = run("_store_import",document="Host jump-private\n  HostName 192.0.2.77\n  Port 2200\n")
        self.assertEqual(imported.returncode,0,imported.stderr)
        manifest = json.loads((private/".hostbook/identity.json").read_bytes())
        self.assertEqual([h["name"] for h in manifest["hosts"]],["new-private","jump-private"])
        self.assertEqual(f.git(private,"status","--porcelain"),"")
        self.assertEqual(f.git(private,"rev-list","--count","HEAD"),"3")
        saved = snapshot(private)
        self.assertNotEqual(run("_store_import",document="Host jump-private\n  HostName 192.0.2.1\n").returncode,0)
        self.assertEqual(snapshot(private),saved)
        self.assertEqual([snapshot(path) for path in (f.store,self.root/"shared b",f.remote)],before)

    def test_private_import_uses_native_physical_lines_for_literal_host_fields(self):
        from store_private import admitted_hosts
        self.assertEqual(admitted_hosts(b"Host db\r\n  HostName 192.0.2.1\r\n")[0]["hostname"],"192.0.2.1")
        for hidden in ("\r","\v","\f","\x85","\u2028","\u2029"):
            with self.subTest(hidden=repr(hidden)), self.assertRaises(StoreContextError):
                admitted_hosts(("Host db\n# note"+hidden+"  HostName 192.0.2.66\n").encode())

    def test_mode_switch_changes_only_selected_registration_and_fences_stale_callbacks(self):
        f = self.prepare_receive()
        self.assertEqual(f.receive().returncode, 0)
        store = self.entries[1]["store_id"]
        before, remote = snapshot(f.store), snapshot(f.remote)
        def mode(direction, expected):
            request = dict(account=asdict(self.account),store_id=store,generations=asdict(expected))
            return subprocess.run([f.app, "_store_mode", direction], cwd=self.root, env=f.env,
                                  input=json.dumps(request,default=os.fspath), text=True, capture_output=True)
        up = mode("two-way", self.generations)
        self.assertEqual(up.returncode, 0, up.stderr)
        first = json.loads(up.stdout)
        self.assertEqual(first["mode"], "two-way")
        self.assertEqual(first["generations"]["policy"], self.generations.policy + 3)
        self.assertNotEqual(mode("receive-only", self.generations).returncode, 0)
        down = mode("receive-only", Generations(**first["generations"]))
        self.assertEqual(down.returncode, 0, down.stderr)
        now = json.loads(self.registry.read_bytes())
        self.assertEqual(now["stores"][1]["effective_mode"], "receive-only")
        self.assertIsNone(now["stores"][1]["transition_id"])
        self.assertEqual(now["stores"][::2], self.entries[::2])
        self.assertEqual(snapshot(f.store), before)
        self.assertEqual(snapshot(f.remote), remote)

    def test_one_retained_edit_per_store_can_retry_without_switching_mode(self):
        import store_mode
        import store_publish
        f, edit, _ = self.prepare_edit()
        store, load = self.entries[1]["store_id"], lambda:self.account
        with patch.object(store_publish,"send",return_value="not-accepted") as sends:
            candidate = edit()["attempt_id"]
            with self.assertRaisesRegex(StoreContextError,candidate):
                edit()
            self.assertEqual(sends.call_count,1)
        self.assertEqual(len(list((self.recovery/"edits").iterdir())),1)
        review = store_mode.change_mode(load,store,self.generations,"two-way")
        self.assertEqual((review["pending"],review["choices"]),([candidate],["retry","discard","cancel"]))
        packet = dict(account=asdict(self.account),store_id=store,generations=asdict(self.generations))
        run = subprocess.run([f.app,"_store_mode","two-way","retry",candidate],cwd=self.root,env=f.env,
                             input=json.dumps(packet,default=os.fspath),text=True,capture_output=True)
        self.assertEqual(run.returncode,0,run.stderr)
        done = json.loads(run.stdout)
        self.assertEqual((done["outcome"],done["mode"],done["local"]),("accepted","two-way","received"))
        self.assertEqual(f.git(f.remote,"show","refs/heads/main:team.conf"),"Host db\n  HostName 192.0.2.88")
        with patch.object(store_publish,"send",side_effect=AssertionError("retired candidate replayed")):
            self.assertNotIn("pending",store_mode.change_mode(load,store,Generations(**done["generations"]),"two-way"))

    def test_edit_rechecks_the_slot_after_staging_and_preserves_other_store(self):
        import store_publish
        f, edit, _ = self.prepare_edit()
        reserve = store_publish.publish_reserved
        first, nested = [], False
        def before_reservation(*args):
            nonlocal nested
            if not nested:
                nested = True
                first.append(edit()["attempt_id"])
            return reserve(*args)
        with patch.object(store_publish,"publish_reserved",side_effect=before_reservation):
            with patch.object(store_publish,"send",return_value="not-accepted") as sends:
                with self.assertRaisesRegex(StoreContextError,"retry or discard"):
                    edit()
                self.assertEqual(sends.call_count,1)
        self.assertEqual([p.name for p in (self.recovery/"edits").iterdir()],first)
        original = snapshot(f.store)
        other = self.prepare_receive(index=2,installed=f.app)
        self.assertEqual(other.receive().returncode,0)
        self.assertEqual(other.git(other.store,"rev-parse","HEAD"),other.tip)
        self.assertEqual(snapshot(f.store),original)

    def test_discard_retained_edit_allows_fresh_save_without_switching_mode(self):
        import store_mode
        import store_publish
        import store_read
        f, edit, _ = self.prepare_edit()
        store, load = self.entries[1]["store_id"], lambda:self.account
        with patch.object(store_publish,"send",return_value="not-accepted"):
            first = edit()["attempt_id"]
        before, remote = snapshot(f.store), snapshot(f.remote)
        offline = self.root/"offline-remote"
        f.remote.rename(offline)
        try:
            with patch.object(store_publish,"send",side_effect=AssertionError("discard sent an edit")):
                done = store_mode.change_mode(load,store,self.generations,"two-way","discard",[first])
        finally:
            offline.rename(f.remote)
        self.assertEqual((done["mode"],snapshot(f.store),snapshot(f.remote)),("two-way",before,remote))
        expected = Generations(**done["generations"])
        row = store_read.refresh_routes(load,store,expected).strip().split("\t")
        result = store_publish.publish_address(load,store,expected,row[1],row[3],"192.0.2.44")
        self.assertEqual(result["outcome"],"accepted")
        self.assertNotEqual(result["attempt_id"],first)
        self.assertEqual(f.git(f.remote,"show","refs/heads/main:team.conf"),"Host db\n  HostName 192.0.2.44")

    def test_mode_cancel_preserves_and_confirmed_discard_retires_exact_edit(self):
        import store_mode
        import store_publish
        f, edit, _ = self.prepare_edit()
        load, store = lambda:self.account, self.entries[1]["store_id"]
        with patch.object(store_publish, "send", return_value="not-accepted"):
            candidate = edit()["attempt_id"]
        note = f.store / "notes"
        note.write_text("independent staged note")
        f.git(f.store,"add","notes")
        note.write_text("independent unstaged note")
        before, remote = snapshot(f.store), snapshot(f.remote)
        review = store_mode.change_mode(load,store,self.generations,"receive-only")
        self.assertEqual(review["pending"],[candidate])
        self.assertEqual(review["choices"],["send-then-switch","discard-then-switch","cancel"])
        cancel = store_mode.change_mode(load,store,self.generations,"cancel")
        self.assertEqual(cancel["mode"],"two-way")
        with self.assertRaises(StoreContextError):
            store_mode.change_mode(load,store,self.generations,"receive-only","discard-confirmed",[new_id()])
        done = store_mode.change_mode(load,store,self.generations,"receive-only","discard-confirmed",[candidate])
        self.assertEqual(done["mode"],"receive-only")
        enabled = store_mode.change_mode(load,store,Generations(**done["generations"]),"two-way")
        again = store_mode.change_mode(load,store,Generations(**enabled["generations"]),"receive-only")
        self.assertNotIn("pending", again)
        self.assertEqual(snapshot(f.store),before)
        self.assertEqual(snapshot(f.remote),remote)

    def test_mode_refuses_replaced_state_at_the_registry_effect(self):
        import store_mode
        f = self.prepare_receive()
        store = self.entries[1]["store_id"]
        displaced = self.root / "displaced-state"
        original = store_mode.prepare_transition
        def replace(*args):
            result = original(*args)
            self.state.rename(displaced)
            shutil.copytree(displaced,self.state)
            return result
        with patch.object(store_mode,"prepare_transition",side_effect=replace):
            with self.assertRaises(StoreContextError):
                store_mode.change_mode(lambda:self.account, store, self.generations, "two-way")
        for root in (self.state,displaced):
            self.assertIsNone(json.loads((root/"stores/v1/registry.json").read_bytes())["stores"][1]["transition_id"])
        self.assertEqual(len(list((self.recovery/"control").iterdir())),1)

    def test_mode_refuses_oversized_intermediate_before_changing_registry(self):
        import store_mode
        f = self.prepare_receive()
        old = json.loads(self.registry.read_bytes())
        compact = json.dumps(old,separators=(",",":")).encode()
        self.registry.write_bytes(compact)
        with patch.object(store_mode,"JSON_LIMIT",len(compact)+4):
            with self.assertRaisesRegex(StoreContextError,"registry.*size"):
                store_mode.change_mode(lambda:self.account, self.entries[1]["store_id"], self.generations, "two-way")
        self.assertEqual(self.registry.read_bytes(),compact)
        self.assertFalse((self.recovery/"control").exists())

    def test_discard_never_resurrects_candidate_when_disposition_missing_or_changed(self):
        import store_mode
        import store_publish
        f, edit, _ = self.prepare_edit()
        store, load = self.entries[1]["store_id"], lambda:self.account
        with patch.object(store_publish,"send",return_value="not-accepted"):
            candidate = edit()["attempt_id"]
        done = store_mode.change_mode(load,store,self.generations,"receive-only","discard-confirmed",[candidate])
        evidence = next((self.recovery/"modes").glob("*/decision.json"))
        evidence.unlink()
        for contents in (None, {"action":"clean","candidates":[]}):
            if contents is not None:
                evidence.write_text(json.dumps(contents))
                evidence.chmod(0o600)
            with self.assertRaises((StoreContextError,FileNotFoundError)):
                store_mode.change_mode(load,store,Generations(**done["generations"]),"two-way")

    def test_mode_sends_only_the_confirmed_reserved_candidate_before_switch(self):
        import store_mode
        import store_publish
        f, edit, _ = self.prepare_edit()
        store = self.entries[1]["store_id"]
        load = lambda:self.account
        with patch.object(store_publish,"send",return_value="not-accepted"):
            candidate = edit()["attempt_id"]
        actual = store_publish.send
        def check_fence(*args):
            entry = json.loads(self.registry.read_bytes())["stores"][1]
            self.assertEqual(entry["effective_mode"],"two-way")
            self.assertIsNotNone(entry["transition_id"])
            recs = [json.loads(p.read_bytes()) for p in (self.recovery/"control").glob("*/records/1.json")]
            self.assertEqual(sum(r["operation"] == "publish" for r in recs),2)
            # Network activity happens after the store gate has been released.
            with store_context.store_writer(load,store,store_context.entry_generations(entry)):
                pass
            return actual(*args)
        with patch.object(store_publish,"send",side_effect=check_fence):
            result = store_mode.change_mode(load,store,self.generations,"receive-only","send-confirmed",[candidate])
        self.assertEqual(result["mode"],"receive-only")
        self.assertEqual(result["outcome"],"accepted")
        self.assertEqual(f.git(f.remote,"show","refs/heads/main:team.conf"),"Host db\n  HostName 192.0.2.88")
        with patch.object(store_publish,"send",side_effect=AssertionError("must never replay")):
            enabled = store_mode.change_mode(load,store,Generations(**result["generations"]),"two-way")
            switched = store_mode.change_mode(load,store,Generations(**enabled["generations"]),"receive-only")
            self.assertNotIn("pending",switched)

    def test_mode_checks_the_original_bundle_digest_before_offering_or_discarding(self):
        import store_mode
        import store_publish
        f, edit, _ = self.prepare_edit()
        store, load = self.entries[1]["store_id"], lambda:self.account
        with patch.object(store_publish,"send",return_value="not-accepted"):
            candidate = edit()["attempt_id"]
        bundle = self.recovery/"edits"/candidate/"proposal.bundle"
        header,_ = bundle.read_bytes().split(b"\n\n",1)
        empty_pack = b"PACK"+(2).to_bytes(4,"big")+bytes(4)
        bundle.write_bytes(header+b"\n\n"+empty_pack+hashlib.sha1(empty_pack).digest())
        for action, confirmed in (("clean",[]),("discard-confirmed",[candidate]),("send-confirmed",[candidate])):
            with self.assertRaisesRegex(StoreContextError,"bundle"):
                store_mode.change_mode(load,store,self.generations,"receive-only",action,confirmed)

    def test_mode_failed_send_preserves_one_candidate_and_old_mode(self):
        import store_mode
        import store_publish
        f, edit, _ = self.prepare_edit()
        store, load = self.entries[1]["store_id"], lambda:self.account
        before, remote = snapshot(f.store), snapshot(f.remote)
        with patch.object(store_publish,"send",return_value="not-accepted"):
            candidate = edit()["attempt_id"]
            failed = store_mode.change_mode(load,store,self.generations,"receive-only","send-confirmed",[candidate])
        self.assertEqual((failed["mode"],failed["outcome"]),("two-way","not-accepted"))
        next_review = store_mode.change_mode(load,store,Generations(**failed["generations"]),"receive-only")
        self.assertEqual(next_review["pending"],[candidate])
        self.assertEqual(snapshot(f.store),before)
        self.assertEqual(snapshot(f.remote),remote)

    def test_mode_lost_ack_keeps_fence_until_original_attempt_settles(self):
        import store_mode
        import store_publish
        f, edit, _ = self.prepare_edit()
        store, load = self.entries[1]["store_id"], lambda:self.account
        with patch.object(store_publish,"send",return_value="not-accepted"):
            candidate = edit()["attempt_id"]
        native = store_publish.send
        def lose_ack(*args):
            self.assertEqual(native(*args),"accepted")
            return "uncertain"
        with patch.object(store_publish,"send",side_effect=lose_ack):
            uncertain = store_mode.change_mode(load,store,self.generations,"receive-only","send-confirmed",[candidate])
        self.assertEqual((uncertain["mode"],uncertain["outcome"]),("two-way","uncertain"))
        expected = Generations(**uncertain["generations"])
        with patch.object(store_publish,"send",side_effect=AssertionError("must never replay")):
            self.assertEqual(store_publish.settle(load,store,expected,uncertain["settle_attempt"])["outcome"],"accepted")
            final = store_mode.change_mode(load,store,expected,"receive-only")
        self.assertEqual(final["mode"],"receive-only")
        self.assertNotIn("transition_id",final)

    def test_mode_fences_an_ordinary_admitted_sender_before_it_finishes(self):
        import store_mode
        import store_publish
        f, edit, _ = self.prepare_edit()
        store, load = self.entries[1]["store_id"], lambda:self.account
        native = store_publish.send
        direction = []
        def while_admitted(*args):
            paused = store_mode.change_mode(load,store,self.generations,"receive-only")
            self.assertEqual(paused["mode"],"two-way")
            self.assertIsNotNone(paused["transition_id"])
            direction.append(paused)
            with self.assertRaises(StoreContextError):
                edit()
            return native(*args)
        with patch.object(store_publish,"send",side_effect=while_admitted):
            result = edit()
        self.assertEqual(result["outcome"],"accepted")
        self.assertEqual(direction[0]["settle_attempt"],result["attempt_id"])
        final = store_mode.change_mode(load,store,Generations(**direction[0]["generations"]),"receive-only")
        self.assertEqual(final["mode"],"receive-only")

    def test_mode_discovers_retry_reserved_before_registry_or_transition_control(self):
        import store_mode
        import store_publish
        f, edit, _ = self.prepare_edit()
        store, load = self.entries[1]["store_id"], lambda:self.account
        with patch.object(store_publish,"send",return_value="not-accepted"):
            candidate = edit()["attempt_id"]
        reserve = store_publish.reserve_edit
        def before_fence(*args,**kwargs):
            reserve(*args,**kwargs)
            raise RuntimeError("lost send owner before fence")
        with patch.object(store_publish,"reserve_edit",side_effect=before_fence):
            with self.assertRaises(RuntimeError):
                store_mode.change_mode(load,store,self.generations,"receive-only","send-confirmed",[candidate])
        self.assertIsNone(json.loads(self.registry.read_bytes())["stores"][1]["transition_id"])
        paused = store_mode.change_mode(load,store,self.generations,"receive-only")
        self.assertEqual(paused["mode"],"two-way")
        expected = Generations(**paused["generations"])
        self.assertEqual(store_publish.settle(load,store,expected,paused["settle_attempt"])["outcome"],"not-accepted")
        self.assertEqual(store_mode.change_mode(load,store,expected,"receive-only")["mode"],"two-way")

    def test_mode_recovers_sender_death_after_fence_before_start_without_resend(self):
        import store_mode
        import store_publish
        f, edit, _ = self.prepare_edit()
        store, load = self.entries[1]["store_id"], lambda:self.account
        with patch.object(store_publish,"send",return_value="not-accepted"):
            candidate = edit()["attempt_id"]
        pid = os.fork()
        if pid == 0:
            try:
                with patch.object(store_publish,"launch_reserved",side_effect=lambda *a:os._exit(79)):
                    store_mode.change_mode(load,store,self.generations,"receive-only","send-confirmed",[candidate])
            finally:
                os._exit(78)
        self.assertEqual(os.waitstatus_to_exitcode(os.waitpid(pid,0)[1]),79)
        entry = json.loads(self.registry.read_bytes())["stores"][1]
        expected = store_context.entry_generations(entry)
        self.assertEqual(entry["effective_mode"],"two-way")
        paused = store_mode.change_mode(load,store,expected,"cancel")
        self.assertIsNotNone(paused["transition_id"])
        result = store_publish.settle(load,store,expected,paused["settle_attempt"])
        self.assertEqual(result["outcome"],"not-accepted")
        with patch.object(store_publish,"send",side_effect=AssertionError("must never replay")):
            finished = store_mode.change_mode(load,store,expected,"receive-only")
            self.assertEqual(finished["mode"],"two-way")
            review = store_mode.change_mode(load,store,Generations(**finished["generations"]),"receive-only")
        self.assertEqual(review["pending"],[candidate])
        self.assertEqual(f.git(f.remote,"rev-parse","refs/heads/main"),f.tip)

    def test_mode_can_cancel_unpointed_intent_when_new_fence_would_exceed_capacity(self):
        import store_mode
        f = self.prepare_receive()
        store, load = self.entries[1]["store_id"], lambda:self.account
        with patch.object(store_mode,"replace_entry",side_effect=RuntimeError("interrupted before pointer")):
            with self.assertRaises(RuntimeError):
                store_mode.change_mode(load,store,self.generations,"two-way")
        current = json.loads(self.registry.read_bytes())
        compact = len(json.dumps(current,separators=(",",":")).encode())
        with patch.object(store_mode,"JSON_LIMIT",compact+4):
            cancelled = store_mode.change_mode(load,store,self.generations,"cancel")
        self.assertEqual(cancelled["mode"],"receive-only")
        self.assertEqual(cancelled["generations"]["policy"],self.generations.policy+1)
        self.assertIsNone(json.loads(self.registry.read_bytes())["stores"][1]["transition_id"])

    def test_mode_recovers_unpointed_fence_and_releases_terminal_pointer(self):
        import store_mode
        f = self.prepare_receive()
        store = self.entries[1]["store_id"]
        load = lambda:self.account
        original = store_mode.replace_entry
        for stop, wanted in ((1,"two-way"), (3,"receive-only")):
            current = json.loads(self.registry.read_bytes())["stores"][1]
            expected = store_context.entry_generations(current)
            calls = []
            def interrupted(*args):
                calls.append(True)
                if len(calls) == stop:
                    raise RuntimeError("interrupted registry write")
                original(*args)
            with patch.object(store_mode, "replace_entry", side_effect=interrupted):
                with self.assertRaisesRegex(RuntimeError,"interrupted"):
                    store_mode.change_mode(load, store, expected, wanted)
            paused = json.loads(self.registry.read_bytes())["stores"][1]
            gens = store_context.entry_generations(paused)
            self.assertEqual(paused["transition_id"] is None, stop == 1)
            with self.assertRaises(StoreContextError):
                import store_receive
                store_receive.receive(load, store, gens)
            result = store_mode.change_mode(load, store, gens, wanted)
            self.assertEqual(result["mode"],wanted)
            self.assertIsNone(json.loads(self.registry.read_bytes())["stores"][1]["transition_id"])

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

    def test_edit_rejects_symbolic_remote_ref_even_at_native_push(self):
        import store_publish
        f, edit, _ = self.prepare_edit()
        ref, other = "refs/heads/main", "refs/heads/other"
        f.git(f.remote, "update-ref", other, f.tip)
        send_git = store_publish.git_result
        def retarget(*args, **kwargs):
            if "push" in args:
                f.git(f.remote, "symbolic-ref", ref, other)
            return send_git(*args, **kwargs)
        before = snapshot(f.store)
        with patch.object(store_publish, "git_result", side_effect=retarget):
            result = edit()
        self.assertNotEqual(result["outcome"], "accepted")
        self.assertEqual(f.git(f.remote, "rev-parse", other), f.tip)
        self.assertEqual(f.git(f.remote, "symbolic-ref", ref), other)
        self.assertEqual(snapshot(f.store), before)

    def test_edit_declines_existing_symbolic_ref_before_reservation(self):
        f, edit, _ = self.prepare_edit()
        ref, other = "refs/heads/main", "refs/heads/other"
        f.git(f.remote, "update-ref", other, f.tip)
        f.git(f.remote, "symbolic-ref", ref, other)
        before, remote = snapshot(f.store), snapshot(f.remote)
        with self.assertRaisesRegex(StoreContextError, "branch revision"):
            edit()
        self.assertEqual(snapshot(f.store), before)
        self.assertEqual(snapshot(f.remote), remote)
        self.assertFalse((self.recovery / "control").exists())

    def test_receiver_selects_only_verified_git_with_explicit_hook_commands(self):
        import store_publish
        with tempfile.TemporaryDirectory(prefix="hb-receiver-") as root:
            trusted = Path(root)
            for name in ("git",):
                (trusted / name).write_text("#!/bin/sh\nprintf '%s\\n' 'hook.<friendly-name>.command' 'hook.<friendly-name>.event'\n")
                (trusted/name).chmod(0o700)
            with patch.object(sys, "platform", "darwin"), patch.object(store_publish, "configured_hooks", side_effect=lambda exe:exe == str(trusted/"git")), \
                    patch("store_login.verified_programs", return_value=(trusted, ())) as verify:
                self.assertEqual(store_publish.configured_receiver(self.account), str(trusted/"git"))
                self.assertEqual(verify.call_args.args[2], ("git",))
                for _, cellar in verify.call_args.args[1]:
                    self.assertIn(str(cellar), ("/opt/homebrew/Cellar/git","/usr/local/Cellar/git"))

    def test_before_send_repository_lock_records_definitive_no_send(self):
        import store_publish
        f, edit, _ = self.prepare_edit()
        reserve = store_publish.reserve
        def concurrent_lock(*args):
            reserve(*args)
            (f.remote / "packed-refs.lock").touch()
        with patch.object(store_publish, "reserve", side_effect=concurrent_lock):
            result = edit()
        self.assertEqual(result["outcome"], "not-accepted")
        receipt = self.recovery / "edits" / result["attempt_id"]
        self.assertFalse((receipt / "send-started").exists())
        self.assertTrue((receipt / "proposal.bundle").is_file())
        self.assertEqual(f.git(f.remote, "rev-parse", "refs/heads/main"), f.tip)

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
        with self.assertRaisesRegex(StoreContextError, result["attempt_id"]):
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
        # An alias to an unrelated branch containing the proposal does not prove acceptance.
        original_ref = "refs/heads/main"
        tip = f.git(f.remote, "rev-parse", original_ref)
        f.git(f.remote, "update-ref", "refs/heads/unrelated", tip)
        f.git(f.remote, "symbolic-ref", original_ref, "refs/heads/unrelated")
        self.assertEqual(json.loads(settle().stdout)["outcome"], "uncertain")
        f.git(f.remote, "update-ref", "--no-deref", original_ref, tip)
        resolved = settle()
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        self.assertEqual(json.loads(resolved.stdout)["outcome"], "accepted")
        self.assertEqual(snapshot(f.store), before)
        self.assertEqual(f.git(f.remote,"rev-parse",entry["connection"]["ref"]), f.tip)
        self.assertEqual(json.loads(settle().stdout)["outcome"], "accepted")

    def test_settle_requires_exact_tip_instead_of_private_proposal_reachable_by_merge(self):
        import store_publish
        f, edit, _ = self.prepare_edit()
        def ambiguous(stage, _target, _lease, ref, base, proposed, _edit, _account, start):
            start()
            f.git(stage, "push", str(f.remote), proposed + ":refs/heads/unrelated")
            merge = f.git(f.remote, "commit-tree", base + "^{tree}", "-p", base, "-p", proposed, "-m", "Other merge")
            f.git(f.remote, "update-ref", ref, merge, base)
            return "uncertain"
        with patch.object(store_publish, "send", side_effect=ambiguous):
            result = edit()
        self.assertEqual(store_publish.settle(lambda:self.account, self.entries[1]["store_id"], self.generations,
                                             result["attempt_id"])["outcome"], "uncertain")

    def test_publish_revision_keeps_one_link_if_selector_update_is_interrupted(self):
        import store_publish
        f, edit, _ = self.prepare_edit()
        pid = os.fork()
        if pid == 0:
            original_link, original_rename = os.link, store_publish.rename_guarded
            def link(source, dest, **kwargs):
                original_link(source, dest, **kwargs)
                if dest == "2.json":
                    os._exit(77)
            def rename(old_dir, old, new_dir, new, **kwargs):
                original_rename(old_dir, old, new_dir, new, **kwargs)
                if new == "2.json":
                    os._exit(77)
            try:
                with patch.object(os, "link", side_effect=link), patch.object(store_publish, "rename_guarded", side_effect=rename):
                    edit()
            finally:
                os._exit(78)
        self.assertEqual(os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]), 77)
        control = next((self.recovery / "control").iterdir())
        self.assertEqual((control / "records/2.json").stat().st_nlink, 1)
        attempt = control.name
        self.assertEqual(store_publish.settle(lambda:self.account, self.entries[1]["store_id"],
                                             self.generations, attempt)["outcome"], "accepted")

    def test_settle_absent_unknown_candidate_preserves_uncertainty_and_bundle(self):
        import store_mode
        import store_publish
        f, edit, _ = self.prepare_edit()
        before = snapshot(f.store)
        def no_ack(*args):
            args[-1]()
            return "uncertain"
        with patch.object(store_publish, "send", side_effect=no_ack):
            result = edit()
        args = (lambda: self.account, self.entries[1]["store_id"], self.generations, result["attempt_id"])
        self.assertEqual(store_publish.settle(*args)["outcome"], "uncertain")
        state = store_mode.change_mode(*args[:3],"two-way")
        self.assertEqual((state["mode"],state["settle_attempt"]),("two-way",result["attempt_id"]))
        with self.assertRaisesRegex(StoreContextError,result["attempt_id"]):
            edit()
        with self.assertRaises(StoreContextError):
            store_mode.change_mode(*args[:3],"two-way","discard",[result["attempt_id"]])
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
        self.addCleanup(config.parent.rmdir)
        self.addCleanup(config.rmdir)
        self.addCleanup(approval.unlink,missing_ok=True)
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

    def transfer_vaults(self):
        from store_login import verified_age_tools
        from store_transfer_credentials import CredentialRef
        tools, _ = verified_age_tools(self.account)
        def age(name, args=(), data=b"", passphrase=None):
            with ExitStack() as stack:
                env = {"PATH": str(tools)}
                inherited = ()
                if passphrase is not None:
                    reader, writer = os.pipe()
                    stack.callback(os.close, reader)
                    with os.fdopen(writer, "wb") as output:
                        output.write(passphrase)
                    inherited = (reader,)
                    env["AGE_PASSPHRASE_FD"] = str(reader)
                return subprocess.run([tools/name, *args], input=data, env=env, pass_fds=inherited,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                      check=True, timeout=30).stdout
        source, destination = self.resolve(0), self.resolve(1)
        passwords = {}
        identities = {}
        recipients = {}
        for context in (source, destination):
            identity = age("age-keygen")
            recipient = age("age-keygen", ["-y"], identity)
            password = ("fixture " + context.store_id).encode()
            passwords[context.store_id] = password
            identities[context.store_id] = identity
            recipients[context.store_id] = recipient.strip().decode()
            (context.vault/"vault.pub").write_bytes(recipient)
            for name, phrase in (("vault.key.age", password), ("vault.recovery.age", b"fixture recovery")):
                (context.vault/name).write_bytes(age("age", ["-a","-e","-j","batchpass"], identity, phrase))
        def seal(context, value):
            return age("age", ["-a","-e","-r",recipients[context.store_id]], value)
        return SimpleNamespace(source=source, destination=destination, seal=seal, identities=identities,
                               tools=tools, unlock=passwords.__getitem__,
                               src=CredentialRef(source.store_id, self.generations, "login"),
                               dst=CredentialRef(destination.store_id, self.generations, "login"))

    def test_transfer_stages_password_only_for_its_verified_destination(self):
        from store_transfer_credentials import stage_credential
        from store_vault import VaultUnlockCache
        f = self.transfer_vaults()
        value = b"synthetic transfer fixture"
        old_ciphertext = f.seal(f.source, value)
        (f.source.vault/"login.age").write_bytes(old_ciphertext)
        (f.source.vault/"login.meta").write_bytes(b"type=password\ncreated=2026-02-20\n")
        before = snapshot(self.root)
        staged = stage_credential(lambda: self.account, f.src, f.dst, f.unlock)
        self.assertEqual(staged.metadata, (f.source.vault/"login.meta").read_bytes())
        self.assertIsNone(staged.public_key)
        self.assertNotEqual(staged.ciphertext, old_ciphertext)
        vault = VaultUnlockCache(f.tools)
        self.assertTrue(vault.open_secret(f.identities[f.dst.store_id], staged.ciphertext) == value)
        with self.assertRaises(StoreContextError):
            vault.open_secret(f.identities[f.src.store_id], staged.ciphertext)
        self.assertEqual(before, snapshot(self.root))

    def test_transfer_preview_classifies_stable_id_name_and_endpoint_without_exposing_source_text(self):
        from store_transfer import preview
        source_id = new_id()
        f = self.prepare_receive()
        self.assertEqual(f.receive().returncode, 0)
        src = self.write_route(0, source_id, "# private fixture note\n# secret: login\nHost DB\n"
                               "  Hostname 192.0.2.2\n  ProxyJump relay.example\n", name="DB")
        f.git(src.checkout,"init","-q","--template=")
        f.git(src.checkout,"symbolic-ref","HEAD","refs/heads/main")
        f.git(src.checkout,"add",".")
        f.git(src.checkout,"commit","-qm","Fixture selected source")
        dst = self.resolve(1)
        def observe(alias="DB"):
            return preview(lambda: self.account,src.store_id,self.generations,source_id,
                           dst.store_id,self.generations,alias).comparison()
        before = snapshot(self.root)
        result = observe()
        self.assertEqual(result["source"]["host_id"], source_id)
        self.assertEqual(result["source"]["proxyjump"], "relay.example")
        self.assertEqual(result["source"]["credential"], "login")
        self.assertNotIn("private fixture note", json.dumps(result))
        self.assertEqual(result["matches"][0]["reasons"], ["alias","endpoint"])
        self.assertEqual(result["destination"]["proposed_host_id"], source_id)
        self.assertEqual(result["publications"], [{"store_id":dst.store_id,"ref":"refs/heads/main","effect":"destination-save"}])
        self.assertEqual(observe("separate")["matches"][0]["reasons"], ["endpoint"])
        self.assertEqual(snapshot(self.root), before)
        if os.environ.get("HOSTBOOK_TEST_DISPOSABLE_PASSWD_HOME") == "1":
            import pwd
            home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
            self.assertIn(home, (Path("/fixture-home"),Path("/home/hostbook-fixture")))
            self.assertTrue(Path("/.dockerenv").is_file())
            self.assertFalse((home/".config").exists())
            approval = self.write_local_approval(home)
            self.addCleanup(approval.parent.parent.rmdir)
            self.addCleanup(approval.parent.rmdir)
            self.addCleanup(approval.unlink)
            result = subprocess.run([f.app,"store-transfer-preview",src.store_id,"1","2","3","4",source_id,
                                     dst.store_id,"1","2","3","4","DB"],cwd=self.root/"shared b",env=f.env,
                                    capture_output=True,check=True,timeout=15)
            self.assertEqual(json.loads(result.stdout), observe())
        identity = dst.checkout/".hostbook/identity.json"
        data = json.loads(identity.read_bytes())
        existing_key = data["hosts"][0]["host_id"]
        data["hosts"][0]["host_id"] = source_id
        identity.write_text(json.dumps(data))
        f.git(dst.checkout,"add",".hostbook/identity.json")
        f.git(dst.checkout,"commit","-qm","Fixture same host ID")
        f.git(dst.checkout,"push","-q",str(f.remote),"HEAD:refs/heads/main")
        self.assertNotEqual(existing_key, source_id)
        self.assertEqual(observe("separate")["matches"][0]["reasons"], ["stable-id","endpoint"])
        import store_transfer
        original = store_transfer.effective
        def change_mode(config, alias):
            route = original(config, alias)
            if alias == "db":
                self.entries[1]["policy_generation"] += 1
                self.save_registry()
            return route
        with patch.object(store_transfer, "effective", side_effect=change_mode):
            with self.assertRaisesRegex(StoreContextError, "stale store generations"):
                observe()

    def test_transfer_endpoint_compares_literal_ip_and_exact_login(self):
        from store_read import effective, ssh_view
        from store_transfer import endpoint
        conf = (b"Host long\n HostName 2001:0db8:0:0:0:0:0:1\n User Mixed\n Port 23\n"
                b"Host short\n HostName 2001:db8::1\n User Mixed\n Port 23\n")
        with ssh_view([conf], (None,None,None)) as config:
            first, second = (effective(config, name) for name in ("long","short"))
        self.assertEqual(endpoint(first), endpoint(second))
        self.assertEqual(endpoint(dict(first,hostname="2001:0db8:0:0:0:0:0:1")), endpoint(second))
        self.assertNotEqual(endpoint(first), endpoint(dict(second,user="mixed")))
        self.assertNotEqual(endpoint(first), endpoint(dict(second,port="22")))

    def test_transaction_vault_reopens_complete_manifest_from_either_independent_envelope(self):
        from store_transfer_recovery import seal_payload, read_payload, verify_backup
        from store_login import verified_age_tools
        from store_checkout import durable_file
        from store_vault import VaultUnlockCache
        f = self.transfer_vaults()
        password, code = b"independent fixture password", b"independent fixture recovery"
        manifest = b'{"credential":"synthetic private fixture","host":"source.invalid"}'
        payload_id = new_id()
        sealed = seal_payload(self.account, manifest, password, code)
        before = snapshot(self.root)
        with tempfile.TemporaryDirectory(dir=self.root) as scratch:
            directory = os.open(scratch,store_context.DIRECTORY_FLAGS)
            try:
                for name, data in (("vault.pub",sealed.recipient), ("vault.key.age",sealed.password_envelope),
                                   ("vault.recovery.age",sealed.recovery_envelope), (payload_id+".age",sealed.ciphertext)):
                    durable_file(directory, name, data)
                for phrase, recovery in ((password,False),(code,True)):
                    self.assertTrue(read_payload(self.account,directory,directory,payload_id,phrase,
                                                 recovery=recovery) == manifest)
                verify_backup(self.account,directory,directory,payload_id,password,code,manifest)
                backup = Path(scratch)/"vault.recovery.age"
                backup.unlink()
                self.assertTrue(read_payload(self.account,directory,directory,payload_id,password) == manifest)
                with self.assertRaises(FileNotFoundError):
                    verify_backup(self.account,directory,directory,payload_id,password,code,manifest)
                durable_file(directory,"vault.recovery.age",sealed.recovery_envelope)
                open_secret = VaultUnlockCache.open_secret
                def change_permission(cache, identity, ciphertext):
                    os.fchmod(directory,0o755)
                    return open_secret(cache,identity,ciphertext)
                try:
                    with patch.object(VaultUnlockCache,"open_secret",change_permission):
                        with self.assertRaisesRegex(StoreContextError, "private"):
                            verify_backup(self.account,directory,directory,payload_id,password,code,manifest)
                finally:
                    os.fchmod(directory,0o700)
                with self.assertRaises(StoreContextError):
                    read_payload(self.account,directory,directory,payload_id,f.unlock(f.src.store_id))
                vault = VaultUnlockCache(verified_age_tools(self.account)[0])
                for identity in f.identities.values():
                    with self.assertRaises(StoreContextError):
                        vault.open_secret(identity,sealed.ciphertext)
                (Path(scratch)/(payload_id+".age")).write_bytes(sealed.ciphertext[:60])
                with self.assertRaises(StoreContextError):
                    read_payload(self.account,directory,directory,payload_id,password)
            finally:
                os.close(directory)
        self.assertEqual(snapshot(self.root), before)

    def transfer_case(self, *, shared_credential=False, prod=False):
        from store_transfer import preview
        vaults = self.transfer_vaults()
        host = new_id()
        self.write_route(0, host, ("# tags: prod\n" if prod else "")+"# secret: login\nHost source-one\n  HostName 192.0.2.61\n"
                         "  User fixture\n  Port 2210\n  ProxyJump relay.example\n", name="source-one")
        if shared_credential:
            other = b"# secret: login\nHost stays\n  HostName 192.0.2.77\n"
            folder = vaults.source.checkout
            (folder/"stays.conf").write_bytes(other)
            sidecar = folder/".hostbook/identity.json"
            data = json.loads(sidecar.read_bytes())
            data["sources"].append({"path":"stays.conf","sha256":hashlib.sha256(other).hexdigest()})
            data["hosts"].append({"host_id":new_id(),"name":"stays","bindings":[
                {"path":"stays.conf","host_ordinal":1,"token_ordinal":1}]})
            sidecar.write_text(json.dumps(data))
        (vaults.source.vault/"login.age").write_bytes(vaults.seal(vaults.source,b"fixture mapped password"))
        (vaults.source.vault/"login.meta").write_bytes(b"type=password\n")
        shared = self.prepare_receive()
        self.assertEqual(shared.receive().returncode, 0)
        for args in (("init","-q","--template="), ("symbolic-ref","HEAD","refs/heads/main"), ("add","."),
                     ("commit","-qm","Fixture private source")):
            shared.git(vaults.source.checkout,*args)
        self.entries[1]["effective_mode"] = "two-way"
        self.save_registry()
        load = lambda: self.account
        plan = preview(load,vaults.src.store_id,self.generations,host,vaults.dst.store_id,self.generations,"moved")
        return SimpleNamespace(plan=plan, shared=shared, vaults=vaults, load=load, host=host)

    def reverse_transfer_case(self):
        from store_transfer import preview
        from store_transfer_credentials import CredentialRef
        seed = self.transfer_case()
        v,shared = seed.vaults,seed.shared
        src,dst = v.destination,v.source
        raw = b"# secret: team-key\nHost db\n  HostName 192.0.2.2\n  User fixture\n"
        (src.checkout/"team.conf").write_bytes(raw)
        path = src.checkout/".hostbook/identity.json"
        identity = json.loads(path.read_bytes())
        identity["generation"] += 1
        identity["sources"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
        path.write_text(json.dumps(identity))
        (src.vault/"team-key.age").write_bytes(v.seal(src,b"shared fixture password"))
        (src.vault/"team-key.meta").write_bytes(b"type=password\n")
        shared.git(src.checkout,"add","--all")
        shared.git(src.checkout,"commit","-qm","Shared host uses its own fixture credential")
        shared.git(src.checkout,"push","-q",str(shared.remote),"HEAD:refs/heads/main")
        host = identity["hosts"][0]["host_id"]
        plan = preview(seed.load,src.store_id,src.generations,host,dst.store_id,dst.generations,"personal-db")
        return SimpleNamespace(plan=plan,load=seed.load,shared=shared,v=v,src=src,dst=dst,
            original=CredentialRef(src.store_id,src.generations,"team-key"),
            mapped=CredentialRef(dst.store_id,dst.generations,"db-credential"))

    def test_reverse_transfer_confirms_saved_decryptable_private_destination_before_touching_shared_source(self):
        from store_transfer import prepare_candidate
        from store_transfer_credentials import stage_credential
        from store_transfer_intent import prepare_intent, reopen_intent
        from store_transfer_apply import save_destination
        import store_transfer_publish as publish
        f = self.reverse_transfer_case()
        mapped = stage_credential(f.load,f.original,f.mapped,f.v.unlock)
        pw,code = b"reverse backup fixture",b"reverse recovery fixture"
        before,remote = snapshot(f.src.checkout),snapshot(f.shared.remote)
        with prepare_candidate(f.load,f.plan,mapped) as candidate:
            ident = prepare_intent(f.load,f.plan,candidate,mapped,f.v.unlock,pw,code,lambda _:True)
            save_destination(f.load,ident,f.plan,candidate,mapped,f.v.unlock,pw,code)
            result = publish.confirm_private_destination(f.load,ident,f.plan,candidate,mapped,f.v.unlock,pw,code)
            self.assertEqual(result["checkpoint"],"destination-confirmed")
            record,manifest = reopen_intent(f.load,ident,code,recovery=True)
            self.assertEqual(record["attempts"],[])
            self.assertEqual(manifest["confirmed_destination"],candidate.proposed)
            self.assertEqual(f.shared.git(f.dst.checkout,"rev-parse","HEAD"),candidate.proposed)
            self.assertEqual(f.shared.git(f.src.checkout,"rev-parse","HEAD"),f.plan.source.base)
            self.assertEqual(f.shared.git(f.shared.remote,"rev-parse","refs/heads/main"),f.plan.source.base)
            mapped.verify_saved(f.load,f.v.unlock)
        self.assertEqual(snapshot(f.src.checkout),before)
        self.assertEqual(snapshot(f.shared.remote),remote)

    def check_shared_source_publication(self, refused, *, editor=False):
        from store_transfer import prepare_candidate
        from store_transfer_credentials import stage_credential
        from store_transfer_intent import prepare_intent, reopen_intent
        from store_transfer_apply import save_destination
        import store_transfer_publish as publisher
        import store_transfer_remove as removal
        f = self.reverse_transfer_case()
        mapped = stage_credential(f.load,f.original,f.mapped,f.v.unlock)
        pw,code = b"reverse backup fixture",b"reverse recovery fixture"
        send = publisher.send
        attempted = []
        with prepare_candidate(f.load,f.plan,mapped) as candidate:
            ident = prepare_intent(f.load,f.plan,candidate,mapped,f.v.unlock,pw,code,lambda _:True)
            save_destination(f.load,ident,f.plan,candidate,mapped,f.v.unlock,pw,code)
            publisher.confirm_private_destination(f.load,ident,f.plan,candidate,mapped,f.v.unlock,pw,code)
            before = snapshot(f.dst.checkout)
            source_vault = snapshot(f.src.vault)
            args = f.load,ident,f.plan,candidate,mapped,f.v.unlock,pw,code
            result = removal.remove_source(*args)
            self.assertEqual(result["checkpoint"],"source-removed-local")
            self.assertEqual(f.shared.git(f.shared.remote,"rev-parse","refs/heads/main"),f.plan.source.base)
            self.assertFalse((f.src.checkout/"team.conf").exists())
            if editor:
                record,manifest = reopen_intent(f.load,ident,pw)
                captured = (f.src.checkout/".git").joinpath(*manifest["source_runtime"],"source")
                changed = captured.read_bytes()+b"# independent save through previously opened source\n"
                captured.write_bytes(changed)
                with self.assertRaisesRegex(StoreContextError,"local changes"):
                    publisher.publish_source(*args)
                self.assertEqual(captured.read_bytes(),changed)
                self.assertEqual(f.shared.git(f.shared.remote,"rev-parse","refs/heads/main"),f.plan.source.base)
                self.assertEqual(reopen_intent(f.load,ident,pw)[0]["attempts"],[])
                return
            def checked(*call):
                record,protected = reopen_intent(f.load,ident,pw)
                attempt = record["attempts"][0]
                self.assertEqual((attempt["store_id"],attempt["outcome"]),(f.src.store_id,"reserved"))
                self.assertEqual((call[3],call[4],call[5]),(f.plan.source.ref,f.plan.source.base,candidate.source_proposed))
                self.assertEqual(protected["publication"]["target"]["inode"],f.shared.remote.stat().st_ino)
                if refused:
                    f.shared.git(f.shared.author,"fetch","-q",str(f.shared.remote),"refs/heads/main")
                    f.shared.git(f.shared.author,"merge","-q","--ff-only","FETCH_HEAD")
                    (f.shared.author/"independent").write_text("unrelated independent update")
                    f.shared.git(f.shared.author,"add","independent")
                    f.shared.git(f.shared.author,"commit","-qm","Independent shared change")
                    f.shared.git(f.shared.author,"push","-q",str(f.shared.remote),"HEAD:refs/heads/main")
                attempted.append(attempt["attempt_id"])
                return send(*call)
            with patch.object(publisher,"send",side_effect=checked):
                sent = publisher.publish_source(*args)
            self.assertEqual(len(attempted),1)
            self.assertEqual(snapshot(f.dst.checkout),before)
            self.assertEqual(snapshot(f.src.vault),source_vault)
            record,manifest = reopen_intent(f.load,ident,code,recovery=True)
            self.assertEqual(record["attempts"][0]["attempt_id"],attempted[0])
            self.assertIn("source_bundle",manifest)
            if refused:
                self.assertEqual(sent["outcome"],"not-accepted")
                self.assertEqual(record["checkpoint"],"source-removed-local")
                self.assertIn("Host db",f.shared.git(f.shared.remote,"show","refs/heads/main:team.conf"))
                self.assertEqual(f.shared.git(f.shared.remote,"show","main:independent"),"unrelated independent update")
                with self.assertRaises(StoreContextError):
                    removal.finish_shared_source(*args)
                self.assertTrue((f.src.checkout/".git").joinpath(*manifest["source_runtime"],"source").is_file())
            else:
                self.assertEqual(sent["checkpoint"],"source-confirmed")
                self.assertEqual(f.shared.git(f.shared.remote,"rev-parse","refs/heads/main"),candidate.source_proposed)
                self.assertEqual(removal.finish_shared_source(*args)["checkpoint"],"completed")
                self.assertEqual(f.shared.git(f.src.checkout,"status","--porcelain"),"")
                from store_journal import require_idle
                from store_publish import recovery_root
                with recovery_root(self.account) as root:
                    for ctx in (f.src,f.dst):
                        require_idle(root,self.account,ctx.store_id)

    def test_shared_source_publishes_exact_reviewed_removal_after_private_destination_is_verified(self):
        self.check_shared_source_publication(False)

    def test_shared_source_refusal_retains_remote_host_private_copy_and_encrypted_recovery(self):
        self.check_shared_source_publication(True)

    def test_shared_source_publication_preserves_an_edit_to_the_displaced_source(self):
        self.check_shared_source_publication(False,editor=True)

    def test_transfer_candidate_preserves_exact_id_settings_and_verified_destination_password(self):
        from store_transfer import prepare_candidate
        from store_transfer_credentials import stage_credential
        from store_receive import git
        from store_vault import VaultUnlockCache
        f = self.transfer_case()
        vaults = f.vaults
        mapped = stage_credential(f.load,vaults.src,replace(vaults.dst,name="team-login"),vaults.unlock)
        before = snapshot(self.root)
        with prepare_candidate(f.load, f.plan, mapped) as candidate:
            repo, proposed = candidate.stage, candidate.proposed
            self.assertEqual(git(repo,"rev-parse",proposed+"^").decode().strip(), f.plan.destination.base)
            sidecar = json.loads(git(repo,"show",proposed+":.hostbook/identity.json"))
            selected = next(row for row in sidecar["hosts"] if row["host_id"] == f.host)
            self.assertEqual(selected["name"], "moved")
            document = git(repo,"show",proposed+":"+selected["bindings"][0]["path"])
            self.assertIn(b"# secret: team-login\n",document)
            self.assertIn(b"  ProxyJump relay.example\n",document)
            ciphertext = git(repo,"show",proposed+":secrets/team-login.age")
            self.assertTrue(VaultUnlockCache(vaults.tools).open_secret(vaults.identities[vaults.dst.store_id],
                                                                       ciphertext) == b"fixture mapped password")
            removal = candidate.source_proposed
            self.assertEqual(git(candidate.source_stage,"rev-parse",removal+"^").decode().strip(),f.plan.source.base)
            remaining = json.loads(git(candidate.source_stage,"show",removal+":.hostbook/identity.json"))
            self.assertFalse(any(row["host_id"] == f.host for row in remaining["hosts"]))
            removed = git(candidate.source_stage,"diff-tree","--no-commit-id","--name-only","--no-renames","-r",removal)
            self.assertEqual(set(removed.decode().splitlines()),{".hostbook/identity.json",candidate.source_path})
        self.assertEqual(snapshot(self.root), before)

    def test_transfer_candidate_requires_the_fetched_vault_and_reused_credential(self):
        from store_transfer import prepare_candidate
        from store_transfer_credentials import stage_credential
        f = self.transfer_case()
        v = f.vaults
        meta, age = (v.destination.vault/("existing."+suffix) for suffix in ("meta","age"))
        meta.write_bytes(b"type=password\n")
        age.write_bytes(v.seal(v.destination,b"fixture mapped password"))
        def rejected(mapping):
            before = snapshot(self.root)
            with self.assertRaisesRegex(StoreContextError, "accepted destination"):
                with prepare_candidate(f.load,f.plan,mapping):
                    self.fail("unverified destination mapping")
            self.assertEqual(snapshot(self.root), before)
        reused = stage_credential(f.load,v.src,replace(v.dst,name="existing"),v.unlock,reuse_existing=True)
        rejected(reused)
        meta.unlink()
        age.unlink()
        (v.destination.vault/"vault.recovery.age").write_bytes((v.destination.vault/"vault.key.age").read_bytes())
        rejected(stage_credential(f.load,v.src,v.dst,v.unlock))

    def test_transfer_candidate_requires_native_authentication_settings_for_host_and_jump(self):
        from store_transfer import prepare_candidate, preview
        from store_transfer_credentials import stage_credential
        f = self.transfer_case()
        v = f.vaults
        mapped = stage_credential(f.load,v.src,v.dst,v.unlock)
        local = self.state/"stores/v1"/v.dst.store_id/"ssh"
        local.mkdir(mode=0o700)
        config = local/"config"
        for name in ("moved","relay.example"):
            config.write_text(f"Host {name}\n  PasswordAuthentication no\n  KbdInteractiveAuthentication no\n")
            config.chmod(0o600)
            plan = preview(f.load,v.src.store_id,self.generations,f.host,v.dst.store_id,self.generations,"moved")
            before = snapshot(self.root)
            with self.assertRaisesRegex(StoreContextError, "selected route"):
                with prepare_candidate(f.load,plan,mapped):
                    self.fail("incompatible native authentication admitted")
            self.assertEqual(before,snapshot(self.root))

    def test_transfer_candidate_rechecks_receiver_policy_after_preparation(self):
        import store_transfer
        from store_transfer_credentials import stage_credential
        f = self.transfer_case()
        v = f.vaults
        mapped = stage_credential(f.load,v.src,v.dst,v.unlock)
        hook = f.shared.remote/"hooks"/"update"
        hook.parent.mkdir(exist_ok=True)
        def change_policy(*args):
            hook.touch(mode=0o700, exist_ok=True)
            return original(*args)
        original = store_transfer.tree
        with patch.object(store_transfer,"tree",side_effect=change_policy):
            with self.assertRaisesRegex(StoreContextError, "server hooks"):
                with store_transfer.prepare_candidate(f.load,f.plan,mapped):
                    self.fail("old receiver policy reused")

    def test_confirmed_transfer_persists_independent_prepared_intent_before_either_checkout_changes(self):
        from store_transfer import prepare_candidate
        from store_transfer_credentials import stage_credential
        from store_transfer_intent import prepare_intent, reopen_intent
        from store_journal import require_idle
        from store_publish import recovery_root
        import base64
        f = self.transfer_case()
        v = f.vaults
        mapping = stage_credential(f.load,v.src,replace(v.dst,name="team-login"),v.unlock)
        branches = [v.source.checkout,v.destination.checkout,f.shared.remote,self.root/"shared b"]
        before = [snapshot(root) for root in branches]
        password, code = b"transaction fixture password", b"transaction fixture recovery code"
        with prepare_candidate(f.load,f.plan,mapping) as candidate:
            with self.assertRaisesRegex(StoreContextError, "confirmation"):
                prepare_intent(f.load,f.plan,candidate,mapping,v.unlock,password,code,lambda _:False)
            self.assertFalse((self.recovery/"transactions").exists())
            called = []
            def confirm(comparison):
                called.append(comparison)
                return True
            ident = prepare_intent(f.load,f.plan,candidate,mapping,v.unlock,password,code,confirm)
            self.assertEqual(len(called),1)
            self.assertEqual(called[0]["destination_credential"],"team-login")
            proposal = candidate.proposed
        self.assertEqual([snapshot(root) for root in branches],before)
        with recovery_root(self.account) as root:
            for store in (v.src.store_id,v.dst.store_id):
                with self.assertRaises(StoreContextError):
                    require_idle(root,self.account,store)
                hint = self.recovery/"stores"/store/(ident+".ref")
                self.assertEqual(json.loads(hint.read_bytes()),{"version":1,"transaction_id":ident})
            require_idle(root,self.account,self.entries[2]["store_id"])
        record, manifest = reopen_intent(f.load,ident,code,recovery=True)
        self.assertEqual(record["checkpoint"],"prepared")
        self.assertEqual(record["transaction_id"],ident)
        self.assertEqual(manifest["proposed"],proposal)
        self.assertEqual(manifest["destination_ref"],"refs/heads/main")
        from store_transfer_owner import owner_lease
        with recovery_root(self.account) as root, store_context.open_directory(root,("transactions",ident)) as tx:
            with owner_lease(tx,record["owner"],manifest) as lease:
                self.assertEqual(os.fstat(lease).st_ino,manifest["lease"]["inode"])
        self.assertTrue(base64.b64decode(manifest["credentials"]["source"]["value"]) == b"fixture mapped password")
        for expanded in manifest["credentials"].values():
            self.assertNotIn("vault_identity", expanded.keys())
        contents = json.dumps(manifest).encode()
        self.assertFalse(any(base64.b64encode(identity) in contents for identity in v.identities.values()))
        visible = self.recovery/"transactions"/ident
        for file in visible.rglob("*"):
            if file.is_file():
                data = file.read_bytes()
                self.assertFalse(any(secret in data for secret in (b"192.0.2.61",b"team-login",b"relay.example",
                                                                   b"fixture mapped password",password,code)))
        from store_transfer_recovery import read_payload
        current = visible/"records/1.json"
        def replace_during_unlock(*args, **kwargs):
            result = read_payload(*args,**kwargs)
            changed = dict(record, payload_id=new_id())
            current.write_text(json.dumps(changed))
            return result
        with patch("store_transfer_intent.read_payload",side_effect=replace_during_unlock):
            with self.assertRaisesRegex(StoreContextError, "changed"):
                reopen_intent(f.load,ident,password)

    def test_transfer_preview_cleans_private_ssh_view_on_termination(self):
        library = Path(__file__).resolve().parents[1]/"lib"
        script = """
import signal, sys
sys.path.insert(0,sys.argv[1])
import store_transfer
from store_read import ssh_view
def wait(*_):
    with ssh_view([b'# fixture note\\nHost fixture\\n HostName 192.0.2.7\\n'], (None,None,None)) as config:
        print(config.parent,flush=True)
        signal.pause()
store_transfer.preview = wait
store_transfer.preview_command(['fixture','1','2','3','4','host','dest','1','2','3','4'])
"""
        for sig in (signal.SIGTERM, signal.SIGHUP):
            with subprocess.Popen([sys.executable,"-I","-B","-c",script,str(library)], stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True) as child:
                self.assertTrue(select.select([child.stdout],[],[],5)[0])
                folder = Path(child.stdout.readline().strip())
                self.assertEqual(folder.parent.resolve(), Path("/tmp").resolve())
                self.assertTrue(folder.name.startswith("hb-route-"))
                self.addCleanup(shutil.rmtree, folder, True)
                child.send_signal(sig)
                child.wait(timeout=5)
                self.assertFalse(folder.exists())

    def test_transfer_prepare_reports_original_transaction_after_first_publication_flush_fails(self):
        import store_transfer_intent as intent
        from store_transfer import prepare_candidate
        from store_transfer_credentials import stage_credential
        f = self.transfer_case()
        v = f.vaults
        mapping = stage_credential(f.load,v.src,v.dst,v.unlock)
        failures = []
        renamed = []
        real_rename, real_sync = intent.rename_guarded, intent.sync_local
        def rename(*args,**kwargs):
            real_rename(*args,**kwargs)
            renamed.append(args[3])
        def sync(fd):
            if renamed and not failures:
                failures.append(True)
                raise OSError("fixture directory flush failed")
            real_sync(fd)
        with prepare_candidate(f.load,f.plan,mapping) as candidate:
            with patch.object(intent,"rename_guarded",rename), patch.object(intent,"sync_local",sync):
                with self.assertRaises(StoreContextError) as outcome:
                    intent.prepare_intent(f.load,f.plan,candidate,mapping,v.unlock,b"fixture password",b"fixture recovery",
                                          lambda _:True)
        self.assertEqual(len(renamed),1)
        self.assertIn(renamed[0],str(outcome.exception))
        self.assertTrue((self.recovery/"transactions"/renamed[0]/"current.json").is_file())

    def test_transfer_owner_distinguishes_native_process_incarnation_and_exclusive_lease(self):
        from store_transfer_owner import new_owner, native_start, owner_alive, owner_lease
        owner, start = new_owner()
        self.assertEqual(new_owner()[0]["process_start_id"],owner["process_start_id"])
        self.assertEqual(start,native_start(os.getpid()))
        self.assertTrue(owner_alive(owner,start))
        with subprocess.Popen([sys.executable,"-c","import sys; sys.stdin.buffer.read()"],stdin=subprocess.PIPE) as child:
            child_start = native_start(child.pid)
            self.assertTrue(owner_alive(dict(owner,process_id=child.pid),child_start))
            with patch("store_transfer_owner.native_start",return_value="different native incarnation"):
                self.assertFalse(owner_alive(dict(owner,process_id=child.pid),child_start))
            child.stdin.close()
            child.wait(timeout=5)
            self.assertFalse(owner_alive(dict(owner,process_id=child.pid),child_start))
        directory = os.open(self.recovery, store_context.DIRECTORY_FLAGS)
        try:
            lease = self.recovery/"owner.lease"
            lease.touch(mode=0o600)
            info = lease.stat()
            manifest = {"native_start":start,"lease":{"device":info.st_dev,"inode":info.st_ino}}
            with owner_lease(directory,owner,manifest):
                with self.assertRaisesRegex(StoreContextError, "busy"):
                    with owner_lease(directory,owner,manifest):
                        self.fail("second claimant acquired lease")
            lease.rename(self.recovery/"old.lease")
            lease.touch(mode=0o600)
            with self.assertRaisesRegex(StoreContextError,"binding changed"):
                with owner_lease(directory,owner,manifest):
                    self.fail("substituted lease acquired")
        finally:
            os.close(directory)

    def test_confirmed_destination_save_is_durable_and_keeps_source_and_remote_intact(self):
        from store_transfer import prepare_candidate
        from store_transfer_credentials import stage_credential
        from store_transfer_intent import prepare_intent, reopen_intent
        from store_transfer_apply import save_destination
        f = self.transfer_case()
        v = f.vaults
        mapped = stage_credential(f.load,v.src,replace(v.dst,name="team-login"),v.unlock)
        password,code = b"fixture transaction secret",b"fixture transaction recovery"
        original = [snapshot(path) for path in (v.source.checkout,f.shared.remote,self.root/"shared b")]
        with prepare_candidate(f.load,f.plan,mapped) as candidate:
            ident = prepare_intent(f.load,f.plan,candidate,mapped,v.unlock,password,code,lambda _:True)
            initial_destination = snapshot(v.destination.checkout)
            with self.assertRaisesRegex(StoreContextError,"vault unlock failed"):
                save_destination(f.load,ident,f.plan,candidate,mapped,lambda _:b"mistyped fixture",password,code)
            self.assertEqual(snapshot(v.destination.checkout),initial_destination)
            self.assertEqual(reopen_intent(f.load,ident,password)[0]["sequence"],1)
            group = v.source.checkout/"groups.tsv"
            group.write_text("new\t\tsource-one\n")
            with self.assertRaisesRegex(StoreContextError, "untracked"):
                save_destination(f.load,ident,f.plan,candidate,mapped,v.unlock,password,code)
            group.unlink()
            result = save_destination(f.load,ident,f.plan,candidate,mapped,v.unlock,password,code)
            self.assertEqual(result["checkpoint"],"destination-saved")
            self.assertEqual(f.shared.git(v.destination.checkout,"rev-parse","HEAD"),candidate.proposed)
        self.assertEqual(original,[snapshot(p) for p in (v.source.checkout,f.shared.remote,self.root/"shared b")])
        self.assertEqual(f.shared.git(v.destination.checkout,"status","--porcelain"),"")
        mapped.verify_saved(f.load,v.unlock)
        record,manifest = reopen_intent(f.load,ident,code,recovery=True)
        self.assertEqual(record["sequence"],3)
        self.assertEqual(record["effects"][0]["outcome"],"observed")
        self.assertEqual(manifest["next_effect"],"destination-publication")
        self.assertFalse((self.recovery/"receives"/ident).exists())

    def test_transfer_destination_checkpoint_rechecks_actual_checkout_after_verification(self):
        from store_transfer import prepare_candidate
        from store_transfer_credentials import stage_credential
        from store_transfer_intent import prepare_intent, reopen_intent
        import store_transfer_apply as effects
        f = self.transfer_case()
        v = f.vaults
        mapped = stage_credential(f.load,v.src,v.dst,v.unlock)
        password,code = b"fixture transaction password",b"fixture transaction recovery"
        with prepare_candidate(f.load,f.plan,mapped) as candidate:
            ident = prepare_intent(f.load,f.plan,candidate,mapped,v.unlock,password,code,lambda _:True)
            actual = v.destination.checkout/("host-"+f.host+".conf")
            advance = effects.advance
            def concurrent_edit(tx,account,record,manifest,new,*args,**kwargs):
                if new["checkpoint"] == "destination-saved":
                    actual.write_bytes(b"Host independently-edited\n  HostName 198.51.100.7\n")
                return advance(tx,account,record,manifest,new,*args,**kwargs)
            with patch.object(effects,"advance",side_effect=concurrent_edit):
                with self.assertRaises(StoreContextError):
                    effects.save_destination(f.load,ident,f.plan,candidate,mapped,v.unlock,password,code)
        self.assertTrue(actual.read_bytes().startswith(b"Host independently-edited"))
        self.assertEqual(reopen_intent(f.load,ident,password)[0]["checkpoint"],"prepared")
        self.assertEqual(f.shared.git(f.shared.remote,"rev-parse","refs/heads/main"),f.plan.destination.base)

    def test_transfer_saved_generation_must_still_be_named_before_actual_destination_write(self):
        from store_transfer import prepare_candidate
        from store_transfer_credentials import stage_credential
        from store_transfer_intent import prepare_intent
        import store_transfer_apply as effects
        f = self.transfer_case()
        v = f.vaults
        mapped = stage_credential(f.load,v.src,v.dst,v.unlock)
        password,code = b"fixture independent password",b"fixture recovery"
        with prepare_candidate(f.load,f.plan,mapped) as candidate:
            ident = prepare_intent(f.load,f.plan,candidate,mapped,v.unlock,password,code,lambda _:True)
            before = snapshot(v.destination.checkout)
            advance = effects.advance
            def detach(*args,**kwargs):
                result = advance(*args,**kwargs)
                if result[0]["sequence"] == 2:
                    payload = self.recovery/"transactions"/ident/"payloads"/(result[0]["payload_id"]+".age")
                    payload.rename(payload.with_suffix(".detached"))
                return result
            with patch.object(effects,"advance",side_effect=detach):
                with self.assertRaises((StoreContextError,OSError)):
                    effects.save_destination(f.load,ident,f.plan,candidate,mapped,v.unlock,password,code)
            self.assertTrue(snapshot(v.destination.checkout) == before)

    def test_transfer_publishes_only_the_reserved_destination_branch_after_local_save(self):
        from store_transfer import prepare_candidate
        from store_transfer_credentials import stage_credential
        from store_transfer_intent import prepare_intent, reopen_intent
        from store_transfer_apply import save_destination
        import store_transfer_publish as effects
        f = self.transfer_case()
        v = f.vaults
        mapped = stage_credential(f.load,v.src,replace(v.dst,name="team-login"),v.unlock)
        password,code = b"fixture independent password",b"fixture independent recovery"
        original = snapshot(v.source.checkout)
        with prepare_candidate(f.load,f.plan,mapped) as candidate:
            ident = prepare_intent(f.load,f.plan,candidate,mapped,v.unlock,password,code,lambda _:True)
            save_destination(f.load,ident,f.plan,candidate,mapped,v.unlock,password,code)
            calls = []
            send = effects.send
            def verify_reserved(*args):
                record, protected = reopen_intent(f.load,ident,code,recovery=True)
                attempt = record["attempts"][-1]
                self.assertEqual(attempt["outcome"],"reserved")
                self.assertEqual(attempt["sender_state"],"active")
                self.assertEqual(protected["publication"]["base"],f.plan.destination.base)
                self.assertEqual(protected["publication"]["proposed"],candidate.proposed)
                calls.append(attempt["attempt_id"])
                return send(*args)
            with patch.object(effects,"send",side_effect=verify_reserved):
                result = effects.publish_destination(f.load,ident,f.plan,candidate,mapped,v.unlock,password,code)
            self.assertEqual(result["checkpoint"],"destination-confirmed")
            self.assertEqual(result["outcome"],"accepted")
            self.assertEqual(f.shared.git(f.shared.remote,"rev-parse","refs/heads/main"),candidate.proposed)
        self.assertEqual(snapshot(v.source.checkout),original)
        self.assertEqual(len(calls),1)
        record,protected = reopen_intent(f.load,ident,password)
        self.assertEqual(record["attempts"][0]["attempt_id"],calls[0])
        self.assertEqual(record["attempts"][0]["sender_state"],"quiescent")
        self.assertEqual(record["attempts"][0]["outcome"],"accepted")
        self.assertEqual(record["checkpoint"],"destination-confirmed")

    def check_transfer_send_failure(self, lost_reply):
        from store_transfer import prepare_candidate
        from store_transfer_credentials import stage_credential
        from store_transfer_intent import prepare_intent, reopen_intent
        from store_transfer_apply import save_destination
        import store_transfer_publish as publisher
        f = self.transfer_case()
        v = f.vaults
        mapped = stage_credential(f.load,v.src,v.dst,v.unlock)
        password,code = b"transaction failure fixture",b"independent recovery fixture"
        original = snapshot(v.source.checkout)
        send = publisher.send
        expected = []
        def changed(*args):
            if lost_reply:
                self.assertEqual(send(*args),"accepted")
                expected.append(args[5])
                raise StoreContextError("fixture lost reply")
            (f.shared.author/"independent").write_text("fixture independent update")
            f.shared.git(f.shared.author,"add","independent")
            f.shared.git(f.shared.author,"commit","-qm","Independent fixture update")
            f.shared.git(f.shared.author,"push","-q",str(f.shared.remote),"HEAD:refs/heads/main")
            expected.append(f.shared.git(f.shared.author,"rev-parse","HEAD"))
            return send(*args)
        with prepare_candidate(f.load,f.plan,mapped) as candidate:
            ident = prepare_intent(f.load,f.plan,candidate,mapped,v.unlock,password,code,lambda _:True)
            save_destination(f.load,ident,f.plan,candidate,mapped,v.unlock,password,code)
            with patch.object(publisher,"send",side_effect=changed):
                result = publisher.publish_destination(f.load,ident,f.plan,candidate,mapped,v.unlock,password,code)
        outcome = "uncertain" if lost_reply else "not-accepted"
        self.assertEqual(result["outcome"],outcome)
        self.assertEqual(f.shared.git(f.shared.remote,"rev-parse","refs/heads/main"),expected[0])
        record,_ = reopen_intent(f.load,ident,code,recovery=True)
        self.assertEqual(record["checkpoint"],"destination-saved")
        self.assertEqual(record["attempts"][0]["outcome"],outcome)
        self.assertEqual(snapshot(v.source.checkout),original)

    def test_transfer_rejected_native_destination_send_preserves_source_and_independent_remote_work(self):
        self.check_transfer_send_failure(False)

    def test_transfer_lost_destination_ack_keeps_source_and_uncertain_original_attempt(self):
        self.check_transfer_send_failure(True)

    def check_transfer_remote_changes_after_ack(self, replaced):
        from store_transfer import prepare_candidate
        from store_transfer_credentials import stage_credential
        from store_transfer_intent import prepare_intent, reopen_intent
        from store_transfer_apply import save_destination
        import store_transfer_publish as publisher
        f = self.transfer_case()
        v = f.vaults
        mapped = stage_credential(f.load,v.src,v.dst,v.unlock)
        password,code = b"fixture transaction password",b"fixture transaction recovery"
        original = snapshot(v.source.checkout)
        advance = publisher.advance
        def remote_advances(tx,account,record,manifest,new,*args,**kwargs):
            if new["checkpoint"] == "destination-confirmed":
                if replaced:
                    retired = self.root/"earlier-target"
                    f.shared.remote.rename(retired)
                    f.shared.git(self.root,"clone","--bare","--no-local",str(retired),str(f.shared.remote))
                else:
                    author = f.shared.author
                    f.shared.git(author,"pull","--ff-only",str(f.shared.remote),"main")
                    (author/"independent").write_text("fixture later work")
                    f.shared.git(author,"add","independent")
                    f.shared.git(author,"commit","-qm","Remote advanced after transfer acceptance")
                    f.shared.git(author,"push","-q",str(f.shared.remote),"HEAD:refs/heads/main")
            return advance(tx,account,record,manifest,new,*args,**kwargs)
        with prepare_candidate(f.load,f.plan,mapped) as candidate:
            ident = prepare_intent(f.load,f.plan,candidate,mapped,v.unlock,password,code,lambda _:True)
            save_destination(f.load,ident,f.plan,candidate,mapped,v.unlock,password,code)
            with patch.object(publisher,"advance",side_effect=remote_advances):
                result = publisher.publish_destination(f.load,ident,f.plan,candidate,mapped,v.unlock,password,code)
        record,_ = reopen_intent(f.load,ident,password)
        self.assertEqual(record["checkpoint"],"destination-saved")
        self.assertEqual(record["attempts"][0]["outcome"],"accepted")
        self.assertEqual(record["attempts"][0]["sender_state"],"quiescent")
        self.assertEqual(result["outcome"],"accepted")
        self.assertEqual(snapshot(v.source.checkout),original)

    def test_transfer_keeps_exact_historical_acceptance_after_remote_advances_during_confirmation(self):
        self.check_transfer_remote_changes_after_ack(False)

    def test_transfer_confirmation_requires_currently_named_registered_receiver(self):
        self.check_transfer_remote_changes_after_ack(True)

    def test_private_source_removal_finishes_only_after_destination_acceptance_and_keeps_shared_credential(self):
        from store_transfer import prepare_candidate
        from store_transfer_credentials import stage_credential
        from store_transfer_intent import prepare_intent, reopen_intent
        from store_transfer_apply import save_destination
        from store_transfer_publish import publish_destination
        from store_transfer_remove import finish_private_source
        from store_publish import recovery_root
        from store_journal import require_idle
        f = self.transfer_case(shared_credential=True)
        v = f.vaults
        mapping = stage_credential(f.load,v.src,v.dst,v.unlock)
        pw,code = b"operator backup fixture",b"operator recovery fixture"
        vault_before = snapshot(v.source.vault)
        with prepare_candidate(f.load,f.plan,mapping) as candidate:
            ident = prepare_intent(f.load,f.plan,candidate,mapping,v.unlock,pw,code,lambda _:True)
            save_destination(f.load,ident,f.plan,candidate,mapping,v.unlock,pw,code)
            before = snapshot(v.source.checkout)
            with self.assertRaisesRegex(StoreContextError,"confirmed destination"):
                finish_private_source(f.load,ident,f.plan,candidate,mapping,v.unlock,pw,code)
            self.assertEqual(snapshot(v.source.checkout),before)
            published = publish_destination(f.load,ident,f.plan,candidate,mapping,v.unlock,pw,code)
            self.assertEqual(published["checkpoint"],"destination-confirmed")
            result = finish_private_source(f.load,ident,f.plan,candidate,mapping,v.unlock,pw,code)
            self.assertEqual(result["checkpoint"],"completed")
            self.assertEqual(f.shared.git(v.source.checkout,"rev-parse","HEAD"),candidate.source_proposed)
            self.assertEqual(f.shared.git(f.shared.remote,"rev-parse","main"),candidate.proposed)
            self.assertFalse((v.source.checkout/candidate.source_path).exists())
        self.assertEqual(f.shared.git(v.source.checkout,"status","--porcelain"),"")
        self.assertEqual(snapshot(v.source.vault),vault_before)
        rows = json.loads((v.source.checkout/".hostbook/identity.json").read_bytes())["hosts"]
        self.assertEqual([row["name"] for row in rows],["stays"])
        record,recovered = reopen_intent(f.load,ident,code,recovery=True)
        self.assertEqual(record["checkpoint"],"completed")
        self.assertIn("source_bundle",recovered)
        with recovery_root(self.account) as root:
            for ctx in (v.source,v.destination):
                require_idle(root,self.account,ctx.store_id)
            txn = self.recovery/"transactions"/ident
            for retained in (txn/"payloads"/(record["payload_id"]+".age"),
                             txn/"recovery"/str(record["recovery_generation"])/"vault.recovery.age",
                             txn/"completion.json"):
                displaced = retained.with_name("displaced")
                retained.rename(displaced)
                for ctx in (v.source,v.destination):
                    with self.assertRaises(StoreContextError):
                        require_idle(root,self.account,ctx.store_id)
                require_idle(root,self.account,self.entries[2]["store_id"])
                displaced.rename(retained)

    def test_transfer_preserves_concurrent_private_editor_save_during_source_removal(self):
        from store_transfer import prepare_candidate
        from store_transfer_credentials import stage_credential
        from store_transfer_intent import prepare_intent, reopen_intent
        from store_transfer_apply import save_destination
        from store_transfer_publish import publish_destination
        import store_transfer_remove as removal
        f = self.transfer_case()
        v = f.vaults
        mapped = stage_credential(f.load,v.src,v.dst,v.unlock)
        pw,code = b"operator backup fixture",b"operator recovery fixture"
        updated = b"# independently saved source\nHost source-one\n  HostName 192.0.2.8\n"
        with prepare_candidate(f.load,f.plan,mapped) as candidate:
            ident = prepare_intent(f.load,f.plan,candidate,mapped,v.unlock,pw,code,lambda _:True)
            save_destination(f.load,ident,f.plan,candidate,mapped,v.unlock,pw,code)
            publish_destination(f.load,ident,f.plan,candidate,mapped,v.unlock,pw,code)
            source_file = v.source.checkout/candidate.source_path
            original = removal.rename_guarded
            def concurrent(src,name,dst,slot,**options):
                if slot == "source":
                    editor = source_file.with_suffix(".saving")
                    editor.write_bytes(updated)
                    os.replace(editor,source_file)
                return original(src,name,dst,slot,**options)
            with patch.object(removal,"rename_guarded",side_effect=concurrent),self.assertRaisesRegex(StoreContextError,"editor changes"):
                removal.finish_private_source(f.load,ident,f.plan,candidate,mapped,v.unlock,pw,code)
            self.assertEqual(source_file.read_bytes(),updated)
            self.assertEqual(f.shared.git(v.source.checkout,"rev-parse","HEAD"),f.plan.source.base)
            self.assertEqual(f.shared.git(f.shared.remote,"rev-parse","refs/heads/main"),candidate.proposed)
            record,_ = reopen_intent(f.load,ident,code,recovery=True)
            self.assertEqual(record["checkpoint"],"destination-confirmed")
            self.assertEqual(record["effects"][-1]["outcome"],"intended")

    def test_transfer_prompt_uses_saved_language_and_first_trusted_compatible_picker(self):
        import store_transfer_command as command
        settings = self.root/"preferences"/"hostbook"
        settings.mkdir(parents=True)
        (settings/"config").write_text("# lang = en\nlang = en\nlang = vi  # selected\n")
        environment = {"XDG_CONFIG_HOME":str(settings.parent),"LANG":"en_US.UTF-8"}
        self.assertEqual(command.operator_language(environment,self.root),"vi")
        self.assertEqual(command.operator_language(dict(environment,HOSTBOOK_LANG="en"),self.root),"en")
        paths = []
        for version in ("0.44.0","0.63.1"):
            folder = self.root/version
            folder.mkdir()
            program = folder/"fzf"
            program.write_text("#!/bin/sh\nprintf '%s\\n' "+version+"\n")
            program.chmod(0o700)
            paths.append((folder,None))
        def verified(account,location,programs,reason):
            self.assertEqual(account,self.account)
            self.assertEqual(programs,("fzf",))
            return location[0][0],("fixture-identity",)
        with patch.object(command,"verified_programs",side_effect=verified):
            self.assertEqual(command.compatible_picker(self.account,paths,"0.63")[0],paths[1][0])
            with self.assertRaisesRegex(StoreContextError,"approved fzf"):
                command.compatible_picker(self.account,paths[:1],"0.63")

    def test_explicit_transfer_command_runs_reviewed_move_and_emits_only_public_results(self):
        from store_transfer_command import run_transfer
        f = self.transfer_case(shared_credential=True)
        preview = f.plan.comparison()
        answers = {"source":f.vaults.unlock(f.vaults.src.store_id),"destination":f.vaults.unlock(f.vaults.dst.store_id),
                   "backup":b"transaction fixture","recovery":b"independent fixture"}
        prompts,review,results = [],[],[]
        def secret(role):
            prompts.append(role)
            return answers[role.removesuffix("-repeat")]
        def confirm(details):
            review.append(details)
            return True
        args = [f.vaults.src.store_id,"1","2","3","4",f.host,f.vaults.dst.store_id,"1","2","3","4","moved","team-login","create"]
        self.assertEqual(run_transfer(f.load,args,secret,confirm,results.append),0)
        self.assertEqual(len(review),1)
        self.assertEqual(review[0]["retained_credential_users"],preview["retained_credential_users"])
        self.assertEqual(review[0]["destination_credential"],"team-login")
        self.assertEqual([row["checkpoint"] for row in results],["comparison","prepared","completed"])
        self.assertEqual(results[-1]["transaction_id"],results[1]["transaction_id"])
        output = json.dumps(results).encode()
        for value in (*answers.values(),b"fixture mapped password",b"AGE-SECRET-KEY"):
            self.assertNotIn(value,output)
        self.assertFalse((f.vaults.source.checkout/"team.conf").exists())
        self.assertIn(f.host,f.shared.git(f.shared.remote,"show","main:.hostbook/identity.json"))
        self.assertIn("backup-repeat",prompts)
        self.assertIn("recovery-repeat",prompts)

    def test_public_transfer_dispatch_and_prod_refusal_precede_any_effect(self):
        from store_transfer_command import run_transfer
        f = self.transfer_case(prod=True)
        app = f.shared.app
        empty = subprocess.run([app,"store-transfer"],cwd=self.root/"shared b",env=f.shared.env,
                               stdin=subprocess.DEVNULL,capture_output=True,timeout=5)
        self.assertEqual(empty.returncode,65)
        self.assertIn(b"both registered selections",empty.stderr)
        v = f.vaults
        args = [v.src.store_id,"1","2","3","4",f.host,v.dst.store_id,"1","2","3","4","moved","team-login","create"]
        passwords = {"source":v.unlock(v.src.store_id),"destination":v.unlock(v.dst.store_id),
                     "backup":b"backup fixture","recovery":b"recovery fixture"}
        protected = [v.source.checkout,v.destination.checkout,f.shared.remote,self.recovery]
        before = [snapshot(path) for path in protected]
        seen = []
        def ordinary_approval(comparison):
            self.assertTrue(comparison["requires_typed_alias"])
            return True
        with self.assertRaisesRegex(StoreContextError,"explicit transfer confirmation"):
            run_transfer(f.load,args,lambda role:passwords[role.removesuffix("-repeat")],ordinary_approval,seen.append)
        self.assertEqual([snapshot(path) for path in protected],before)
        self.assertEqual([row["checkpoint"] for row in seen],["comparison"])

    def test_transfer_mapping_requires_explicit_verified_reuse_and_rechecks_saved_bytes(self):
        from store_transfer_credentials import stage_credential
        f = self.transfer_vaults()
        original = b"synthetic shared use"
        for context in (f.source, f.destination):
            (context.vault/"login.meta").write_bytes(b"type=password\n")
        (f.source.vault/"login.age").write_bytes(f.seal(f.source, original))
        destination = f.destination.vault/"login.age"
        destination.write_bytes(f.seal(f.destination, b"different fixture"))
        before = snapshot(self.root)
        for reuse, error in ((False,"occupied"), (True,"different")):
            with self.assertRaisesRegex(StoreContextError, error):
                stage_credential(lambda: self.account, f.src, f.dst, f.unlock, reuse_existing=reuse)
        self.assertEqual(before, snapshot(self.root))
        destination.write_bytes(f.seal(f.destination, original))
        existing = destination.read_bytes()
        staged = stage_credential(lambda: self.account, f.src, f.dst, f.unlock, reuse_existing=True)
        self.assertTrue(staged.reused)
        self.assertEqual(staged.ciphertext, existing)
        staged.verify_saved(lambda: self.account, f.unlock)
        destination.write_bytes(f.seal(f.destination, original))
        with self.assertRaisesRegex(StoreContextError, "changed"):
            staged.verify_saved(lambda: self.account, f.unlock)

    def test_transfer_protected_staging_cleans_on_abort_and_preserves_shared_source(self):
        from store_transfer_credentials import stage_credential
        from store_read import managed_credentials
        f = self.transfer_vaults()
        self.entries[1]["effective_mode"] = "two-way"
        self.save_registry()
        (f.source.vault/"login.meta").write_bytes(b"type=password\n")
        (f.source.vault/"login.age").write_bytes(f.seal(f.source, b"shared fixture"))
        config = b"# secret: login\nHost first second\n  HostName shared.invalid\n"
        (f.source.checkout/"project.conf").write_bytes(config)
        sidecar = f.source.checkout/".hostbook/identity.json"
        records = json.loads(sidecar.read_text())
        records["sources"] = [{"path":"project.conf", "sha256":hashlib.sha256(config).hexdigest()}]
        records["hosts"] = [{"host_id":new_id(), "name":name, "bindings":[
            {"path":"project.conf", "host_ordinal":1, "token_ordinal":n}]}
            for n, name in enumerate(("first","second"), 1)]
        sidecar.write_text(json.dumps(records))
        self.assertEqual(managed_credentials({"project.conf":config}, records["hosts"])[1],
                         {"first":"login", "second":"login"})
        env = {"PATH":"/usr/bin:/bin","HOME":str(self.root),"GIT_CONFIG_NOSYSTEM":"1","GIT_CONFIG_GLOBAL":"/dev/null"}
        def git(*args):
            return subprocess.run(["/usr/bin/git",*args], cwd=f.source.checkout, env=env,
                                  check=True, capture_output=True).stdout
        git("init", "-q")
        unrelated = f.source.checkout/"unrelated"
        unrelated.write_bytes(b"staged work")
        git("add","--","unrelated")
        unrelated.write_bytes(b"unstaged work")
        before = snapshot(self.root)
        load = lambda: self.account
        staged = stage_credential(load, f.src, f.dst, f.unlock)
        with self.assertRaisesRegex(RuntimeError, "cancel"):
            with staged.private_files(load) as folder:
                self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
                self.assertEqual((folder/"login.age").stat().st_mode & 0o777, 0o600)
                self.assertEqual((folder/"login.age").read_bytes(), staged.ciphertext)
                raise RuntimeError("cancel")
        self.assertFalse(folder.exists())
        self.assertEqual(before, snapshot(self.root))
        self.assertEqual(git("show",":unrelated"), b"staged work")
        with staged.private_files(load) as folder:
            for path in folder.iterdir():
                shutil.copyfile(path, f.destination.vault/path.name)
        staged.verify_saved(load, f.unlock)
        envelope = f.source.vault/"vault.recovery.age"
        envelope.write_bytes(envelope.read_bytes() + b"\n")
        with self.assertRaisesRegex(StoreContextError, "changed"):
            staged.verify_saved(load, f.unlock)

    def test_transfer_rejects_copied_vault_identity_before_unlock(self):
        from store_transfer_credentials import stage_credential
        f = self.transfer_vaults()
        (f.source.vault/"login.meta").write_bytes(b"type=password\n")
        (f.source.vault/"login.age").write_bytes(f.seal(f.source, b"copied identity fixture"))
        for name in ("vault.pub", "vault.key.age", "vault.recovery.age"):
            shutil.copyfile(f.source.vault/name, f.destination.vault/name)
        before = snapshot(self.root)
        with self.assertRaisesRegex(StoreContextError, "share an identity"):
            stage_credential(lambda: self.account, f.src, f.dst, lambda _: self.fail("unexpected unlock"))
        self.assertEqual(before, snapshot(self.root))

    def test_transfer_requires_one_native_recipient_at_each_end_before_unlock(self):
        from store_transfer_credentials import stage_credential
        f = self.transfer_vaults()
        for context in (f.source, f.destination):
            (context.vault/"login.meta").write_bytes(b"type=password\n")
            (context.vault/"login.age").write_bytes(f.seal(context, b"same fixture"))
        for context, other in ((f.source,f.destination), (f.destination,f.source)):
            pub = context.vault/"vault.pub"
            saved = pub.read_bytes()
            pub.write_bytes(saved + (other.vault/"vault.pub").read_bytes())
            try:
                for reuse in (True, False):
                    dst = f.dst if reuse else replace(f.dst, name="free")
                    with self.assertRaisesRegex(StoreContextError, "single native vault recipient"):
                        stage_credential(lambda: self.account, f.src, dst, lambda _: self.fail("unexpected unlock"),
                                         reuse_existing=reuse)
            finally:
                pub.write_bytes(saved)

    def test_transfer_verifies_key_metadata_and_private_public_pair(self):
        from store_transfer_credentials import stage_credential
        from store_vault import VaultUnlockCache
        f = self.transfer_vaults()
        env = {"PATH": "/usr/bin:/bin", "HOME":str(self.root)}
        key, wrong = self.root/"key", self.root/"wrong"
        for path in (key, wrong):
            subprocess.run(["/usr/bin/ssh-keygen","-q","-t","ed25519","-N","","-C","fixture","-f",path],
                           env=env, check=True, capture_output=True)
        fingerprint = subprocess.check_output(["/usr/bin/ssh-keygen","-lf",key.with_suffix(".pub")],
                                              env=env).split()[1]
        meta = b"type=key\nfp=" + fingerprint + b"\nkeytype=ed25519\ncreated=2026-02-01\n"
        public = key.with_suffix(".pub").read_bytes()
        (f.source.vault/"login.meta").write_bytes(meta)
        (f.source.vault/"login.age").write_bytes(f.seal(f.source,key.read_bytes()))
        (f.source.vault/"login.pub").write_bytes(public)
        load = lambda: self.account
        dst = replace(f.dst, name="renamed")
        before = snapshot(self.root)
        staged = stage_credential(load, f.src, dst, f.unlock)
        self.assertEqual(staged.public_key, public)
        self.assertTrue(VaultUnlockCache(f.tools).open_secret(f.identities[dst.store_id], staged.ciphertext)
                        == key.read_bytes())
        self.assertEqual(before, snapshot(self.root))
        with staged.private_files(load) as folder:
            self.assertEqual(sorted(x.name for x in folder.iterdir()), ["renamed.age","renamed.meta","renamed.pub"])
            for file in folder.iterdir():
                shutil.copyfile(file, f.destination.vault/file.name)
        staged.verify_saved(load, f.unlock)
        self.assertTrue(stage_credential(load, f.src, dst, f.unlock, reuse_existing=True).reused)
        (f.source.vault/"login.meta").write_bytes(meta.replace(b"ed25519", b"rsa"))
        with self.assertRaisesRegex(StoreContextError, "metadata"):
            stage_credential(load, f.src, replace(dst,name="free"), f.unlock)
        (f.source.vault/"login.meta").write_bytes(b"type=key\n")
        (f.source.vault/"login.pub").write_bytes(wrong.with_suffix(".pub").read_bytes())
        remaining = snapshot(self.root)
        with self.assertRaisesRegex(StoreContextError, "key do not match"):
            stage_credential(load, f.src, replace(dst,name="free"), f.unlock)
        self.assertEqual(remaining, snapshot(self.root))

    def test_transfer_failures_and_changed_inputs_leave_store_files_and_work_intact(self):
        from store_transfer_credentials import stage_credential, durable_file
        from store_vault import VaultUnlockCache
        f = self.transfer_vaults()
        load = lambda: self.account
        (f.source.vault/"login.meta").write_bytes(b"type=password\n")
        (f.source.vault/"login.age").write_bytes(f.seal(f.source, b"failure fixture"))
        before = snapshot(self.root)
        for failure in (None, b"incorrect"):
            with self.assertRaises(StoreContextError):
                stage_credential(load, f.src, f.dst,
                                 lambda store: failure if store == f.dst.store_id else f.unlock(store))
        real_seal = VaultUnlockCache.seal_secret
        def fail_seal(cache, recipient, value):
            if value:
                raise StoreContextError("encryption failed")
            return real_seal(cache, recipient, value)
        with patch.object(VaultUnlockCache, "seal_secret", fail_seal):
            with self.assertRaisesRegex(StoreContextError, "encryption failed"):
                stage_credential(load, f.src, f.dst, f.unlock)
        staged = stage_credential(load, f.src, f.dst, f.unlock)
        def fail_after_one(directory, name, data):
            if name.endswith(".age"):
                raise OSError("stage full")
            durable_file(directory, name, data)
        with patch("store_transfer_credentials.durable_file", side_effect=fail_after_one):
            with self.assertRaisesRegex(OSError, "stage full"):
                with staged.private_files(load):
                    self.fail("partial staging exposed")
        self.assertEqual(before, snapshot(self.root))
        for index in (0, 1):
            self.entries[index]["policy_generation"] += 1
            self.save_registry()
            with self.assertRaisesRegex(StoreContextError, "stale store generations"):
                with staged.private_files(load):
                    self.fail("stale stage exposed")
            self.entries[index]["policy_generation"] -= 1
            self.save_registry()
        def edit_during_unlock(store):
            if store == f.dst.store_id:
                (f.destination.vault/"login.meta").write_bytes(b"type=password\n")
                (f.destination.vault/"login.age").write_bytes(f.seal(f.destination, b"independent edit"))
            return f.unlock(store)
        with self.assertRaisesRegex(StoreContextError, "changed"):
            stage_credential(load, f.src, f.dst, edit_during_unlock)
        self.assertTrue((f.destination.vault/"login.age").exists())
        for path, old in before.items():
            if path != self.registry.relative_to(self.root):
                self.assertEqual(snapshot(self.root)[path], old)

    def test_transfer_verifies_existing_unencrypted_pem_key_records(self):
        from store_transfer_credentials import stage_credential
        from store_vault import VaultUnlockCache
        f = self.transfer_vaults()
        env = {"PATH":"/usr/bin:/bin", "HOME":str(self.root)}
        for keytype, encoding in (("rsa","PKCS8"),("ecdsa","PKCS8"),("rsa","PEM"),("ecdsa","PEM")):
            key = self.root/(keytype + encoding)
            subprocess.run(["/usr/bin/ssh-keygen","-q","-t",keytype,"-m",encoding,"-N","","-C","fixture","-f",key],
                           env=env, stdin=subprocess.DEVNULL, capture_output=True, check=True)
            plain = key.read_bytes()
            key.unlink()
            pub = key.with_suffix(".pub").read_bytes()
            if encoding == "PKCS8":
                self.assertEqual(plain.splitlines()[0], b"-----BEGIN PRIVATE KEY-----")
            else:
                self.assertIn(b" PRIVATE KEY-----", plain.splitlines()[0])
            (f.source.vault/"login.pub").write_bytes(pub)
            (f.source.vault/"login.meta").write_bytes(b"type=key\n")
            (f.source.vault/"login.age").write_bytes(f.seal(f.source,plain))
            staged = stage_credential(lambda: self.account, f.src, f.dst, f.unlock)
            self.assertTrue(VaultUnlockCache(f.tools).open_secret(f.identities[f.dst.store_id],staged.ciphertext)
                            == plain)
            header, body = plain.split(b"\n",1)
            invalid = header + b"\nProc-Type: 4,ENCRYPTED\n\n" + body
            (f.source.vault/"login.age").write_bytes(f.seal(f.source, invalid))
            with self.assertRaisesRegex(StoreContextError, "private key"):
                stage_credential(lambda: self.account, f.src, f.dst, f.unlock)

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
