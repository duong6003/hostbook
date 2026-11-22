#!/usr/bin/env python3
"""Exercise an encrypted restore with disposable Git and vault data only."""
import errno
import hashlib
import io
import os
from pathlib import Path
import platform
import stat
import subprocess
import tarfile
import tempfile
import unittest


SECRET = b"synthetic recovery check\n"


class RestoreRejected(Exception):
    pass


def capture_tree(root):
    entries = {}
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        key = path.relative_to(root).as_posix()
        if stat.S_ISDIR(mode):
            entries[key] = ("directory", stat.S_IMODE(mode), None)
        elif stat.S_ISREG(mode):
            entries[key] = ("file", stat.S_IMODE(mode), hashlib.sha256(path.read_bytes()).digest())
        else:
            raise RestoreRejected("fixture accepts only regular files and directories")
    return entries


class RestoreFixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hostbook-restore-")
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "original"
        self.restored = self.root / "verified-copy"
        self.checkout = self.source / "checkout"
        self.external = self.root / "external.d" / "team.conf"
        self.backup_key = self.root / "backup-identity"
        self.recovery_key = self.root / "vault-recovery-identity"
        self.original_vault_key = self.root / "original-vault-identity"
        self.env = {
            "PATH": os.environ["PATH"], "HOME": str(self.root / "home"),
            "XDG_CONFIG_HOME": str(self.root / "xdg"),
            "HOSTBOOK_DIR": str(self.checkout), "HOSTBOOK_STATE": str(self.source / "state"),
            "HOSTBOOK_SSH_CONFIG": str(self.source / "config" / "ssh"), "LC_ALL": "C",
            "HOSTBOOK_KEYCHAIN": "file:" + str(self.root / "keychain"),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_OPTIONAL_LOCKS": "0",
            "GIT_AUTHOR_NAME": "fixture", "GIT_COMMITTER_NAME": "fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.test", "GIT_COMMITTER_EMAIL": "fixture@example.test",
        }
        for directory in (self.checkout / "secrets", self.source / "state", self.source / "config" / "conf.d",
                          self.root / "home", self.root / "xdg"):
            directory.mkdir(parents=True, mode=0o700)

        self.backup_recipient = self.make_identity(self.backup_key)
        recovery_recipient = self.make_identity(self.recovery_key)
        vault_recipient = self.make_identity(self.original_vault_key)
        vault = self.checkout / "secrets"
        (vault / "vault.pub").write_text(vault_recipient + "\n")
        (vault / "vault.key.age").write_bytes(self.encrypt(recovery_recipient, self.original_vault_key.read_bytes()))
        (vault / "credential.age").write_bytes(self.encrypt(vault_recipient, SECRET))
        self.original_vault_key.unlink()

        self.git(self.source, "init", "-q", "--template=")
        self.git(self.source, "symbolic-ref", "HEAD", "refs/heads/main")
        (self.checkout / ".gitignore").write_text("ignored.local\n")
        self.write_host("192.0.2.9")
        self.git(self.source, "add", ".")
        self.git(self.source, "commit", "-qm", "Import fixture")
        self.git(self.source, "tag", "fixture-initial")
        self.write_host("192.0.2.10")
        self.git(self.source, "add", "--", "hosts.conf")
        self.git(self.source, "commit", "-qm", "Accept host revision")
        self.git(self.source, "remote", "add", "origin", "ssh://fixture.example.test/team/hosts.git")
        self.git(self.source, "update-ref", "refs/remotes/origin/main", "HEAD")

        self.write_host("192.0.2.11")
        self.git(self.source, "add", "--", "hosts.conf")
        self.write_host("192.0.2.12")
        (self.checkout / "untracked.conf").write_text("Host draft\n")
        ignored = self.checkout / "ignored.local"
        ignored.write_text("retained fixture data\n")
        ignored.chmod(0o640)
        (self.source / "state" / "status").write_text("pending\n")
        self.external.parent.mkdir(mode=0o700)
        self.external.write_text("Host dependency-host\n  HostName 192.0.2.23\n  Port 2222\n")
        self.external.chmod(0o600)
        (self.source / "config" / "ssh").write_text(
            "CanonicalizeHostname no\nIdentityAgent none\nIdentityFile none\n"
            "ControlPath none\nGlobalKnownHostsFile none\n"
            f'UserKnownHostsFile "{self.root / "known_hosts"}"\n'
            f'# Include "{self.external}"\nInclude "{self.external}"\n'
        )
        (self.source / "config" / "conf.d" / "team.conf").write_text("Host *\n  ConnectTimeout 4\n")
        self.external_expected = self.read_external()
        (self.source / "config" / "hostbook.conf").write_text("language = vi\n")
        self.expected = capture_tree(self.source)

    def close(self):
        self.temp.cleanup()

    def command(self, *args, data=None, environment=None):
        stdin = subprocess.DEVNULL if data is None else None
        return subprocess.run(args, env=self.env if environment is None else environment,
                              cwd=self.root, stdin=stdin, input=data,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=True).stdout

    def git(self, root, *args, environment=None):
        return self.command("git", "-C", str(root / "checkout"), *args, environment=environment)

    def make_identity(self, path):
        self.command("age-keygen", "-o", str(path))
        return self.command("age-keygen", "-y", str(path)).decode("ascii").strip()

    def encrypt(self, recipient, contents):
        return self.command("age", "-r", recipient, data=contents)

    def write_host(self, address):
        (self.checkout / "hosts.conf").write_text(f"Host db\n  HostName {address}\n")

    def observe_git(self, root):
        with tempfile.TemporaryDirectory(prefix="inspect-", dir=self.root) as staging:
            # Git can refresh index stat entries during diff after files move.
            index = Path(staging) / "index"
            index.write_bytes((root / "checkout" / ".git" / "index").read_bytes())
            environment = dict(self.env, GIT_INDEX_FILE=str(index))

            def read(*args):
                return self.git(root, *args, environment=environment)

            return {
                "head": read("symbolic-ref", "HEAD"),
                "history": read("rev-list", "--all"),
                "refs": read("show-ref"),
                "remote": read("remote", "get-url", "origin"),
                "head_config": read("show", "HEAD:hosts.conf"),
                "index_config": read("show", ":hosts.conf"),
                "worktree_config": (root / "checkout" / "hosts.conf").read_bytes(),
                "staged": read("diff", "--cached", "--binary"),
                "unstaged": read("diff", "--binary"),
                "status": read("status", "--porcelain", "--ignored"),
            }

    def read_vault(self, root):
        vault = root / "checkout" / "secrets"
        identity = self.command("age", "-d", "-i", str(self.recovery_key), str(vault / "vault.key.age"))
        return self.command("age", "-d", "-i", "-", str(vault / "credential.age"), data=identity)

    def read_external(self):
        label = self.external.relative_to(self.root)
        try:
            dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            parent = os.open(self.root, dir_flags)
            try:
                directory = os.open(self.external.parent.name, dir_flags, dir_fd=parent)
                try:
                    fd = os.open(self.external.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
                    with os.fdopen(fd, "rb") as stream:
                        info = os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode):
                            raise RestoreRejected(f"{label}: expected a regular file")
                        contents = stream.read(4097)
                        if len(contents) > 4096:
                            raise RestoreRejected(f"{label}: fixture dependency is too large")
                    parent_info = os.fstat(directory)
                finally:
                    os.close(directory)
            finally:
                os.close(parent)
        except OSError as error:
            reason = "missing" if error.errno == errno.ENOENT else "cannot safely open"
            raise RestoreRejected(f"{label}: {reason}") from error
        return (parent_info.st_dev, parent_info.st_ino, info.st_dev, info.st_ino,
                info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), contents)

    def verify_external(self, candidate):
        label = self.external.relative_to(self.root)
        current = self.read_external()
        if current != self.external_expected:
            raise RestoreRejected(f"{label}: approved dependency changed")
        with tempfile.TemporaryDirectory(prefix="ssh-probe-", dir=self.root) as staging:
            probe = Path(staging)
            copy = probe / "checked-include"
            copy.write_bytes(current[-1])
            config = probe / "ssh"
            lines = (candidate / "config" / "ssh").read_text().splitlines(keepends=True)
            reference = f'Include "{self.external}"\n'
            if lines.count(reference) != 1:
                raise RestoreRejected(f"{label}: expected one direct Include")
            lines[lines.index(reference)] = f'Include "{copy}"\n'
            config.write_text("".join(lines))
            try:
                effective = self.command("ssh", "-G", "-F", str(config), "dependency-host").splitlines()
            except (OSError, subprocess.SubprocessError) as error:
                raise RestoreRejected(f"{label}: SSH settings cannot be verified") from error
            if b"hostname 192.0.2.23" not in effective or b"port 2222" not in effective:
                raise RestoreRejected(f"{label}: unexpected effective SSH settings")
        if self.read_external() != current:
            raise RestoreRejected(f"{label}: changed during verification")

    def backup(self, omit=None):
        wrapped = io.BytesIO()
        with tarfile.open(fileobj=wrapped, mode="w") as archive:
            for key, (kind, mode, _) in sorted(self.expected.items()):
                if key == omit:
                    continue
                entry = tarfile.TarInfo(key)
                entry.mode = mode
                if kind == "directory":
                    entry.type = tarfile.DIRTYPE
                    archive.addfile(entry)
                else:
                    data = (self.source / key).read_bytes()
                    entry.size = len(data)
                    archive.addfile(entry, io.BytesIO(data))
        payload = self.encrypt(self.backup_recipient, wrapped.getvalue())
        destination = self.root / "backup.age"
        destination.touch(mode=0o600, exist_ok=False)
        destination.write_bytes(payload)
        return destination

    def restore(self, payload):
        if self.restored.exists() or self.restored.is_symlink():
            raise RestoreRejected("restore destination already exists")
        try:
            contents = self.command("age", "-d", "-i", str(self.backup_key), str(payload))
        except subprocess.SubprocessError as exc:
            raise RestoreRejected("backup cannot be decrypted") from exc

        with tempfile.TemporaryDirectory(prefix="restore-stage-", dir=self.root) as staging:
            candidate = Path(staging) / "candidate"
            candidate.mkdir(mode=0o700)
            with tarfile.open(fileobj=io.BytesIO(contents), mode="r:") as archive:
                members = archive.getmembers()
                if len(members) != len(self.expected) or {m.name for m in members} != self.expected.keys():
                    raise RestoreRejected("archive inventory does not match the original snapshot")
                for member in sorted(members, key=lambda entry: (entry.name.count("/"), entry.name)):
                    kind, mode, _ = self.expected[member.name]
                    target = candidate / member.name
                    if member.mode != mode:
                        raise RestoreRejected("unexpected archive entry mode")
                    if kind == "directory" and member.isdir():
                        target.mkdir()
                    elif kind == "file" and member.type == tarfile.REGTYPE:
                        with archive.extractfile(member) as stream:
                            target.write_bytes(stream.read())
                    else:
                        raise RestoreRejected("unexpected archive entry type")
                    target.chmod(mode)
            if capture_tree(candidate) != self.expected:
                raise RestoreRejected("restored bytes do not match the original snapshot")
            try:
                if self.read_vault(candidate) != SECRET:
                    raise RestoreRejected("restored vault data does not match the fixture")
            except subprocess.SubprocessError as exc:
                raise RestoreRejected("restored vault cannot be decrypted") from exc
            self.verify_external(candidate)
            candidate.rename(self.restored)


class RestoreTest(unittest.TestCase):
    def setUp(self):
        self.fixture = RestoreFixture()
        self.addCleanup(self.fixture.close)

    def test_complete_backup_restores_git_layers_settings_and_vault(self):
        f = self.fixture
        before = f.observe_git(f.source)
        self.assertIn(b"192.0.2.10", before["head_config"])
        self.assertIn(b"192.0.2.11", before["index_config"])
        self.assertIn(b"192.0.2.12", before["worktree_config"])
        self.assertIn(b"?? untracked.conf", before["status"])
        self.assertIn(b"!! ignored.local", before["status"])
        self.assertFalse(f.original_vault_key.exists())

        backup = f.backup()
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        f.restore(backup)
        self.assertEqual(f.observe_git(f.restored), before)
        self.assertEqual(f.read_vault(f.restored), b"synthetic recovery check\n")
        self.assertEqual(capture_tree(f.restored), f.expected)
        self.assertEqual(capture_tree(f.source), f.expected)

    def test_incomplete_encrypted_backup_stops_before_promotion(self):
        f = self.fixture
        with self.assertRaisesRegex(RestoreRejected, "archive inventory"):
            f.restore(f.backup(omit="checkout/.git/index"))
        self.assertFalse(f.restored.exists())
        self.assertEqual(capture_tree(f.source), f.expected)

    def test_damaged_ciphertext_stops_before_promotion(self):
        f = self.fixture
        backup = f.backup()
        damaged = bytearray(backup.read_bytes())
        damaged[-20] ^= 1
        backup.write_bytes(damaged)
        with self.assertRaisesRegex(RestoreRejected, "cannot be decrypted"):
            f.restore(backup)
        self.assertFalse(f.restored.exists())
        self.assertEqual(capture_tree(f.source), f.expected)

    def test_vault_without_recovery_access_stops_before_promotion(self):
        f = self.fixture
        backup = f.backup()
        f.recovery_key.rename(f.root / "unavailable-vault-identity")
        with self.assertRaisesRegex(RestoreRejected, "vault cannot be decrypted"):
            f.restore(backup)
        self.assertFalse(f.restored.exists())
        self.assertEqual(capture_tree(f.source), f.expected)


    def assert_external_blocked(self, backup, reason):
        f = self.fixture
        with self.assertRaisesRegex(RestoreRejected, rf"external[.]d/team[.]conf: {reason}"):
            f.restore(backup)
        self.assertFalse(f.restored.exists())
        self.assertEqual(capture_tree(f.source), f.expected)

    def test_missing_external_include_blocks_promotion(self):
        f = self.fixture
        backup = f.backup()
        f.external.unlink()
        fallback = f.command("ssh", "-G", "-F", f.env["HOSTBOOK_SSH_CONFIG"], "dependency-host")
        self.assertIn(b"hostname dependency-host", fallback.splitlines())
        self.assert_external_blocked(backup, "missing")

    def test_changed_external_include_blocks_promotion(self):
        f = self.fixture
        backup = f.backup()
        f.external.write_text("Host dependency-host\n  HostName 192.0.2.24\n")
        self.assert_external_blocked(backup, "approved dependency changed")

    def test_redirected_external_include_blocks_promotion(self):
        f = self.fixture
        backup = f.backup()
        alternate = f.root / "other-config"
        f.external.rename(alternate)
        f.external.symlink_to(alternate)
        self.assert_external_blocked(backup, "cannot safely open")


if __name__ == "__main__":
    print(f"Platform: {platform.system()} {platform.release()} ({platform.machine()})", flush=True)
    unittest.main(verbosity=2)
