"""Apply confirmed transfer effects under both store gates and the original ownership lease."""
from contextlib import ExitStack, contextmanager
import base64
import os

from store_checkout import (check_contents, check_destinations, check_worktree, durable_file, fast_forward,
                            install_sources, make_parents, read_index, read_ref, rename_guarded, tree)
from store_context import (JSON_LIMIT, StoreContextError, open_directory, path_parts, read_file, require, require_approval_file, require_private_directory,
                           resolved_store, staged_local_file, store_writer, sync_local, verify_state_directory)
from store_journal import classify, completion_receipt, private_json, require_idle, rollback_receipt, transaction
from store_login import verified_age_tools
from store_publish import recovery_root, sync_named
from store_read import local_inputs
from store_receive import IDENTITY, git, plain_git, prepare_adoption
from store_transfer import observe_store, writable_shared
from store_transfer_intent import encoded, local_descriptors, private_path, reopen_intent, token, wire
from store_transfer_owner import owner_lease
from store_transfer_recovery import capture, verify_backup
from store_vault import VaultUnlockCache, file_evidence


def selected(tx, account, record):
    classify(tx,account,record["transaction_id"],"transactions")
    selector = private_json(tx,"current.json")
    seq = record["sequence"]
    with open_directory(tx,("records",)) as history:
        require(selector["sequence"] == seq == len(os.listdir(history))
                and private_json(history,f"{seq}.json") == record, "transfer selection changed")


def write_once(tx, dest, name, data):
    with staged_local_file(tx,"transfer",data) as temp:
        sync_named(tx,temp)
        rename_guarded(tx,temp,dest,name,exchange=False)
        sync_local(dest)


def payload_pin(tx, account, record):
    with recovery_root(account) as root,open_directory(root,("transactions",record["transaction_id"])) as named:
        require_private_directory(named,"transfer transaction")
        require(os.path.samestat(os.fstat(tx),os.fstat(named)), "selected transfer directory changed")
        with open_directory(named,("recovery",str(record["recovery_generation"]))) as env,open_directory(named,("payloads",)) as payloads:
            files,evidence = capture(env,payloads,record["payload_id"],("vault.key.age","vault.recovery.age"))
            # Appending another immutable generation legitimately changes the payload directory's timestamp.
            return files, tuple(st[:2] for st in evidence[:2]), evidence[2:]


def record_pin(tx, record):
    stamps = []
    def check(fd,info):
        require_approval_file(fd,info)
        stamps.append(file_evidence(info))
    with open_directory(tx,("records",)) as history:
        require_private_directory(history,"transfer history")
        data = read_file(history,str(record["sequence"])+".json",JSON_LIMIT,check)
        require(data == wire(record),"selected transfer record changed")
        return data,stamps


def advance(tx, account, record, manifest, next_record, next_manifest, password, code, *, observe=None):
    selected(tx,account,record)
    old_record = record_pin(tx,record)
    seq = record["sequence"]+1
    ident = record["transaction_id"]
    new = dict(next_record,sequence=seq,previous_sequence=seq-1,payload_id=token())
    transaction(new,record)
    updated = dict(next_manifest,record=new)
    body = wire(updated)
    require(len(body) <= JSON_LIMIT, "transfer recovery manifest exceeds the supported size")
    with open_directory(tx,("recovery",str(record["recovery_generation"]))) as env, open_directory(tx,("payloads",)) as payloads:
        old_pin = payload_pin(tx,account,record)
        verify_backup(account,env,payloads,record["payload_id"],password,code,wire(manifest))
        recipient = read_file(env,"vault.pub",4096,require_approval_file)
        tools = verified_age_tools(account)
        sealed = VaultUnlockCache(tools[0]).seal_secret(recipient.strip(),body)
        require(len(sealed) <= JSON_LIMIT and verified_age_tools(account) == tools, "transfer recovery sealing failed")
        write_once(tx,payloads,new["payload_id"]+".age",sealed)
        new_pin = payload_pin(tx,account,new)
        verify_backup(account,env,payloads,new["payload_id"],password,code,body)
    def verified_generations():
        require(record_pin(tx,record) == old_record and payload_pin(tx,account,record) == old_pin
                and payload_pin(tx,account,new) == new_pin,
                "verified transfer recovery generation changed")
        if observe:
            observe()
    selected(tx,account,record)
    verified_generations()
    if new["checkpoint"] == "completed":
        # Preserve the verified old forward receipt without rewriting historical sender evidence.
        attempt = new["attempts"][0]
        brief = {key:attempt[key] for key in ("attempt_id","evidence_id","sender_state","outcome")}
        legacy = ("decision" not in manifest["comparison"] and not manifest["comparison"]["matches"]
                  and manifest.get("previous_owners") and any(row["role"] == "destination" and
                  row["store_id"] == attempt["store_id"] for row in new["stores"])
                  and manifest["publication_evidence"] == brief)
        write_once(tx,tx,"completion.json",wire(completion_receipt(new,1 if legacy else 2)))
    if new["checkpoint"] == "rolled-back":
        write_once(tx,tx,"rollback.json",wire(rollback_receipt(new)))
    with open_directory(tx,("records",)) as history:
        write_once(tx,history,f"{seq}.json",wire(new))
    try:
        saved_record = record_pin(tx,new)
        with staged_local_file(tx,"selector",wire({"version":1,"sequence":seq,"transaction_id":ident})) as temp:
            sync_named(tx,temp)
            verified_generations()
            require(private_json(tx,"current.json") == {key:record[key] for key in ("version","sequence","transaction_id")},
                    "prior transfer selector changed")
            os.replace(temp,"current.json",src_dir_fd=tx,dst_dir_fd=tx)
            sync_local(tx)
        selected(tx,account,new)
    except (OSError,StoreContextError):
        raise StoreContextError("transfer "+ident+" selection requires reconciliation") from None
    def verify_new():
        require(payload_pin(tx,account,new) == new_pin and record_pin(tx,new) == saved_record,
                "selected transfer recovery generation changed")
    verify_new()
    return new,updated,verify_new


@contextmanager
def transfer_gates(load, plan, record, manifest, *, recovery=False):
    account = plan.account
    ident = record["transaction_id"]
    with recovery_root(account) as root,open_directory(root,("transactions",ident)) as tx, ExitStack() as held:
        require_private_directory(tx,"transfer transaction")
        selected(tx,account,record)
        writers = {}
        for store in sorted((plan.source,plan.destination),key=lambda item:item.context.store_id):
            ctx = store.context
            actual, active, _, writer = held.enter_context(store_writer(load,ctx.store_id,ctx.generations))
            require((account,ctx) == (actual,active),"transfer store writer changed")
            writers[ctx.store_id] = writer
        lease = held.enter_context(owner_lease(tx,record["owner"],manifest,recovery=recovery))
        def verify(current):
            require(load() == account, "transfer account changed")
            with recovery_root(account) as fresh, open_directory(fresh,("transactions",ident)) as named:
                require(os.path.samestat(os.fstat(tx),os.fstat(named)) and
                        os.path.samestat(os.fstat(lease),os.stat("owner.lease",dir_fd=named,follow_symlinks=False)),
                        "transfer transaction or owner lease changed")
                selected(named,account,current)
                for store in (plan.source,plan.destination):
                    ctx = store.context
                    with resolved_store(account,ctx.store_id,ctx.generations) as (active,_,_,state):
                        require(active == ctx and local_inputs(state,ctx.store_id) == store.local,
                                "transfer store or SSH settings changed")
                        verify_state_directory(state,path_parts(account.state.path,ctx.writer_lock),writers[ctx.store_id],
                                               "transfer store writer changed")
                        require_idle(fresh,account,ctx.store_id,transfer=current)
        verify(record)
        yield tx,verify


def check_source(account, source, snapshot):
    with local_descriptors(account,source) as (checkout,repo):
        modes,_ = plain_git(repo,bare=False)
        require(read_ref(repo,source.ref) == snapshot["base"] and encoded(read_index(repo)) == snapshot["index"],
                "original transfer source Git state changed")
        check_worktree(checkout,{k:v.encode() for k,v in snapshot["tracked"].items()},modes)


def inode(info):
    return [info.st_dev,info.st_ino]


def exchange_files(slot, files, filemode, binding=None, *, retiring=False):
    """Check the original or renamed inode. Only an already confirmed cleanup can consume it."""
    require_private_directory(slot,"transfer file exchange")
    captured = {"directory":inode(os.fstat(slot)),"files":{}}
    if binding is not None:
        require(captured["directory"] == binding["directory"] and set(binding["files"]) == {old for old,_,_ in files},
                "transfer exchange binding changed")
    remaining = []
    for old,new,expected in files:
        found = []
        for name in (old,new):
            try:
                info = os.stat(name,dir_fd=slot,follow_symlinks=False)
            except FileNotFoundError:
                continue
            check_contents(slot,name,expected,filemode)
            require(os.path.samestat(info,os.stat(name,dir_fd=slot,follow_symlinks=False)),"displaced file changed")
            found.append((name,inode(info)))
        require(len(found) <= 1 and (retiring and binding is not None or found and found[0][0] == old),
                "transfer displaced files need reconciliation")
        if found:
            name,stamp = found[0]
            require(binding is None or binding["files"][old] == stamp,"original displaced inode changed")
            captured["files"][old] = stamp
            remaining.append((name,new,expected,stamp))
    return captured,remaining


def retire_exchange(slot, files, filemode, binding, current):
    captured,remaining = exchange_files(slot,files,filemode,binding,retiring=True)
    # Older receipts without inode evidence can begin retirement only with all originals still present.
    binding = binding or captured
    for name,new,_,_ in remaining:
        current()
        if name != new:
            rename_guarded(slot,name,slot,new,exchange=False)
            sync_local(slot)
    _,remaining = exchange_files(slot,files,filemode,binding,retiring=True)
    for name,new,expected,stamp in remaining:
        current()
        require(name == new and inode(os.stat(name,dir_fd=slot,follow_symlinks=False)) == stamp,"displaced inode changed")
        check_contents(slot,name,expected,filemode)
        os.unlink(name,dir_fd=slot)
        sync_local(slot)
    _,left = exchange_files(slot,files,filemode,binding,retiring=True)
    require(not left,"displaced files still need reconciliation")


def displaced_destination(plan, candidate, record, manifest, *, retire=False):
    project = candidate.replaced_project
    if not project:
        return
    runtime = ("hostbook-transfer-v1",record["transaction_id"],record["effects"][0]["effect_id"])
    require(manifest["replaced_project"] == project and manifest["destination_runtime"] == list(runtime),
            "reviewed destination exchange changed")
    original = tree(candidate.stage,plan.destination.base,git)[project]
    with local_descriptors(plan.account,plan.destination) as (_,repo),open_directory(repo,runtime) as slot:
        require_private_directory(slot,"transfer destination exchange")
        filemode,_ = plain_git(repo,bare=False)
        files = [("1","verified-project",original)]
        binding = manifest.get("destination_evidence",{}).get("exchange")
        retiring = record["checkpoint"] in ("source-confirmed","completed")
        if retire:
            require(retiring,"destination retirement requires source confirmation")
            def current():
                with local_descriptors(plan.account,plan.destination) as (_,named),open_directory(named,runtime) as actual:
                    require_private_directory(actual,"transfer destination exchange")
                    require(os.path.samestat(os.fstat(slot),os.fstat(actual)),"transfer destination exchange changed")
            retire_exchange(slot,files,filemode,binding,current)
        else:
            observed,_ = exchange_files(slot,files,filemode,binding,retiring=retiring)
            return observed


def save_destination(load, ident, plan, candidate, mapping, credential_passphrase, password, code):
    record, manifest = reopen_intent(load,ident,password)
    require(record["checkpoint"] == "prepared" and not record["effects"] and not record["attempts"]
            and candidate.plan is plan and candidate.mapping is mapping and manifest["proposed"] == candidate.proposed
            and manifest["source_path"] == candidate.source_path and manifest["destination_ref"] == plan.destination.ref
            and manifest["destination_base"] == plan.destination.base and record["hosts"] == plan.host_keys()
            and manifest.get("replaced_project") == candidate.replaced_project
            and manifest["changes"] == {k:encoded(v) for k,v in candidate.changes.items()},
            "destination save requires the original confirmed preparation")
    require(mapping.destination.name == manifest["credentials"]["destination"]["name"] and
            mapping.source.name == manifest["credentials"]["source"]["name"], "transfer credential decision changed")
    for role,end in (("source",mapping.source_before),("destination",mapping.destination_before)):
        protected = manifest["credentials"][role]
        require(wire(end.file_evidence) == wire(protected["credential_evidence"])
                and wire(end.vault.evidence) == wire(protected["vault_evidence"]), "confirmed transfer credential changed")
    phrase = credential_passphrase(mapping.destination.store_id)
    mapping.verify_decryption(load,lambda _store:phrase,saved=False)
    account, dst = plan.account, plan.destination
    stage = candidate.stage
    before, after = tree(stage,dst.base,git), tree(stage,candidate.proposed,git)
    require(set(before) <= set(after) and set(candidate.changes) == {p for p in after if before.get(p) != after[p]}
            and all(git(stage,"cat-file","blob",after[p].decode()) == data for p,data in candidate.changes.items()),
            "transfer proposal differs from the confirmed bytes")
    snapshots = {item["role"]:manifest["snapshots"][item["snapshot_id"]] for item in record["stores"]}
    check_source(account,plan.source,snapshots["source"])
    original_index = base64.b64decode(snapshots["destination"]["index"],validate=True)
    prepare_adoption(stage,original_index,before,dst.base,candidate.proposed)
    effect_id = token()
    effect = {"effect_id":effect_id,"intent_id":token(),"store_id":dst.context.store_id,"evidence_id":None,"outcome":"intended"}
    with transfer_gates(load,plan,record,manifest) as (tx,verify), local_descriptors(account,dst) as (checkout,repo):
        plan.revalidate(load,transfer=record)
        mapping.check_current(load)
        filemode,logs = plain_git(repo,bare=False)
        require(read_ref(repo,dst.ref) == dst.base and read_index(repo) == original_index, "destination Git base changed")
        check_worktree(checkout,before,filemode)
        exchange_root = repo if candidate.replaced_project else tx
        runtime = (("hostbook-transfer-v1",ident,effect_id) if candidate.replaced_project else ("exchange",effect_id))
        check_destinations(checkout,candidate.changes,before,exchange_root)
        with writable_shared(plan,transfer=record):
            record,manifest,proof = advance(tx,account,record,manifest,dict(record,effects=[effect]),
                                      dict(manifest,next_effect="install-destination",destination_effect=effect_id,destination_runtime=list(runtime)),password,code)
        def live():
            verify(record)
            proof()
            check_source(account,plan.source,snapshots["source"])
            require(observe_store(account,plan.source.context.store_id,plan.source.context.generations,transfer=record) == plan.source,
                    "transfer source changed")
            with local_descriptors(account,dst) as (bound,git_dir):
                require(os.path.samestat(os.fstat(bound),os.fstat(checkout)) and os.path.samestat(os.fstat(git_dir),os.fstat(repo)),
                        "destination filesystem binding changed")
            with writable_shared(plan,transfer=record):
                pass
        with private_path(exchange_root,runtime[:-1]) as parent:
            os.mkdir(runtime[-1],0o700,dir_fd=parent)
            sync_local(parent)
        with open_directory(exchange_root,runtime) as exchange:
            require_private_directory(exchange,"transfer destination exchange")
            replacements = [IDENTITY]
            if candidate.replaced_project:
                replacements.append(candidate.replaced_project)
            for index,path in enumerate(replacements):
                durable_file(exchange,str(index),candidate.changes[path])
            sync_local(exchange)
            def install(working, capture, _paths, previous, proposed, modes):
                for path,data in candidate.changes.items():
                    if path in replacements:
                        continue
                    require(path not in previous, "transfer destination is occupied")
                    parts = path.split("/")
                    with make_parents(working,parts[:-1]) as parent:
                        proof()
                        durable_file(parent,parts[-1],data)
                        sync_local(parent)
                        check_contents(parent,parts[-1],proposed[path],modes)
                proof()
                install_sources(working,capture,replacements,previous,proposed,modes)
            live()
            fast_forward(checkout,repo,dst.ref,dst.base,candidate.proposed,original_index,stage,before,after,live,
                         filemode,logs,exchange,[],install=install)
            check_contents(exchange,"0",before[IDENTITY],filemode)
            mapping.verify_saved(load,lambda _store:phrase)
            live()
            expected_index = (stage/"index").read_bytes()
            def destination_observation():
                require(load() == account,"transfer account changed")
                with resolved_store(account,dst.context.store_id,dst.context.generations) as (ctx,_,_,state):
                    require(ctx == dst.context and local_inputs(state,ctx.store_id) == dst.local,
                            "selected destination settings changed")
                with local_descriptors(account,dst) as (working,actual):
                    tip,index = read_ref(actual,dst.ref),read_index(actual)
                    require(tip == candidate.proposed and index == expected_index, "saved destination Git state changed")
                    check_worktree(working,after,filemode)
                mapping.check_current(load,saved=True)
                exchange = displaced_destination(plan,candidate,record,manifest)
                return {"ref":dst.ref,"tip":tip,"index":encoded(index),"exchange":exchange}
            observation = destination_observation()
            def unchanged():
                require(destination_observation() == observation,"saved destination observation changed")
            evidence = token()
            observed = dict(effect,evidence_id=evidence,outcome="observed")
            record,manifest,proof = advance(tx,account,record,manifest,dict(record,checkpoint="destination-saved",effects=[observed]),
                                      dict(manifest,next_effect="destination-publication" if dst.context.kind == "shared" else "confirm-private-destination",
                                           destination_evidence=dict(observation,evidence_id=evidence)),password,code,observe=unchanged)
            verify(record)
            proof()
            unchanged()
            check_contents(exchange,"0",before[IDENTITY],filemode)
            os.unlink("0",dir_fd=exchange)
            sync_local(exchange)
    return {"transaction_id":ident,"checkpoint":"destination-saved"}
