"""Prepare one transfer mapping without writing to a registered store or its Git index."""
from contextlib import contextmanager
from dataclasses import dataclass
import base64
import binascii
import hashlib
from hmac import compare_digest
import os
from pathlib import Path
import re
import tempfile
from typing import Optional

from store_checkout import durable_file
from store_context import (Account, DIRECTORY_FLAGS, Generations, JSON_LIMIT, StoreContext, StoreContextError, bound_directory,
                           open_directory, path_parts, read_file, require, require_private_directory,
                           resolved_store)
from store_login import agent_keys, ssh_agent, verified_age_tools
from store_read import SECRET_NAME
from store_vault import VaultMaterial, VaultUnlockCache, file_evidence, read_vault


@dataclass(frozen=True)
class CredentialRef:
    store_id: str
    generations: Generations
    name: str


@dataclass(frozen=True, repr=False)
class CredentialRecord:
    metadata: bytes
    ciphertext: bytes
    public_key: Optional[bytes]


@dataclass(frozen=True, repr=False)
class MappingEnd:
    context: StoreContext
    vault: VaultMaterial
    file_evidence: tuple
    record: Optional[CredentialRecord]


def read_mapping_end(account, ref):
    require(isinstance(ref.name, str) and SECRET_NAME.fullmatch(ref.name)
            and ref.name != "vault" and not ref.name.startswith("vault."), "invalid transfer credential name")
    with resolved_store(account, ref.store_id, ref.generations) as (context, binding, root_fd, _):
        vault = read_vault(root_fd, account, binding)
        with bound_directory(root_fd, path_parts(account.root.path, binding.root.path), binding.root,
                             "checkout binding mismatch") as checkout:
            with open_directory(checkout, ("secrets",)) as directory:
                info = os.fstat(directory)
                require((info.st_dev, info.st_ino) == vault.evidence[0], "transfer vault directory changed")
                evidence, files = [], {}
                def verify(_fd, info):
                    require(info.st_nlink == 1, "linked transfer credential")
                    evidence.append(file_evidence(info))
                for suffix, limit in (("meta", 4096), ("age", JSON_LIMIT), ("pub", 4096)):
                    try:
                        files[suffix] = read_file(directory, ref.name + "." + suffix, limit, verify)
                    except FileNotFoundError:
                        pass
    record = None
    if files:
        require("meta" in files and files.get("age"), "incomplete transfer credential")
        record = CredentialRecord(files["meta"], files["age"], files.get("pub"))
    return MappingEnd(context, vault, tuple(evidence), record)


def unlock_end(cache, tools, account, load, ref, read_passphrase):
    require(load() == account, "transfer account approval changed")
    phrase = read_passphrase(ref.store_id)
    require(load() == account and verified_age_tools(account) == tools, "transfer authority or tools changed")
    cache.unlock(load, ref.store_id, ref.generations, phrase)
    identity = cache.get(load, ref.store_id, ref.generations)
    require(identity is not None, "transfer vault changed after unlock")
    keys = [line for line in identity.split(b"\n") if line and not line.startswith(b"#")]
    require(len(keys) == 1 and re.fullmatch(rb"AGE-SECRET-KEY-1[AC-HJ-NP-Z02-9]{58}", keys[0]),
            "transfer requires a single native vault identity")
    return identity


def single_recipient(cache, material):
    match = re.fullmatch(rb"(age1[ac-hj-np-z02-9]{58})\n?", material.recipient)
    require(match, "transfer requires a single native vault recipient")
    # Native age validates the complete Bech32 recipient before either unlock prompt.
    cache.seal_secret(match[1], b"")
    return match[1]


def key_binding(record):
    date = rb"(?:created=[0-9]{4}-[0-9]{2}-[0-9]{2}\n)?"
    if record.public_key is None:
        require(re.fullmatch(rb"type=password\n" + date, record.metadata), "unsupported transfer credential metadata")
        return None
    meta = re.fullmatch(rb"type=key\n(?:fp=(SHA256:[A-Za-z0-9+/]{43})\nkeytype=([a-z0-9]+)\n)?" + date,
                        record.metadata)
    key = re.fullmatch(rb"(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521))[ \t]+([A-Za-z0-9+/]+={0,2})"
                       rb"(?:[ \t]+[^\x00-\x1f\x7f]*)?\n?", record.public_key)
    require(meta and key, "unsupported transfer key metadata")
    try:
        blob = base64.b64decode(key[2], validate=True)
    except binascii.Error:
        raise StoreContextError("invalid transfer public key") from None
    if meta[1]:
        fingerprint = b"SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).rstrip(b"=")
        keytype = b"ecdsa" if key[1].startswith(b"ecdsa-") else key[1][4:]
        require((meta[1], meta[2]) == (fingerprint, keytype), "inconsistent transfer key metadata")
    return key[1], key[2]


@contextmanager
def private_workspace():
    with tempfile.TemporaryDirectory(prefix="hb-map-", dir="/tmp") as scratch:
        directory = os.open(scratch, DIRECTORY_FLAGS)
        try:
            require_private_directory(directory, "transfer staging")
            yield Path(scratch), directory
        finally:
            os.close(directory)


def verify_private_key(plain, key):
    # Imported unencrypted PEM records keep their original encoding. Bound the
    # accepted envelopes before asking the private agent to prove key possession.
    lines = plain.splitlines()
    require(len(lines) >= 3 and lines[0] in (b"-----BEGIN OPENSSH PRIVATE KEY-----",
            b"-----BEGIN RSA PRIVATE KEY-----", b"-----BEGIN EC PRIVATE KEY-----", b"-----BEGIN PRIVATE KEY-----")
            and lines[-1] == lines[0].replace(b"BEGIN", b"END"), "unsupported transfer private key")
    try:
        raw = base64.b64decode(b"".join(lines[1:-1]), validate=True)
    except binascii.Error:
        raise StoreContextError("invalid transfer private key") from None
    if lines[0] == b"-----BEGIN OPENSSH PRIVATE KEY-----":
        require(raw.startswith(b"openssh-key-v1\0\0\0\0\x04none\0\0\0\x04none"),
                "requires an unlocked OpenSSH private key")
    elif lines[0] == b"-----BEGIN PRIVATE KEY-----":
        # An unencrypted PKCS#8 PrivateKeyInfo starts with an INTEGER version.
        # An EncryptedPrivateKeyInfo starts with an AlgorithmIdentifier instead.
        require(key[0] != b"ssh-ed25519" and len(raw) > 4 and raw[0] == 0x30, "invalid RSA or EC private key")
        extra = raw[1] & 0x7f if raw[1] >= 128 else 0
        start = 2 + extra
        require(extra <= 4 and raw[start:start+3] in (b"\x02\x01\x00", b"\x02\x01\x01"),
                "expected an unencrypted PKCS#8 private key")
    with private_workspace() as (folder, directory):
        with ssh_agent(folder/"agent") as env:
            agent_keys(env, "-q", "-", data=plain)
            observed = [tuple(line.split()[:2]) for line in agent_keys(env, "-L").splitlines()]
            require(observed == [key], "transfer public and private key do not match")
            durable_file(directory, "expected.pub", b" ".join(key) + b"\n")
            agent_keys(env, "-T", str(folder/"expected.pub"))


@dataclass(frozen=True, repr=False)
class StagedCredential(CredentialRecord):
    source: CredentialRef
    destination: CredentialRef
    account: Account
    source_before: MappingEnd
    destination_before: MappingEnd
    reused: bool

    def check_current(self, load, *, saved=False):
        account = load()
        require(account == self.account and read_mapping_end(account, self.source) == self.source_before,
                "transfer source credential or vault changed")
        current = read_mapping_end(account, self.destination)
        if saved and not self.reused:
            require(current.context == self.destination_before.context and current.vault == self.destination_before.vault
                    and current.record == CredentialRecord(self.metadata, self.ciphertext, self.public_key),
                    "saved transfer credential or vault changed")
        else:
            require(current == self.destination_before, "transfer destination credential or vault changed")
        return current

    @contextmanager
    def private_files(self, load):
        """Ephemeral encrypted input for the transfer writer. A reused record needs no writes."""
        require(not self.reused, "existing credential mapping needs no write")
        self.check_current(load)
        with private_workspace() as (folder, directory):
            for suffix, data in (("meta", self.metadata), ("age", self.ciphertext), ("pub", self.public_key)):
                if data is not None:
                    name = self.destination.name + "." + suffix
                    durable_file(directory, name, data)
                    require(read_file(directory, name, JSON_LIMIT) == data, "transfer staging write failed")
            self.check_current(load)
            yield folder

    def verify_decryption(self, load, read_passphrase, *, saved):
        current = self.check_current(load, saved=saved)
        tools = verified_age_tools(self.account)
        cache = VaultUnlockCache(tools[0])
        identity = unlock_end(cache, tools, self.account, load, self.destination, read_passphrase)
        ciphertext = current.record.ciphertext if saved else self.ciphertext
        require(cache.open_secret(identity, ciphertext), "transfer credential is empty")
        require(verified_age_tools(self.account) == tools, "transfer vault tools changed")
        require(self.check_current(load, saved=saved) == current, "transfer credential changed")

    def verify_saved(self, load, read_passphrase):
        """Recheck exact saved bytes and prove the bound destination still decrypts them."""
        self.verify_decryption(load,read_passphrase,saved=True)


def stage_credential(load_account, source, destination, read_passphrase, *, reuse_existing=False):
    """Stage a new destination name or explicitly verify an existing equivalent credential."""
    require(source.store_id != destination.store_id and type(reuse_existing) is bool, "invalid transfer mapping")
    account = load_account()
    original, target = (read_mapping_end(account, ref) for ref in (source, destination))
    require(original.record is not None, "source credential is missing")
    tools = verified_age_tools(account)
    cache = VaultUnlockCache(tools[0])
    source_recipient, target_recipient = (single_recipient(cache, end.vault) for end in (original, target))
    require(source_recipient != target_recipient, "transfer vaults share an identity")
    key = key_binding(original.record)
    if reuse_existing:
        require(target.record is not None, "approved destination credential is missing")
        require(key_binding(target.record) == key, "destination credential has different metadata or public key")
    else:
        require(target.record is None, "destination credential is occupied")
    source_id, destination_id = (unlock_end(cache, tools, account, load_account, ref, read_passphrase)
                                 for ref in (source, destination))
    plain = cache.open_secret(source_id, original.record.ciphertext)
    require(plain, "empty transfer credential")
    if key:
        verify_private_key(plain, key)
    if reuse_existing:
        record = target.record
    else:
        encrypted = cache.seal_secret(target_recipient, plain)
        require(0 < len(encrypted) <= JSON_LIMIT, "encrypted transfer credential exceeds the record limit")
        record = CredentialRecord(original.record.metadata, encrypted, original.record.public_key)
    require(compare_digest(cache.open_secret(destination_id, record.ciphertext), plain),
            "destination credential contains different material")
    staged = StagedCredential(record.metadata, record.ciphertext, record.public_key, source, destination, account,
                              original, target, reuse_existing)
    require(verified_age_tools(account) == tools, "transfer vault tools changed")
    staged.check_current(load_account)
    return staged
