"""Inventory a named legacy checkout without running its configuration or altering its data."""
import base64
import binascii
import json
import os
from pathlib import Path
import re

from store_checkout import MAX_STORE_FILES, count_file, read_ref, unlinked
from store_context import JSON_LIMIT, StoreContextError, open_directory, read_file, require, valid_ref
from store_read import SECRET_NAME
from store_receive import absolute_directory, git
from store_vault import file_evidence


def archive_header(data, kind):
    lines = data.split(b"\n")
    if (len(lines) < 4 or lines[0] != b"-----BEGIN AGE ENCRYPTED FILE-----"
            or lines[-2:] != [b"-----END AGE ENCRYPTED FILE-----",b""] or any(not line or len(line) > 64 for line in lines[1:-2])):
        return False
    try:
        raw = base64.b64decode(b"".join(lines[1:-2]),validate=True)
    except (ValueError,binascii.Error):
        return False
    return raw.startswith(b"age-encryption.org/v1\n-> "+kind+b" ") and b"\n--- " in raw


def vault_inventory(root):
    if "secrets" not in os.listdir(root):
        return "absent",None
    try:
        with open_directory(root,("secrets",)) as vault:
            names = sorted(os.listdir(vault))
            require(len(names) <= MAX_STORE_FILES,"legacy vault has too many files")
            evidence,files,total = [file_evidence(os.fstat(vault))],{},0
            def observe(fd,info):
                unlinked(fd,info)
                evidence.append(file_evidence(info))
            for name in names:
                data = read_file(vault,name,JSON_LIMIT,observe)
                total = count_file(total,len(data))
                files[name] = data
        if not files:
            return "absent",evidence
        vault_names = {"vault.pub","vault.key.age","vault.recovery.age"}
        if not vault_names <= set(files):
            return "partial",evidence
        if (not re.fullmatch(rb"age1[ac-hj-np-z02-9]{58}\n?",files["vault.pub"])
                or any(not archive_header(files[p],b"scrypt") for p in ("vault.key.age","vault.recovery.age"))):
            return "damaged",evidence
        credentials = {}
        for path in set(files)-vault_names:
            name,separator,suffix = path.rpartition(".")
            if not separator or not SECRET_NAME.fullmatch(name) or suffix not in ("meta","age","pub") or name == "vault":
                return "unrecognized",evidence
            credentials.setdefault(name,set()).add(suffix)
        for name,parts in credentials.items():
            if not {"meta","age"} <= parts:
                return "partial",evidence
            kind = files[name+".meta"].partition(b"\n")[0]
            if kind not in (b"type=key",b"type=password") or not archive_header(files[name+".age"],b"X25519"):
                return "damaged",evidence
            if (kind == b"type=key") != ("pub" in parts):
                return "partial",evidence
        return "locked-unverified",evidence
    except (OSError,StoreContextError):
        return "unreadable",None


def git_inventory(root):
    blockers = []
    if ".git" not in os.listdir(root):
        return {"role":"private-default","remote_count":0,"remote_observation":"not-configured","ref":None,
                "revision":None,"git_state":"absent","prior_remote_refs":False},blockers,None
    unknown = {"role":"ambiguous","remote_count":None,"remote_observation":"unknown","ref":None,
               "revision":None,"git_state":"unresolved","prior_remote_refs":None}
    try:
        with open_directory(root,(".git",)) as repo:
            config = read_file(repo,"config",65536,unlinked)
            # Parse only the supplied bytes. Never follow repository includes or ask a transport for discovery.
            parsed = git("/","config","--no-includes","--file","-","--list","--null",data=config)
            pairs = [item.partition(b"\n")[::2] for item in parsed.split(b"\0") if item]
            names = {k[7:].rsplit(b".",1)[0] for k,_ in pairs if k.startswith(b"remote.") and b"." in k[7:]}
            if names:
                unknown.update(role="shared",remote_count=len(names),remote_observation="not-contacted")
            indirect = any(k.startswith((b"include.",b"includeif.",b"extensions.")) for k,_ in pairs)
            upstream = any(k.startswith(b"branch.") and k.endswith(b".remote") and v not in (b".",b"") for k,v in pairs)
            repo_names = set(os.listdir(repo))
            indirect |= bool(repo_names & {"commondir","config.worktree","gitdir"})
            for remotes in ("remotes","branches"):
                if remotes in repo_names:
                    with open_directory(repo,(remotes,)) as files:
                        indirect |= bool(os.listdir(files))
            role = "shared" if names else "ambiguous" if indirect or upstream else "private-default"
            if indirect or (upstream and not names):
                blockers.append("resolve-external-git-configuration")
            if len(names) > 1:
                blockers.append("choose-exact-remote")
            if role == "shared":
                blockers.append("verify-shared-vault-and-ref-policy")
            ref,revision = None,None
            try:
                head = read_file(repo,"HEAD",4096,unlinked)
                full = head.removeprefix(b"ref: ").removesuffix(b"\n").decode("ascii")
                require(head.startswith(b"ref: ") and valid_ref(full),"legacy checkout needs one branch")
                ref,revision = full,read_ref(repo,full)
            except (UnicodeError,OSError,StoreContextError):
                blockers.append("select-verifiable-local-branch")
            pending = repo_names & {"MERGE_HEAD","REVERT_HEAD","CHERRY_PICK_HEAD","rebase-apply","rebase-merge","sequencer","index.lock"}
            if pending:
                blockers.append("preserve-and-reconcile-pending-git-work")
            packed = read_file(repo,"packed-refs",JSON_LIMIT,unlinked) if "packed-refs" in repo_names else b""
            prior = b" refs/remotes/" in packed
            try:
                with open_directory(repo,("refs","remotes")) as remote_refs:
                    prior |= bool(os.listdir(remote_refs))
            except FileNotFoundError:
                pass
            if prior and not names:
                blockers.append("review-prior-publication")
            stamp = (file_evidence(os.fstat(repo)),config,packed)
            return {"role":role,"remote_count":len(names),"remote_observation":"not-contacted" if names else "unknown" if indirect or upstream else "not-configured",
                    "ref":ref,"revision":revision,"git_state":"in-progress" if pending else "inventory-only",
                    "prior_remote_refs":prior},blockers,stamp
    except (OSError,StoreContextError):
        if unknown["role"] == "shared":
            blockers.append("verify-shared-vault-and-ref-policy")
        return unknown,list(dict.fromkeys([*blockers,"inspect-original-git-configuration"])),None


def inspect_legacy(path):
    require(path.is_absolute() and path.anchor == "/" and len(path.parts) > 1 and ".." not in path.parts,
            "select an absolute legacy checkout path")
    def observe():
        with absolute_directory(path) as root:
            info = os.fstat(root)
            report,blockers,git_stamp = git_inventory(root)
            vault,vault_stamp = vault_inventory(root)
            if ".hostbook" in os.listdir(root):
                blockers.append("review-existing-store-metadata")
            if vault != "absent":
                blockers.append("unlock-and-verify-original-vault" if vault == "locked-unverified" else "preserve-and-reconcile-vault")
            blockers.extend(["verify-work-index-dependencies-and-backup","verify-legacy-writer-exclusion"])
            report.update(checkout={"device":info.st_dev,"inode":info.st_ino},vault=vault,cutover_ready=False,blockers=blockers)
            return report,(file_evidence(info),git_stamp,vault_stamp)
    result = observe()
    require(observe() == result,"legacy checkout changed during inspection")
    return result[0]


def inspection_command(args):
    require(len(args) == 1,"provide the original absolute legacy checkout path")
    print(json.dumps(inspect_legacy(Path(args[0]))))
    return 0
