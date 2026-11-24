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
import subprocess
from uuid import uuid4
from types import SimpleNamespace

from store_checkout import direct_ref, tree
from store_context import (Generations, entry_generations, index_rows, registry_entries, store_writer, load_approved_account, open_directory, parse_json, read_file,
                           require, require_approval_file, valid_id, verified_account)
from store_mode import apply_transition, candidate_record, mode_records, preflight_mode, prepare_transition, verify_live
from store_publish import (attempt_binding, commit_changes, launch_reserved, prepare_proposal, read_attempt, record_at, recovery_root,
                           require_plain_receiver, reserve_edit, stanza)
from store_read import DIRECTIVE, INDEX_NAME, SECRET_NAME, local_inputs
from store_receive import IDENTITY, absolute_directory, fetch_direct, git, oid, plain_git, receive_binding, verify_incoming
from store_transfer_credentials import private_workspace

ANCHORS = ("secrets/vault.pub","secrets/vault.key.age","secrets/vault.recovery.age")


def selected_prod(lines,alias):
    tagged = False
    for raw in lines:
        line = raw.decode().strip()
        if annotation := re.fullmatch(r"#[ \t]*tags[ \t]*:[ \t]*(.*)",line,re.I):
            tagged = "prod" in annotation[1].lower().split()
        elif directive := DIRECTIVE.fullmatch(line):
            if directive[1].lower() == "host" and directive[2].split("#",1)[0].strip().lower() == alias.lower():
                return tagged
            tagged = False
        elif line and not line.startswith("#"):
            tagged = False
    return False


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
    return dict(alias=alias,address=address,credential=credential,prod=selected_prod(lines,alias),user=route["user"],port=route["port"],
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
    collisions,changed_bindings = [],[]
    selected = {side["credential"] for side,_ in sides if side is not None and side["credential"]}
    for name in sorted(names):
        records = [tuple(files.get("secrets/"+name+"."+ext) for ext in ("age","meta","pub")) for files in trees]
        if name in selected and (len(set(records)) != 1 or not all(records[0][:2])):
            changed_bindings.append(name)
        if len(set(records)) == 3:
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
    required_aliases = sorted({view["alias"] for view,_ in sides if view is not None and view["prod"]})
    report = dict(store_id=store,host_id=host,attempt_id=candidate["attempt_id"],ref=candidate["ref"],required_aliases=required_aliases,
                base_revision=base,candidate_revision=draft,remote_revision=tip,kind=kind,
                credential_conflicts=collisions,changed_bound_credentials=sorted(set(changed_bindings)|(selected-names)),**summary)

    report["choices"] = ["cancel"]
    if kind in ("same-host-edit","compatible","delete-versus-edit") and not report["changed_bound_credentials"]:
        report["choices"].insert(0,"keep-remote")
        if kind == "delete-versus-edit" and summary["candidate"] and not summary["remote"] and not summary["candidate"]["credential"]:
            report["choices"].insert(0,"restore-candidate")
        if kind != "delete-versus-edit" and summary["candidate"]["address"] != summary["remote"]["address"]:
            report["choices"].insert(0,"use-candidate-address")
    report["review_id"] = hashlib.sha256(json.dumps(report,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    return report


@contextmanager
def comparison(load,store,expected,ident):
    require(valid_id(ident),"select the original rejected publication")
    account = load()
    with verified_account(account) as (_,state):
        inputs = []
        local = local_inputs(state,store,inputs)
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
                    observed = []
                    require(local_inputs(state,store,observed)==local and observed==inputs,"local SSH inputs changed")
                with receive_binding(account,store,expected) as (again,actual,fd,_,where):
                    require((again,actual,where)==(ctx,target,binding) and direct_ref(fd,ctx.ref)==tip,
                            "conflict comparison is stale")
            current()
            yield report,stage,current,(account,ctx,target,remote,binding,recovery,local,inputs)


def compare(load,store,expected,ident):
    with comparison(load,store,expected,ident) as (report,_,current,_):
        current()
        return report


def restore_host(stage,store,report,local,attempt):
    from store_transfer import standalone_document
    tip,draft,host = report["remote_revision"],report["candidate_revision"],report["host_id"]
    selected = report["candidate"]
    identity,path,lines,_,_ = record_at(stage,draft,store,host)
    require(not selected["credential"] and not selected["proxyjump"],"credential or jump restoration requires separate review")
    _,document = standalone_document(SimpleNamespace(identity=identity,files={path:b"".join(lines)}),
                                     host,selected["alias"],None,None,False)
    files = tree(stage,tip,git)
    recovered = parse_json(git(stage,"cat-file","blob",files[IDENTITY].decode()))
    require(all(row["host_id"] != host and row["name"].lower() != selected["alias"].lower() for row in recovered["hosts"]),
            "restoration conflicts with another host identity or alias")
    current = git(stage,"cat-file","blob",files[path].decode()) if path in files else b""
    require(all(not line.strip() or line.lstrip().startswith(b"#") for line in current.split(b"\n"))
            and not re.search(rb"^[ \t]*#[ \t]*(?:tags|secret|desc)[ \t]*:",current,re.M|re.I),
            "restoration requires an absent or unoccupied project without pending annotations")
    for revision in (tip,draft):
        if "groups.tsv" in tree(stage,revision,git):
            groups = git(stage,"show",revision+":groups.tsv").decode().lower().split()
            require(selected["alias"].lower() not in groups,"review existing group membership before restoration")
    updated = current + (b"\n" if current and not current.endswith(b"\n") else b"") + document
    sources = [row for row in recovered["sources"] if row["path"] == path]
    require(len(sources) == int(path in files),"changed project mapping")
    source = dict(path=path,sha256=hashlib.sha256(updated).hexdigest())
    if sources:
        sources[0].update(source)
    else:
        recovered["sources"].append(source)
    recovered["hosts"].append(next(row for row in identity["hosts"] if row["host_id"] == host))
    recovered["generation"] += 1
    before,after = {},{}
    verify_incoming(stage,tip,tip,store,local,review_routes=before)
    shutil.rmtree(stage/"validated")
    require(all(not route["proxyjump"] for route in before.values()),"review existing jump routes before restoration")
    proposed = commit_changes(stage,tip,{path:updated,IDENTITY:json.dumps(recovered).encode()+b"\n"},attempt)
    verify_incoming(stage,proposed,tip,store,local,review_routes=after)
    observed = after.pop(host)
    require(before == after and observed["hostname"] == selected["effective_address"] and
            all(observed[k] == selected[k] for k in ("user","port","proxyjump")) and
            dict(observed["settings"]) == selected["settings"],"restored host changes the reviewed SSH settings")
    return proposed


def resolve(load,store,expected,ident,action,draft,tip,review_id,*,confirm_prod=None):
    require(action in ("use-candidate-address","keep-remote","restore-candidate","cancel"),"select an explicit supported conflict decision")
    with comparison(load,store,expected,ident) as (report,stage,current,inputs), ExitStack() as opened:
        account,ctx,target,remote,binding,recovery,local,ssh_inputs = inputs
        host = report["host_id"]
        require((report["candidate_revision"],report["remote_revision"],report["review_id"]) == (draft,tip,review_id)
                and action in report["choices"],"conflict decision differs from the reviewed versions")
        if action == "cancel":
            return dict(outcome="cancelled",original_attempt=ident)
        if report["required_aliases"]:
            require(confirm_prod and confirm_prod(account,report,action) == report["required_aliases"],
                    "type the exact affected PROD host aliases to confirm")
            current()
        if action == "keep-remote":
            with store_writer(load,store,expected) as (actual,locked,state,writer):
                require((actual,locked) == (account,ctx),"conflict registration changed")
                current()
                entry = registry_entries(state,account)[0][store]
                preflight_mode(state,entry,"two-way")
                candidate = candidate_record(recovery,account,store,ident,verify_bundle=True)
                reviewed = dict(revision=tip,ref=ctx.ref,review_id=review_id,ssh_inputs=ssh_inputs)
                active = prepare_transition(recovery,account,entry,"two-way",action,[candidate],reviewed_remote=reviewed)
                final = apply_transition(state,recovery,account,entry,active,"two-way",
                                         lambda row:verify_live(load,account,state,writer,row))
                return dict(outcome="kept-remote",original_attempt=ident,remote_revision=tip,
                            generations=asdict(entry_generations(final)))
        require_plain_receiver(remote)
        attempt = str(uuid4())
        if action == "restore-candidate":
            proposed = restore_host(stage,store,report,local,attempt)
        else:
            require(all(report[side] is not None for side in ("base","candidate","remote")),
                    "address resolution needs the same existing host on both branches")
            chosen = str(ipaddress.ip_address(report["candidate"]["address"]))
            require(not report["changed_bound_credentials"] and
                    all(report[side]["credential"] == report["remote"]["credential"] for side in ("candidate","base")),
                    "review the changed credential binding separately")
            signature = record_at(stage,tip,store,host)[4]
            proposed = prepare_proposal(stage,tip,tip,store,host,chosen,attempt,signature)
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
                present = any(row["host_id"] == host for row in identity["hosts"])
                if present:
                    selected = stanza(identity,files.__getitem__,store,host)[4]
                    accepted = [record_at(stage,report["base_revision"],store,host)[4]]
                    if report["remote"] is not None:
                        accepted.append(record_at(stage,tip,store,host)[4])
                    require(selected in accepted,"local host changed during conflict resolution")
                else:
                    require(action == "restore-candidate" and identity == parse_json(git(stage,"show",tip+":"+IDENTITY)),
                            "local deleted selection differs from the reviewed branch")
            link = dict(action=action,original_attempt=ident,original_base=report["base_revision"],original_proposal=draft,review_id=review_id)
            reservation = reserve_edit(opened,recovery,account,ctx,target,host,tip,proposed,bundle,
                                       attempt,str(uuid4()),resolution=link)
        result = launch_reserved(load,account,recovery,ctx,expected,stage,remote,reservation)
        result["original_attempt"] = ident
        return result


def confirm_aliases(account,report,action):
    from store_login import age_installations
    from store_transfer_command import compatible_picker, operator_environment
    app = Path(__file__).resolve().parents[1]/"hostbook"
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    with private_workspace() as (folder,_):
        tools,env = operator_environment(folder,home)
        labels = subprocess.run(["/bin/bash",str(app),"_store_transfer_labels","create"],env=env,
                                stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,timeout=8,check=True).stdout.splitlines()
        require(labels,"missing installed picker version")
        locations = [(path,cellar.with_name("fzf") if cellar else None) for path,cellar in age_installations()]
        picker = compatible_picker(account,locations,labels[0].decode())
        (tools/"fzf").symlink_to(picker[0]/"fzf")
        command = ["/bin/bash",str(app),"_store_conflict_confirm",report["store_id"],report["ref"],
                   report["candidate_revision"],report["remote_revision"],action,",".join(report["required_aliases"]),
                   (report["candidate"] or {}).get("address","—"),(report["remote"] or {}).get("address","—")]
        approved = subprocess.run(command,env=env,stdout=subprocess.PIPE)
        return approved.stdout.decode().splitlines() if approved.returncode == 0 else []


def conflict_command(args):
    require(len(args) in (6,10),"provide store ID, four generations and original attempt, then optional decision, reviewed candidate/remote revisions and review ID")
    store,expected,ident = args[0],Generations(*(int(v) for v in args[1:5])),args[5]
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    from store_transfer import interruptible_preview
    with interruptible_preview():
        def load():
            return load_approved_account(home)
        result = compare(load,store,expected,ident) if len(args)==6 else resolve(load,store,expected,ident,*args[6:],confirm_prod=confirm_aliases)
    print(json.dumps(result))
    return 0 if result.get("outcome","accepted") in ("accepted","kept-remote","cancelled") else 75
