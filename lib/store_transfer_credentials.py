"""Prepare one credential mapping without changing either store or its Git state."""
from dataclasses import dataclass
from hmac import compare_digest
import re
from typing import Optional

from store_context import (Generations, JSON_LIMIT, bound_directory, open_directory, path_parts,
                           read_file, require, resolved_store)
from store_login import verified_age_tools
from store_read import SECRET_NAME
from store_vault import VaultUnlockCache, file_evidence, read_vault


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


def read_mapping_end(account, ref):
    require(isinstance(ref.name, str) and SECRET_NAME.fullmatch(ref.name)
            and ref.name != "vault" and not ref.name.startswith("vault."), "invalid transfer credential name")
    with resolved_store(account, ref.store_id, ref.generations) as (context, binding, root_fd, _):
        vault = read_vault(root_fd, account, binding)
        with bound_directory(root_fd, path_parts(account.root.path, binding.root.path), binding.root,
                             "checkout binding mismatch") as checkout:
            with open_directory(checkout, ("secrets",)) as directory:
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
    return context, vault, tuple(evidence), record


def stage_credential(load_account, source, destination, read_passphrase):
    """Return verified encrypted bytes. Only the eventual transfer writer may adopt them."""
    require(source.store_id != destination.store_id, "transfer requires different stores")
    account = load_account()
    initial = tuple(read_mapping_end(account, ref) for ref in (source, destination))
    original = initial[0][3]
    require(original is not None, "source credential is missing")
    require(initial[1][3] is None, "destination credential is occupied")
    require(original.public_key is None and re.fullmatch(
        rb"type=password\n(?:created=[0-9]{4}-[0-9]{2}-[0-9]{2}\n)?", original.metadata),
        "unsupported transfer credential metadata")
    selected = verified_age_tools(account)
    vault = VaultUnlockCache(selected[0])
    identities = []
    for ref in (source, destination):
        phrase = read_passphrase(ref.store_id)
        require(verified_age_tools(account) == selected, "transfer vault tools changed")
        vault.unlock(load_account, ref.store_id, ref.generations, phrase)
        del phrase
        identities.append(vault.get(load_account, ref.store_id, ref.generations))
    require(all(identities), "transfer vault changed after unlock")
    plain = vault.open_secret(identities[0], original.ciphertext)
    require(plain, "empty transfer credential")
    encrypted = vault.seal_secret(initial[1][1].recipient.strip(), plain)
    require(compare_digest(vault.open_secret(identities[1], encrypted), plain),
            "destination credential verification failed")
    del identities, plain
    require(load_account() == account and verified_age_tools(account) == selected
            and tuple(read_mapping_end(account, ref) for ref in (source, destination)) == initial,
            "transfer credential or vault changed during staging")
    return CredentialRecord(original.metadata, encrypted, original.public_key)
