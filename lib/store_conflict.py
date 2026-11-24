"""Compare one retained, rejected shared publication with its original branch."""
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import shutil
from uuid import uuid4

from store_checkout import direct_ref, tree
from store_context import (Generations, index_rows, store_writer, load_approved_account, open_directory, parse_json, read_file,
                           require, require_approval_file, valid_id, verified_account)
from store_mode import candidate_record, mode_records
from store_publish import (attempt_binding, launch_reserved, prepare_proposal, read_attempt, record_at, recovery_root,
                           require_plain_receiver, reserve_edit, stanza)
from store_read import DIRECTIVE, INDEX_NAME, SECRET_NAME, local_inputs
from store_receive import IDENTITY, absolute_directory, fetch_direct, git, oid, plain_git, receive_binding, verify_incoming
from store_transfer_credentials import private_workspace

ANCHORS = ("secrets/vault.pub","secrets/vault.key.age","secrets/vault.recovery.age")


def selected_version(stage,rev,files,store,host,local):
    routes = {}
    verify_incoming(stage,rev,rev,store,local,review_routes=routes)
    shutil.rmtree(stage/"validated")
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
    route = routes[host]
    return dict(alias=alias,address=address,credential=credential,user=route["user"],port=route["port"],
                effective_address=route["hostname"],proxyjump=route["proxyjump"],settings=dict(route["settings"])),signature


def compare_versions(stage,store,candidate,tip,local):
    base,draft,host = (candidate[key] for key in ("base_oid","proposed_oid","host_id"))
    versions = (base,draft,tip)
    trees = [tree(stage,revision,git) for revision in versions]
    sides = [selected_version(stage,rev,files,store,host,local) for rev,files in zip(versions,trees)]
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
    with verified_account(account) as (_,state):
        local = local_inputs(state,store)
    with recovery_root(account) as recovery, private_workspace() as (stage,_):
        stage = stage.resolve(strict=True)
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
            report = compare_versions(stage,store,candidate,tip,local)
            def current():
                require(load()==account and pending()==candidate,"conflict authority changed")
                with verified_account(account) as (_,state):
                    require(local_inputs(state,store)==local,"local SSH inputs changed")
                with receive_binding(account,store,expected) as (again,actual,fd,_,where):
                    require((again,actual,where)==(ctx,target,binding) and direct_ref(fd,ctx.ref)==tip,
                            "conflict comparison is stale")
            current()
            yield report,stage,current,(account,ctx,target,remote,binding,recovery,local)


def compare(load,store,expected,ident):
    with comparison(load,store,expected,ident) as (report,_,current,_):
        current()
        return report


def resolve(load,store,expected,ident,action,draft,tip):
    require(action == "use-candidate-address","select an explicit supported conflict decision")
    with comparison(load,store,expected,ident) as (report,stage,current,inputs), ExitStack() as opened:
        account,ctx,target,remote,binding,recovery,local = inputs
        host = report["host_id"]
        require((report["candidate_revision"],report["remote_revision"]) == (draft,tip)
                and report["kind"] in ("same-host-edit","compatible"),"conflict decision differs from the reviewed versions")
        require(all(report[side] is not None for side in ("base","candidate","remote")),
                "address resolution needs the same existing host on both branches")
        chosen = str(ipaddress.ip_address(report["candidate"]["address"]))
        require(all(report[side]["credential"] == report["remote"]["credential"] for side in ("candidate","base")),
                "review the changed credential binding separately")
        versions = [record_at(stage,oid,store,host) for oid in (report["base_revision"],draft,tip)]
        # The current operator route supports ordinary targets. PROD needs terminal alias confirmation.
        for version,revision in zip(versions,(report["base_revision"],draft,tip)):
            source = git(stage,"show",revision+":"+version[1])
            require(not any(b"prod" in line.split(b":",1)[1].lower().split() for line in source.split(b"\n")
                            if re.match(rb"^[ \t]*#[ \t]*tags[ \t]*:",line,re.I)),"PROD conflict needs terminal alias confirmation")
        require_plain_receiver(remote)
        attempt = str(uuid4())
        proposed = prepare_proposal(stage,tip,tip,store,host,chosen,attempt,versions[2][4])
        verify_incoming(stage,proposed,tip,store,local,{report["remote"]["alias"]:{"hostname":chosen}})
        bundle = stage/"resolved.bundle"
        git(stage,"bundle","create",str(bundle),"refs/hostbook/proposal","^"+tip)
        with store_writer(load,store,expected) as (actual,locked,_,_lock):
            require((actual,locked) == (account,ctx),"resolution registration changed")
            current()
            require_plain_receiver(remote)
            with absolute_directory(ctx.checkout) as work:
                info = os.fstat(work)
                require((info.st_dev,info.st_ino) == (binding.root.device,binding.root.inode),"resolution checkout changed")
                files = {}
                identity,_ = index_rows(work,store,files)
                selected = stanza(identity,files.__getitem__,store,host)[4]
                require(selected in (versions[0][4],versions[2][4]),"local host changed during conflict resolution")
            link = dict(action=action,original_attempt=ident,original_base=report["base_revision"],original_proposal=draft)
            reservation = reserve_edit(opened,recovery,account,ctx,target,host,tip,proposed,bundle,
                                       attempt,str(uuid4()),resolution=link)
        result = launch_reserved(load,account,recovery,ctx,expected,stage,remote,reservation)
        result["original_attempt"] = ident
        return result


def conflict_command(args):
    require(len(args) in (6,9),"provide store ID, four generations and original attempt, then optional decision and reviewed candidate/remote revisions")
    store,expected,ident = args[0],Generations(*(int(v) for v in args[1:5])),args[5]
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    from store_transfer import interruptible_preview
    with interruptible_preview():
        def load():
            return load_approved_account(home)
        result = compare(load,store,expected,ident) if len(args)==6 else resolve(load,store,expected,ident,*args[6:])
    print(json.dumps(result))
    return 0 if result.get("outcome","accepted") == "accepted" else 75
