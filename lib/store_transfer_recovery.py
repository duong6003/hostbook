"""Authenticated recovery payloads for one transfer and its independent age identity."""
from dataclasses import dataclass
from hmac import compare_digest

from store_context import (JSON_LIMIT, read_file, require, require_approval_file, require_private_directory, valid_id)
from store_login import verified_age_tools
from store_vault import VaultUnlockCache


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


def read_payload(account, envelopes, payloads, payload_id, phrase, *, recovery=False):
    require(valid_id(payload_id), "invalid transfer payload ID")
    require_private_directory(envelopes, "transaction recovery vault")
    require_private_directory(payloads, "transaction payload")
    def captured():
        name = "vault.recovery.age" if recovery else "vault.key.age"
        return (read_file(envelopes,"vault.pub",4096,require_approval_file),
                read_file(envelopes,name,JSON_LIMIT,require_approval_file),
                read_file(payloads,payload_id+".age",JSON_LIMIT,require_approval_file))
    original = captured()
    recipient, envelope, ciphertext = original
    tools = verified_age_tools(account)
    vault = VaultUnlockCache(tools[0])
    identity = vault.open_identity(envelope, phrase, recipient)
    manifest = vault.open_secret(identity, ciphertext)
    require(0 < len(manifest) <= JSON_LIMIT and verified_age_tools(account) == tools
            and all(compare_digest(a,b) for a,b in zip(captured(),original)), "transaction backup changed")
    return manifest
