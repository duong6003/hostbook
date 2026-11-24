"""Compare one retained, rejected shared publication with its original branch."""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re

from store_checkout import direct_ref, tree
from store_context import (Generations, load_approved_account, open_directory, parse_json, read_file,
                           require, require_approval_file, valid_id)
from store_mode import candidate_record, mode_records
from store_publish import attempt_binding, read_attempt, record_at, recovery_root
from store_read import DIRECTIVE, INDEX_NAME, SECRET_NAME
from store_receive import IDENTITY, fetch_direct, git, oid, plain_git, receive_binding
from store_transfer_credentials import private_workspace

ANCHORS = ("secrets/vault.pub","secrets/vault.key.age","secrets/vault.recovery.age")


def selected_version(stage,rev,files,store,host):
    identity = parse_json(git(stage,"cat-file","blob",files[IDENTITY].decode()))
    require(identity["store_id"] == store and identity["format"] == "hostbook-store" and
            identity["version"] == 1,"conflict store identity changed")
    if all(h["host_id"] != host for h in identity["hosts"]):
        return None,None
    _,_,lines,pos,signature = record_at(stage,rev,store,host)
    address = DIRECTIVE.fullmatch(lines[pos].decode().strip())[2].split("#",1)[0].strip()
    try:
        address = str(ipaddress.ip_address(address))
    except ValueError:
        require(len(address)<=253 and re.fullmatch(r"[A-Za-z0-9.-]+",address),"unsupported conflict endpoint")
    alias,_,_,credential = signature
    require(INDEX_NAME.fullmatch(alias),"unsupported conflict alias")
    return dict(alias=alias,address=address,credential=credential),signature


def compare_versions(stage,store,candidate,tip):
    base,draft,host = (candidate[key] for key in ("base_oid","proposed_oid","host_id"))
    versions = (base,draft,tip)
    trees = [tree(stage,revision,git) for revision in versions]
    sides = [selected_version(stage,rev,files,store,host) for rev,files in zip(versions,trees)]
    summary = dict(zip(("base","candidate","remote"),(view for view,_ in sides)))
    anchors = [tuple(files.get(key) for key in ANCHORS) for files in trees]
    names = set()
    for files in trees:
        for path in files:
            if path.startswith("secrets/") and path not in ANCHORS:
                name,sep,ext = path[8:].rpartition(".")
                require(sep and ext in ("age","meta","pub") and SECRET_NAME.fullmatch(name),
                        "unrecognized shared credential record")
                names.add(name)
    collisions = []
    for name in sorted(names):
        records = [tuple(files.get("secrets/"+name+"."+ext) for ext in ("age","meta","pub")) for files in trees]
        if records[0] != records[1] != records[2] and records[0] != records[2]:
            collisions.append(name)
    if anchors[0] != anchors[2] or anchors[1] != anchors[0]:
        kind = "vault-identity" if len({r[0] for r in anchors})>1 else "vault-envelopes"
    elif collisions:
        kind = "credential-conflict"
    elif sides[1][0] is None or sides[2][0] is None:
        kind = "delete-versus-edit"
    elif sides[0][1] != sides[2][1] and sides[1][1] != sides[2][1]:
        kind = "same-host-edit"
    else:
        kind = "compatible"
    return dict(store_id=store,host_id=host,attempt_id=candidate["attempt_id"],ref=candidate["ref"],
                base_revision=base,candidate_revision=draft,remote_revision=tip,kind=kind,
                credential_conflicts=collisions,**summary)


@contextmanager
def comparison(load,store,expected,ident):
    require(valid_id(ident),"select the original rejected publication")
    account = load()
    with recovery_root(account) as recovery, private_workspace() as (stage,_):
        def pending():
            active,retained,inflight = mode_records(recovery,account,store,None)
            require(not active and not inflight and retained == [ident],"retained publication changed")
            return candidate_record(recovery,account,store,ident,verify_bundle=True)
        candidate = pending()
        with receive_binding(account,store,expected) as (ctx,target,remote,_,binding):
            require(ctx.effective_mode == "two-way","conflict comparison requires the selected two-way registration")
            original,_ = read_attempt(recovery,account,store,ident)
            with open_directory(recovery,("edits",ident)) as edit:
                evidence = attempt_binding(edit,account,original)
                require(evidence["target_id"] == ctx.target_id and evidence["ref"] == ctx.ref and
                        evidence["bare_repository"] == dict(asdict(target),path=str(target.path)) and
                        all(evidence["generations"][field] == getattr(expected,field)
                            for field in ("registration","checkout","connection")),"original conflict binding changed")
                data = read_file(edit,"proposal.bundle",64*1024*1024,require_approval_file)
                require(hashlib.sha256(data).hexdigest() == evidence["bundle_sha256"],"conflict candidate changed")
            plain_git(remote,bare=True)
            git(stage,"init","-q","--bare","--template=")
            tip = fetch_direct(stage,remote,ctx.ref,"refs/hostbook/current")
            git(stage,"merge-base","--is-ancestor",candidate["base_oid"],tip)
            bundle = stage/"original.bundle"
            bundle.write_bytes(data)
            git(stage,"bundle","verify",str(bundle))
            git(stage,"fetch","--no-tags","--",str(bundle),"refs/hostbook/proposal:refs/hostbook/proposal")
            proposal = oid(stage,"refs/hostbook/proposal")
            require(proposal == candidate["proposed_oid"] and
                    git(stage,"show","-s","--format=%P",proposal).strip().decode() == candidate["base_oid"],
                    "conflict candidate no longer matches its admitted base")
            report = compare_versions(stage,store,candidate,tip)
            def current():
                require(load()==account and pending()==candidate,"conflict authority changed")
                with receive_binding(account,store,expected) as (again,actual,fd,_,where):
                    require((again,actual,where)==(ctx,target,binding) and direct_ref(fd,ctx.ref)==tip,
                            "conflict comparison is stale")
            current()
            yield report,stage,current


def compare(load,store,expected,ident):
    with comparison(load,store,expected,ident) as (report,_,current):
        current()
        return report


def conflict_command(args):
    require(len(args) == 6,"provide store ID, four generations and the original rejected attempt ID")
    store,expected,ident = args[0],Generations(*(int(v) for v in args[1:5])),args[5]
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    from store_transfer import interruptible_preview
    with interruptible_preview():
        result = compare(lambda:load_approved_account(home),store,expected,ident)
    print(json.dumps(result))
    return 0
