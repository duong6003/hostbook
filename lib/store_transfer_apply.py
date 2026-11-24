"""Apply confirmed transfer effects under both store gates and the original ownership lease."""
from contextlib import ExitStack, contextmanager
import base64
import os

from store_checkout import (check_contents, check_destinations, check_worktree, durable_file, fast_forward,
                            install_sources, make_parents, read_index, read_ref, rename_guarded, tree)
from store_context import (JSON_LIMIT, StoreContextError, open_directory, path_parts, read_file, require, require_approval_file, require_private_directory,
                           resolved_store, staged_local_file, store_writer, sync_local, verify_state_directory)
from store_journal import classify, private_json, require_idle, transaction
from store_login import verified_age_tools
from store_publish import recovery_root, sync_named
from store_read import local_inputs
from store_receive import IDENTITY, git, plain_git, prepare_adoption
from store_transfer import observe_store, writable_destination
from store_transfer_intent import encoded, local_descriptors, private_path, reopen_intent, token, wire
from store_transfer_owner import owner_lease
from store_transfer_recovery import verify_backup
from store_vault import VaultUnlockCache


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


def advance(tx, account, record, manifest, next_record, next_manifest, password, code):
    selected(tx,account,record)
    seq = record["sequence"]+1
    ident = record["transaction_id"]
    new = dict(next_record,sequence=seq,previous_sequence=seq-1,payload_id=token())
    transaction(new,record)
    updated = dict(next_manifest,record=new)
    body = wire(updated)
    require(len(body) <= JSON_LIMIT, "transfer recovery manifest exceeds the supported size")
    with open_directory(tx,("recovery",str(record["recovery_generation"]))) as env, open_directory(tx,("payloads",)) as payloads:
        verify_backup(account,env,payloads,record["payload_id"],password,code,wire(manifest))
        recipient = read_file(env,"vault.pub",4096,require_approval_file)
        tools = verified_age_tools(account)
        sealed = VaultUnlockCache(tools[0]).seal_secret(recipient.strip(),body)
        require(len(sealed) <= JSON_LIMIT and verified_age_tools(account) == tools, "transfer recovery sealing failed")
        write_once(tx,payloads,new["payload_id"]+".age",sealed)
        verify_backup(account,env,payloads,new["payload_id"],password,code,body)
    selected(tx,account,record)
    with open_directory(tx,("records",)) as history:
        write_once(tx,history,f"{seq}.json",wire(new))
    try:
        with staged_local_file(tx,"selector",wire({"version":1,"sequence":seq,"transaction_id":ident})) as temp:
            sync_named(tx,temp)
            os.replace(temp,"current.json",src_dir_fd=tx,dst_dir_fd=tx)
            sync_local(tx)
        selected(tx,account,new)
    except (OSError,StoreContextError):
        raise StoreContextError("transfer "+ident+" selection requires reconciliation") from None
    return new,updated


@contextmanager
def transfer_gates(load, plan, record, manifest):
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
        lease = held.enter_context(owner_lease(tx,record["owner"],manifest))
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


def save_destination(load, ident, plan, candidate, mapping, credential_passphrase, password, code):
    record, manifest = reopen_intent(load,ident,password)
    require(record["checkpoint"] == "prepared" and not record["effects"] and not record["attempts"]
            and candidate.plan is plan and candidate.mapping is mapping and manifest["proposed"] == candidate.proposed
            and manifest["source_path"] == candidate.source_path and manifest["destination_ref"] == plan.destination.ref
            and manifest["destination_base"] == plan.destination.base and record["hosts"] == [
                {"store_id":s.context.store_id,"host_id":plan.host_id} for s in (plan.source,plan.destination)]
            and manifest["changes"] == {k:encoded(v) for k,v in candidate.changes.items()},
            "destination save requires the original confirmed preparation")
    require(mapping.destination.name == manifest["credentials"]["destination"]["name"] and
            mapping.source.name == manifest["credentials"]["source"]["name"], "transfer credential decision changed")
    for role,end in (("source",mapping.source_before),("destination",mapping.destination_before)):
        protected = manifest["credentials"][role]
        require(wire(end.file_evidence) == wire(protected["credential_evidence"])
                and wire(end.vault.evidence) == wire(protected["vault_evidence"]), "confirmed transfer credential changed")
    phrase = credential_passphrase(mapping.destination.store_id)
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
        check_destinations(checkout,candidate.changes,before,tx)
        with writable_destination(plan,transfer=record):
            record,manifest = advance(tx,account,record,manifest,dict(record,effects=[effect]),
                                      dict(manifest,next_effect="install-destination",destination_effect=effect_id),password,code)
        def live():
            verify(record)
            check_source(account,plan.source,snapshots["source"])
            require(observe_store(account,plan.source.context.store_id,plan.source.context.generations) == plan.source,
                    "transfer source changed")
            with local_descriptors(account,dst) as (bound,git_dir):
                require(os.path.samestat(os.fstat(bound),os.fstat(checkout)) and os.path.samestat(os.fstat(git_dir),os.fstat(repo)),
                        "destination filesystem binding changed")
            with writable_destination(plan,transfer=record):
                pass
        with private_path(tx,("exchange",effect_id)) as exchange:
            # Only the native sidecar requires replacement. Its original and proposal are
            # already encrypted. Keep the displaced inode addressable until it is verified.
            durable_file(exchange,"0",candidate.changes[IDENTITY])
            sync_local(exchange)
            def install(working, capture, _paths, previous, proposed, modes):
                for path,data in candidate.changes.items():
                    if path == IDENTITY:
                        continue
                    require(path not in previous, "transfer destination is occupied")
                    parts = path.split("/")
                    with make_parents(working,parts[:-1]) as parent:
                        durable_file(parent,parts[-1],data)
                        sync_local(parent)
                        check_contents(parent,parts[-1],proposed[path],modes)
                install_sources(working,capture,[IDENTITY],previous,proposed,modes)
            live()
            fast_forward(checkout,repo,dst.ref,dst.base,candidate.proposed,original_index,stage,before,after,live,
                         filemode,logs,exchange,[],install=install)
            check_contents(exchange,"0",before[IDENTITY],filemode)
            mapping.verify_saved(load,lambda _store:phrase)
            live()
            evidence = token()
            observed = dict(effect,evidence_id=evidence,outcome="observed")
            record,manifest = advance(tx,account,record,manifest,dict(record,checkpoint="destination-saved",effects=[observed]),
                                      dict(manifest,next_effect="destination-publication",destination_evidence={
                                          "evidence_id":evidence,"ref":dst.ref,"tip":candidate.proposed,
                                          "index":encoded(read_index(repo))}),password,code)
            verify(record)
            check_contents(exchange,"0",before[IDENTITY],filemode)
            os.unlink("0",dir_fd=exchange)
            sync_local(exchange)
    return {"transaction_id":ident,"checkpoint":"destination-saved"}
