"""Process-local unlock cache for registered stores.

Callers pass the approved account resolver and trusted installed age tool
directory. Passphrases and decrypted identities stay out of argv, environment,
logs and files. This module is loaded only by an explicit unlock consumer.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import base64
import json
import os
from pathlib import Path
import re
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


def receive_password_records(account, changes, before, incoming, blob, read_passphrase=None):
    """Inspect encrypted records from the approved source without unlocking the existing vault."""
    from store_login import age_installations, verified_age_tools, verified_programs
    from store_read import SECRET_NAME

    anchors = ("secrets/vault.pub", "secrets/vault.key.age", "secrets/vault.recovery.age")
    require(all(path in before and before[path] == incoming.get(path) for path in anchors),
            "receive requires the existing unchanged vault identity and both envelopes")
    names = set()
    for path in changes:
        name, dot, suffix = path.removeprefix("secrets/").rpartition(".")
        require(dot and SECRET_NAME.fullmatch(name) and name != "vault" and not name.startswith("vault.")
                and suffix in ("age", "meta") and path in incoming, "unsupported received credential change")
        names.add(name)

    tools, _ = verified_programs(account, age_installations(), ("age-inspect","age"),
                                 "encrypted receive requires trusted age and age-inspect")
    def inspect_tool(program, args, data):
        try:
            return subprocess.run([str(tools/program), *args], input=data, env={"PATH":str(tools)},
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            raise StoreContextError("received vault material failed inspection") from None

    def archive(data, kind):
        lines = data.split(b"\n")
        require(len(lines) >= 4 and lines[0] == b"-----BEGIN AGE ENCRYPTED FILE-----"
                and lines[-2:] == [b"-----END AGE ENCRYPTED FILE-----",b""], "expected one armored age archive")
        # age-inspect reads the age header. Bound the entire input to the single reviewed armor block.
        require(all(line and len(line) <= 64 for line in lines[1:-2]), "invalid age armor")
        base64.b64decode(b"".join(lines[1:-2]), validate=True)
        result = json.loads(inspect_tool("age-inspect",["--json"],data))
        require(result.get("version") == "age-encryption.org/v1" and result.get("armor") is True
                and result.get("stanza_types") == [kind] and result.get("sizes",{}).get("min_payload",0)>0,
                "unsupported encrypted archive")

    pub = blob(before[anchors[0]]).rstrip(b"\n")
    require(re.fullmatch(rb"age1[ac-hj-np-z02-9]{58}",pub),"unsupported existing vault recipient")
    inspect_tool("age",["-e","-r",pub.decode()],b"")
    for path in anchors[1:]:
        archive(blob(before[path]), "scrypt")
    verify = []
    for name in names:
        prefix = "secrets/"+name
        encrypted, metadata = prefix+".age", prefix+".meta"
        for files in (before,incoming):
            if files is before and encrypted not in files and metadata not in files:
                continue
            require(encrypted in files and metadata in files and prefix+".pub" not in files,
                    "receive requires a complete password credential")
            meta = blob(files[metadata])
            require(re.fullmatch(rb"type=password\n(?:created=[0-9]{4}-[0-9]{2}-[0-9]{2}\n)?",meta),
                    "unsupported password credential metadata")
            archive(blob(files[encrypted]),"X25519")
        if before.get(encrypted) != incoming[encrypted] and (encrypted in before or read_passphrase is not None):
            verify.append(incoming[encrypted])
    if verify:
        require(read_passphrase is not None, "unlock the existing vault to receive replacement ciphertext")
        tool_dir, evidence = verified_age_tools(account)
        passphrase = read_passphrase()
        require(verified_age_tools(account) == (tool_dir,evidence), "vault tools changed during verification")
        vault = VaultUnlockCache(tool_dir)
        vault.verify_passwords(blob(before[anchors[1]]),pub,passphrase,(blob(oid) for oid in verify))


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

    def _passphrase_operation(self, args, value, passphrase):
        require(isinstance(passphrase, bytes) and passphrase and b"\r" not in passphrase
                and b"\n" not in passphrase and b"\0" not in passphrase,
                "vault passphrase must be a nonempty line")
        with self._secret_pipe(passphrase) as reader:
            return self._run("age", args, value, pass_fd=reader)

    def _decrypt(self, envelope, passphrase):
        return self._passphrase_operation(("-d", "-j", "batchpass"), envelope, passphrase)

    def generate_identity(self):
        identity = self._run("age-keygen", (), b"")
        return identity, self._run("age-keygen", ("-y",), identity)

    def wrap_identity(self, identity, passphrase):
        return self._passphrase_operation(("-a", "-e", "-j", "batchpass"), identity, passphrase)

    def open_identity(self, envelope, passphrase, recipient):
        identity = self._decrypt(envelope, passphrase)
        emitted = self._run("age-keygen", ("-y",), identity)
        require(re.fullmatch(rb"age1[ac-hj-np-z02-9]{58}\n", recipient) and emitted == recipient,
                "recovery vault identity mismatch")
        return identity

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

    def verify_passwords(self, envelope, recipient, passphrase, ciphertexts):
        identity = self._decrypt(envelope, passphrase)
        require(self._run("age-keygen",("-y",),identity).strip() == recipient,
                "accepted vault envelope does not match its recipient")
        for ciphertext in ciphertexts:
            require(self.open_secret(identity, ciphertext), "received password is empty")

    def open_secret(self, identity, ciphertext):
        with self._secret_pipe(identity) as reader:
            return self._run("age", ("-d", "-i", f"/dev/fd/{reader}"), ciphertext, identity_fd=reader)

    def seal_secret(self, recipient, value):
        require(re.fullmatch(rb"age1[ac-hj-np-z02-9]{58}", recipient), "unsupported vault recipient")
        return self._run("age", ("-e", "-a", "-r", recipient.decode("ascii")), value)

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
