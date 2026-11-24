"""Prepare one transfer mapping without writing to a registered store or its Git index."""
from contextlib import contextmanager
from dataclasses import dataclass
from hmac import compare_digest
import os
from pathlib import Path
import re
import tempfile
from typing import Optional

from store_checkout import durable_file
from store_context import (Account, DIRECTORY_FLAGS, Generations, JSON_LIMIT, StoreContext, bound_directory,
                           open_directory, path_parts, read_file, require, require_private_directory,
                           resolved_store)
from store_login import verified_age_tools
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
    phrase = read_passphrase(ref.store_id)
    require(verified_age_tools(account) == tools, "transfer vault tools changed")
    cache.unlock(load, ref.store_id, ref.generations, phrase)
    identity = cache.get(load, ref.store_id, ref.generations)
    require(identity is not None, "transfer vault changed after unlock")
    return identity


def validate_password(record):
    require(record.public_key is None and re.fullmatch(
        rb"type=password\n(?:created=[0-9]{4}-[0-9]{2}-[0-9]{2}\n)?", record.metadata),
        "unsupported transfer credential metadata")


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
        with tempfile.TemporaryDirectory(prefix="hb-map-", dir="/tmp") as scratch:
            folder = Path(scratch)
            directory = os.open(scratch, DIRECTORY_FLAGS)
            try:
                require_private_directory(directory, "transfer staging")
                for suffix, data in (("meta", self.metadata), ("age", self.ciphertext), ("pub", self.public_key)):
                    if data is not None:
                        name = self.destination.name + "." + suffix
                        durable_file(directory, name, data)
                        require(read_file(directory, name, JSON_LIMIT) == data, "transfer staging write failed")
            finally:
                os.close(directory)
            self.check_current(load)
            yield folder

    def verify_saved(self, load, read_passphrase):
        """Recheck exact saved bytes and prove the bound destination still decrypts them."""
        current = self.check_current(load, saved=True)
        tools = verified_age_tools(self.account)
        cache = VaultUnlockCache(tools[0])
        identity = unlock_end(cache, tools, self.account, load, self.destination, read_passphrase)
        require(cache.open_secret(identity, current.record.ciphertext), "saved transfer credential is empty")
        require(verified_age_tools(self.account) == tools, "transfer vault tools changed")
        require(self.check_current(load, saved=True) == current, "saved transfer credential changed")


def stage_credential(load_account, source, destination, read_passphrase, *, reuse_existing=False):
    """Stage a new destination name or explicitly verify an existing equivalent credential."""
    require(source.store_id != destination.store_id and type(reuse_existing) is bool, "invalid transfer mapping")
    account = load_account()
    original, target = (read_mapping_end(account, ref) for ref in (source, destination))
    require(original.record is not None, "source credential is missing")
    require(original.vault.recipient.strip() != target.vault.recipient.strip(), "transfer vaults share an identity")
    if reuse_existing:
        require(target.record is not None, "approved destination credential is missing")
        validate_password(target.record)
    else:
        require(target.record is None, "destination credential is occupied")
    validate_password(original.record)
    tools = verified_age_tools(account)
    cache = VaultUnlockCache(tools[0])
    source_id, destination_id = (unlock_end(cache, tools, account, load_account, ref, read_passphrase)
                                 for ref in (source, destination))
    plain = cache.open_secret(source_id, original.record.ciphertext)
    require(plain, "empty transfer credential")
    if reuse_existing:
        record = target.record
    else:
        encrypted = cache.seal_secret(target.vault.recipient.strip(), plain)
        record = CredentialRecord(original.record.metadata, encrypted, original.record.public_key)
    require(compare_digest(cache.open_secret(destination_id, record.ciphertext), plain),
            "destination credential contains different material")
    staged = StagedCredential(record.metadata, record.ciphertext, record.public_key, source, destination, account,
                              original, target, reuse_existing)
    require(verified_age_tools(account) == tools, "transfer vault tools changed")
    staged.check_current(load_account)
    return staged
