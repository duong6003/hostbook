"""Inventory a named legacy checkout without running its configuration or altering its data."""
import base64
import binascii
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from store_checkout import MAX_STORE_FILES, OID, count_file, direct_ref, read_ref, unlinked
from store_context import DIRECTORY_FLAGS, JSON_LIMIT, StoreContextError, open_directory, read_file, require, valid_ref
from store_read import SECRET_NAME
from store_receive import absolute_directory, git
from store_transfer_credentials import CredentialRecord, key_binding
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
        anchors = tuple(files.get(name) for name in ("vault.pub","vault.key.age","vault.recovery.age"))
        return vault_condition(files),(evidence,anchors)
    except (OSError,StoreContextError):
        return "unreadable",None


def vault_condition(files):
    if not files:
        return "absent"
    vault_names = {"vault.pub","vault.key.age","vault.recovery.age"}
    if not vault_names <= set(files):
        return "partial"
    if (not re.fullmatch(rb"age1[ac-hj-np-z02-9]{58}\n?",files["vault.pub"])
            or any(not archive_header(files[p],b"scrypt") for p in ("vault.key.age","vault.recovery.age"))):
        return "damaged"
    credentials = {}
    for path in set(files)-vault_names:
        name,separator,suffix = path.rpartition(".")
        if not separator or not SECRET_NAME.fullmatch(name) or suffix not in ("meta","age","pub") or name == "vault":
            return "unrecognized"
        credentials.setdefault(name,set()).add(suffix)
    for name,parts in credentials.items():
        if not {"meta","age"} <= parts:
            return "partial"
        kind = files[name+".meta"].partition(b"\n")[0]
        if kind not in (b"type=key",b"type=password") or not archive_header(files[name+".age"],b"X25519"):
            return "damaged"
        if (kind == b"type=key") != ("pub" in parts):
            return "partial"
        public = files.get(name+".pub")
        try:
            key_binding(CredentialRecord(files[name+".meta"],files[name+".age"],public))
            if public is not None:
                result = subprocess.run(["/usr/bin/ssh-keygen","-lf","-"],input=public,
                                        env={"PATH":"/usr/bin:/bin"},stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL,timeout=5)
                if result.returncode:
                    return "damaged"
        except (OSError,StoreContextError,subprocess.SubprocessError):
            return "damaged"
    return "locked-unverified"


def enclosing_repository(root):
    child = os.dup(root)
    try:
        while True:
            parent = os.open("..",DIRECTORY_FLAGS,dir_fd=child)
            same = os.path.samestat(os.fstat(parent),os.fstat(child))
            os.close(child)
            child = parent
            if same:
                return False
            try:
                os.stat(".git",dir_fd=parent,follow_symlinks=False)
                return True
            except FileNotFoundError:
                pass
    finally:
        os.close(child)


def git_inventory(root):
    blockers = []
    if ".git" not in os.listdir(root):
        if enclosing_repository(root):
            return {"role":"ambiguous","remote_count":None,"remote_observation":"unknown","ref":None,
                    "revision":None,"git_state":"enclosing-checkout","prior_remote_refs":None},["select-original-checkout-root"],None
        return {"role":"private-default","remote_count":0,"remote_observation":"not-configured","ref":None,
                "revision":None,"git_state":"absent","prior_remote_refs":False},blockers,None
    unknown = {"role":"ambiguous","remote_count":None,"remote_observation":"unknown","ref":None,
               "revision":None,"git_state":"unresolved","prior_remote_refs":None}
    try:
        with open_directory(root,(".git",)) as repo:
            config = read_file(repo,"config",65536,unlinked)
            # Parse only the supplied bytes. Never follow repository includes or ask a transport for discovery.
            pairs = config_pairs(config)
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
            if names and ref and not remote_choices_match(names,pairs,ref):
                if "choose-exact-remote" not in blockers:
                    blockers.append("choose-exact-remote")
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


def config_pairs(data):
    parsed = git("/","config","--no-includes","--file","-","--list","--null",data=data)
    return [item.partition(b"\n")[::2] for item in parsed.split(b"\0") if item]


def remote_choices_match(names,pairs,ref):
    if len(names) != 1:
        return False
    name, = names
    key = b"remote."+name
    upstream = b"branch."+ref.removeprefix("refs/heads/").encode()
    def values(option):
        return [v for k,v in pairs if k == option]
    urls = values(key+b".url")
    return (len(urls) == 1 and urls[0] and values(key+b".pushurl") in ([],urls)
            and values(upstream+b".remote") in ([],[name])
            and len(values(upstream+b".merge")) <= 1
            and values(upstream+b".pushremote") in ([],[name])
            and values(b"remote.pushdefault") in ([],[name])
            and not any(k.startswith(b"url.") for k,_ in pairs))


def review_target(config, report, target, ref):
    require(report["role"] == "shared" and report["remote_count"] == 1 and report["ref"] and
            not {"resolve-external-git-configuration","choose-exact-remote"}.intersection(report["blockers"]),"select an unambiguous remote")
    pairs = config_pairs(config)
    names = {k[7:].rsplit(b".",1)[0] for k,_ in pairs if k.startswith(b"remote.") and b"." in k[7:]}
    name, = names
    def values(key):
        return [v for k,v in pairs if k == key]
    key = b"remote."+name
    urls = values(key+b".url")
    upstream = b"branch."+report["ref"].removeprefix("refs/heads/").encode()
    require(len(urls) == 1 and urls[0] in (os.fsencode(target),target.as_uri().encode())
            and not values(key+b".pushurl") and not any(k.startswith(b"url.") for k,_ in pairs)
            and values(upstream+b".remote") in ([],[name])
            and values(upstream+b".merge") in ([],[ref.encode()]),"selected remote or branch differs")


def snapshot_objects(repo, stage):
    # Copy only object data. Git reads our generated bare config, refs and hooks directory.
    count,total = 0,0
    with open_directory(repo,("objects",)) as objects:
        for group in os.listdir(objects):
            if group == "info":
                with open_directory(objects,(group,)) as info:
                    require(not set(os.listdir(info)) & {"alternates","http-alternates"},"external object database")
                continue
            require(group == "pack" or re.fullmatch("[0-9a-f]{2}",group),"unrecognized object database")
            folder = stage/"objects"/group
            folder.mkdir(exist_ok=True)
            with open_directory(objects,(group,)) as source:
                for name in os.listdir(source):
                    if group == "pack":
                        require(re.fullmatch(r"pack-[0-9a-f]{40}\.(?:pack|idx|rev|bitmap)",name),
                                "unrecognized Git pack")
                        if not name.endswith((".pack",".idx")):
                            continue
                    else:
                        require(re.fullmatch("[0-9a-f]{38}",name),"unrecognized Git object")
                    count += 1
                    require(count <= MAX_STORE_FILES,"legacy object database is too large")
                    data = read_file(source,name,JSON_LIMIT,unlinked)
                    total = count_file(total,len(data))
                    (folder/name).write_bytes(data)


def remote_vault(repo, ref):
    tip = direct_ref(repo,ref)
    with tempfile.TemporaryDirectory(prefix="hostbook-migration-",dir="/tmp") as scratch:
        stage = Path(scratch)
        git(stage,"init","-q","--bare","--template=")
        snapshot_objects(repo,stage)
        require(git(stage,"cat-file","-t",tip) == b"commit\n","selected remote ref is not a commit")
        git(stage,"fsck","--strict","--no-reflogs","--no-dangling",tip)
        root = git(stage,"ls-tree","-z",tip,"--","secrets")
        files,total = {},0
        if root:
            require(re.fullmatch(rb"040000 tree [0-9a-f]{40}\tsecrets\x00",root),"unrecognized remote vault")
            rows = git(stage,"ls-tree","-lz",tip+":secrets").split(b"\0")[:-1]
            require(len(rows) <= MAX_STORE_FILES,"remote vault has too many files")
            for row in rows:
                meta,name = row.split(b"\t",1)
                mode,kind,oid,length = meta.split()
                require(mode == b"100644" and kind == b"blob" and OID.fullmatch(oid),"unrecognized remote vault entry")
                total = count_file(total,int(length))
                files[name.decode("ascii")] = git(stage,"cat-file","blob",oid.decode())
        return tip,vault_condition(files),tuple(files.get(n) for n in ("vault.pub","vault.key.age","vault.recovery.age"))


def discover(report,stamp,target,ref):
    unresolved = dict(remote_observation="unresolved",remote_vault="unknown",vault_decision="preserve-and-rediscover")
    try:
        require(target.is_absolute() and target.anchor == "/" and len(target.parts)>1 and ".." not in target.parts
                and valid_ref(ref) and stamp[1] is not None,"select an absolute local bare repository and direct branch")
        review_target(stamp[1][1],report,target,ref)
        with absolute_directory(target) as remote:
            binding = os.fstat(remote)
            require((binding.st_dev,binding.st_ino) != tuple(report["checkout"][k] for k in ("device","inode")),
                    "select a separate bare repository")
            pairs = config_pairs(read_file(remote,"config",65536,unlinked))
            require([v for k,v in pairs if k == b"core.bare"] == [b"true"] and
                    not any(k.startswith((b"extensions.",b"include.",b"includeif.")) for k,_ in pairs),
                    "remote is not a regular bare repository")
            tip,condition,anchors = remote_vault(remote,ref)
            with absolute_directory(target) as actual:
                check = os.fstat(actual)
                require((check.st_dev,check.st_ino) == (binding.st_dev,binding.st_ino) and direct_ref(actual,ref) == tip,
                        "remote binding changed during observation")
        local = report["vault"]
        if "absent" == local == condition:
            decision = "both-absent-unapproved"
        elif local == "absent" and condition == "locked-unverified":
            decision = "unlock-existing-shared"
        elif local == condition == "locked-unverified":
            original = stamp[2][1]
            decision = ("preserve-independent-vaults" if original[0].strip() != anchors[0].strip()
                        else "verify-existing-shared" if original == anchors else "reconcile-vault-envelopes")
        else:
            decision = "preserve-and-reconcile-vault"
        return dict(remote_observation="observed",remote_checkout={"device":binding.st_dev,"inode":binding.st_ino},
                    remote_ref=ref,remote_revision=tip,remote_vault=condition,vault_decision=decision)
    except (OSError,StoreContextError,UnicodeError):
        return unresolved


def inspect_legacy(path, target=None, ref=None):
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
    remote = discover(*result,target,ref) if target is not None else {}
    require(observe() == result,"legacy checkout changed during inspection")
    return {**result[0],**remote}


def inspection_command(args):
    require(len(args) == 1 or (len(args) == 4 and args[1] == "--inspect-local"),
            "provide an absolute legacy checkout and optionally --inspect-local BARE FULL_REF")
    target,ref = (Path(args[2]),args[3]) if len(args) == 4 else (None,None)
    from store_transfer import interruptible_preview
    with interruptible_preview():
        print(json.dumps(inspect_legacy(Path(args[0]),target,ref)))
    return 0
