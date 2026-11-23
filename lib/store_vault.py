"""Process-local unlock cache for registered stores.

Callers pass the approved account resolver and trusted installed age tool
directory. Passphrases and decrypted identities stay out of argv, environment,
logs and files. This module is loaded only by an explicit unlock consumer.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess

from store_context import (JSON_LIMIT, StoreContextError, bound_directory,
                           open_directory, path_parts, read_file, require,
                           resolved_store, valid_id)


@dataclass(frozen=True, repr=False)
class VaultMaterial:
    evidence: tuple
    recipient: bytes
    password_envelope: bytes
    recovery_envelope: bytes


def file_evidence(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def read_vault(root_fd, account, binding):
    parts = path_parts(account.root.path, binding.root.path)
    with bound_directory(root_fd, parts, binding.root, "checkout binding mismatch") as checkout_fd:
        with open_directory(checkout_fd, ("secrets",)) as vault_fd:
            directory = os.fstat(vault_fd)
            evidence = [(directory.st_dev, directory.st_ino)]

            def verify(_fd, info):
                require(info.st_nlink == 1, "linked vault material is not allowed")
                evidence.append(file_evidence(info))

            recipient = read_file(vault_fd, "vault.pub", 4096, verify)
            password = read_file(vault_fd, "vault.key.age", JSON_LIMIT, verify)
            recovery = read_file(vault_fd, "vault.recovery.age", JSON_LIMIT, verify)
    require(recipient and password and recovery, "incomplete store vault")
    return VaultMaterial(tuple(evidence), recipient, password, recovery)


class VaultUnlockCache:
    def __init__(self, tool_dir):
        self._tools = Path(tool_dir)
        require(self._tools.is_absolute(), "age tool directory must be absolute")
        self._entries = {}

    def _run(self, name, args, data, pass_fd=None, identity_fd=None):
        env = {"PATH": str(self._tools)}
        inherited_fds = ()
        if pass_fd is not None:
            env["AGE_PASSPHRASE_FD"] = str(pass_fd)
            inherited_fds = (pass_fd,)
        elif identity_fd is not None:
            inherited_fds = (identity_fd,)
        try:
            result = subprocess.run([str(self._tools / name), *args], input=data, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    pass_fds=inherited_fds, timeout=30, check=True)
        except (OSError, subprocess.SubprocessError):
            raise StoreContextError("vault unlock failed") from None
        return result.stdout

    def _decrypt(self, envelope, passphrase):
        require(isinstance(passphrase, bytes) and passphrase and b"\r" not in passphrase
                and b"\n" not in passphrase and b"\0" not in passphrase,
                "vault passphrase must be a nonempty line")
        with self._secret_pipe(passphrase) as reader:
            return self._run("age", ("-d", "-j", "batchpass"), envelope, pass_fd=reader)

    @staticmethod
    @contextmanager
    def _secret_pipe(value):
        reader, writer = os.pipe()
        try:
            with os.fdopen(writer, "wb", buffering=0) as pipe:
                require(value and len(value) <= os.fpathconf(pipe.fileno(), "PC_PIPE_BUF"),
                        "vault input exceeds pipe limit")
                require(pipe.write(value) == len(value), "vault pipe write failed")
            yield reader
        finally:
            os.close(reader)

    def open_secret(self, identity, ciphertext):
        with self._secret_pipe(identity) as reader:
            return self._run("age", ("-d", "-i", f"/dev/fd/{reader}"), ciphertext, identity_fd=reader)

    def _current(self, load_account, store_id, expected):
        require(valid_id(store_id), "invalid store ID")
        account = load_account()
        key = (os.geteuid(), account.device_id, account.root, store_id)
        with resolved_store(account, store_id, expected) as (_, binding, root_fd, _):
            try:
                material = read_vault(root_fd, account, binding)
            except (OSError, StoreContextError):
                self._entries.pop(key, None)
                raise
        cached = self._entries.get(key)
        if cached is not None and cached[0] != material:
            self._entries.pop(key, None)
        return key, material

    def get(self, load_account, store_id, expected):
        """Return a validated identity to the in-process caller, or None while locked."""
        key, material = self._current(load_account, store_id, expected)
        cached = self._entries.get(key)
        return cached[1] if cached is not None and cached[0] == material else None

    def unlock(self, load_account, store_id, expected, passphrase):
        key, material = self._current(load_account, store_id, expected)
        identity = self._decrypt(material.password_envelope, passphrase)
        recipient = self._run("age-keygen", ("-y",), identity)
        require(identity and recipient.startswith(b"age1") and recipient.strip() == material.recipient.strip(),
                "vault identity mismatch")
        require(self._current(load_account, store_id, expected) == (key, material),
                "vault generation changed during unlock")
        self._entries[key] = (material, identity)
