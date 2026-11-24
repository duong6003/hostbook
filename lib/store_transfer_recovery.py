"""Authenticated recovery payloads for one transfer and its independent age identity."""
from dataclasses import dataclass
from hmac import compare_digest
import os

from store_context import (JSON_LIMIT, read_file, require, require_approval_file, require_private_directory, valid_id)
from store_login import verified_age_tools
from store_vault import VaultUnlockCache, file_evidence


@dataclass(frozen=True, repr=False)
class SealedPayload:
    recipient: bytes
    password_envelope: bytes
    recovery_envelope: bytes
    ciphertext: bytes


def seal_payload(account, manifest, password, recovery_code):
    require(manifest and len(manifest) <= JSON_LIMIT and password != recovery_code,
            "complete transfer manifest and independent recovery credentials are required")
    tools = verified_age_tools(account)
    vault = VaultUnlockCache(tools[0])
    identity, recipient = vault.generate_identity()
    protected = SealedPayload(recipient, vault.wrap_identity(identity, password),
                              vault.wrap_identity(identity, recovery_code), vault.seal_secret(recipient.strip(), manifest))
    require(len(protected.ciphertext) <= JSON_LIMIT, "transfer recovery payload exceeds the supported size")
    require(verified_age_tools(account) == tools, "transfer recovery tools changed")
    return protected


def capture(envelopes, payloads, payload_id, names):
    require(valid_id(payload_id), "invalid transfer payload ID")
    evidence = []
    for directory in (envelopes, payloads):
        require_private_directory(directory, "transaction recovery")
        evidence.append(file_evidence(os.fstat(directory)))

    def private_file(fd, info):
        require_approval_file(fd,info)
        evidence.append(file_evidence(info))

    files = {name:read_file(envelopes,name,JSON_LIMIT,private_file) for name in ("vault.pub",*names)}
    files["payload"] = read_file(payloads,payload_id+".age",JSON_LIMIT,private_file)
    return files, evidence


def open_manifest(account, envelopes, payloads, payload_id, access, expected=None):
    names = tuple(name for name, _phrase in access)
    original = capture(envelopes,payloads,payload_id,names)
    files = original[0]
    tools = verified_age_tools(account)
    vault = VaultUnlockCache(tools[0])
    for name, phrase in access:
        identity = vault.open_identity(files[name],phrase,files["vault.pub"])
        manifest = vault.open_secret(identity,files["payload"])
        require(0 < len(manifest) <= JSON_LIMIT and (expected is None or compare_digest(manifest,expected)),
                "transaction backup does not match its reviewed manifest")
    require(verified_age_tools(account) == tools and capture(envelopes,payloads,payload_id,names) == original,
            "transaction backup changed")
    return manifest


def read_payload(account, envelopes, payloads, payload_id, phrase, *, recovery=False):
    """Recover through an available operator-held credential."""
    name = "vault.recovery.age" if recovery else "vault.key.age"
    return open_manifest(account,envelopes,payloads,payload_id,((name,phrase),))


def verify_backup(account, envelopes, payloads, payload_id, password, code, expected):
    """Prove both retained envelopes and the complete payload before a new transfer effect."""
    require(password != code and expected, "independent recovery credentials and a complete manifest are required")
    open_manifest(account,envelopes,payloads,payload_id,(("vault.key.age",password),("vault.recovery.age",code)),expected)
