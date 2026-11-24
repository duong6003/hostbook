"""Remove the exact private source only after verified, settled shared destination acceptance."""
import os

from store_checkout import (check_contents, check_destinations, check_worktree, direct_ref, durable_file,
                            fast_forward, install_sources, read_index, read_ref, rename_guarded, tree)
from store_context import (StoreContextError, open_directory, require, require_private_directory,
                           resolved_store, sync_local)
from store_journal import private_json
from store_receive import IDENTITY, git, plain_git, prepare_adoption, registered_target
from store_transfer_apply import advance, check_source, transfer_gates
from store_transfer_intent import encoded, local_descriptors, private_path, reopen_intent, token
from store_transfer_publish import destination_checkout


def finish_private_source(load, ident, plan, candidate, mapping, read_passphrase, password, code):
    record,manifest = reopen_intent(load,ident,password)
    require(record["checkpoint"] == "destination-confirmed" and manifest["confirmed_destination"] == candidate.proposed
            and len(record["attempts"]) == 1 and record["attempts"][0]["outcome"] == "accepted"
            and record["attempts"][0]["sender_state"] == "quiescent" and len(record["effects"]) == 1
            and record["effects"][0]["outcome"] == "observed", "source removal requires a confirmed destination")
    require(plan.source.context.kind == "private" and candidate.source_proposed == manifest["source_proposed"]
            and encoded(candidate.source_identity) == manifest["source_identity"]
            and candidate.source_path == manifest["source_path"], "private source removal proposal changed")
    account,src,dst = plan.account,plan.source,plan.destination
    phrase = read_passphrase(mapping.destination.store_id)
    mapping.verify_saved(load,lambda _store:phrase)
    source = manifest["snapshots"][next(row["snapshot_id"] for row in record["stores"] if row["role"] == "source")]
    stage,removal,path = candidate.source_stage,candidate.source_proposed,candidate.source_path
    before,after = tree(stage,src.base,git),tree(stage,removal,git)
    require(git(stage,"rev-list","--parents","-n","1",removal).split() == [removal.encode(),src.base.encode()]
            and set(before)-set(after) == {path} and not set(after)-set(before)
            and {p for p in after if after[p] != before[p]} == {IDENTITY}
            and git(stage,"cat-file","blob",after[IDENTITY].decode()) == candidate.source_identity,
            "private source removal differs from its confirmed bytes")
    effect = {"effect_id":token(),"store_id":src.context.store_id,"intent_id":token(),"evidence_id":None,"outcome":"intended"}
    with transfer_gates(load,plan,record,manifest) as (tx,verify),local_descriptors(account,src) as (checkout,repo):
        check_source(account,src,source)
        filemode,logs = plain_git(repo,bare=False)
        original_index = read_index(repo)
        prepare_adoption(stage,original_index,before,src.base,removal)
        proposed_index = (stage/"index").read_bytes()
        attempt = record["attempts"][0]
        def destination():
            destination_checkout(load,plan,candidate,mapping,record,manifest)
            with open_directory(tx,("senders",attempt["attempt_id"])) as sender:
                require(private_json(sender,"outcome.json") == manifest["publication_evidence"],
                        "original destination acceptance evidence changed")
            with resolved_store(account,dst.context.store_id,dst.context.generations) as (active,_,root,state):
                require(active == dst.context,"destination registration changed")
                with registered_target(account,active,root,state) as (target,remote):
                    require(target == dst.target and direct_ref(remote,dst.ref) == candidate.proposed,
                            "confirmed destination is no longer current")
        destination()
        def before_removal():
            destination()
            check_source(account,src,source)
        runtime = ("hostbook-transfer-v1",ident,effect["effect_id"])
        record,manifest,proof = advance(tx,account,record,manifest,dict(record,effects=record["effects"]+[effect]),
            dict(manifest,next_effect="remove-private-source",source_runtime=list(runtime)),password,code,observe=before_removal)
        def live():
            proof()
            verify(record)
            destination()
            with local_descriptors(account,src) as (working,actual):
                require(os.path.samestat(os.fstat(working),os.fstat(checkout)) and os.path.samestat(os.fstat(actual),os.fstat(repo)),
                        "source checkout binding changed")
        live()
        with private_path(repo,runtime[:-1]) as parent:
            os.mkdir(runtime[-1],0o700,dir_fd=parent)
            sync_local(parent)
        with open_directory(repo,runtime) as slot:
            require_private_directory(slot,"private source exchange")
            check_destinations(checkout,(IDENTITY,path),before,slot)
            durable_file(slot,"0",candidate.source_identity)
            sync_local(slot)
            def verify_slot():
                with open_directory(repo,runtime) as actual:
                    require_private_directory(actual,"private source exchange")
                    require(os.path.samestat(os.fstat(actual),os.fstat(slot)),"private source exchange changed")
            def install(working,exchange,_paths,prior,updated,modes):
                live()
                verify_slot()
                parts = path.split("/")
                with open_directory(working,parts[:-1]) as project:
                    name = parts[-1]
                    check_contents(project,name,prior[path],modes)
                    rename_guarded(project,name,exchange,"source",exchange=False)
                    sync_local(project)
                    sync_local(exchange)
                    try:
                        check_contents(exchange,"source",prior[path],modes)
                    except (OSError,StoreContextError):
                        # Put the actual displaced editor save back only if the source remains free.
                        try:
                            rename_guarded(exchange,"source",project,name,exchange=False)
                            sync_local(project)
                            sync_local(exchange)
                        except FileExistsError:
                            pass
                        raise StoreContextError("source editor changes require transfer reconciliation") from None
                live()
                install_sources(working,exchange,[IDENTITY],prior,updated,modes)
            fast_forward(checkout,repo,src.ref,src.base,removal,original_index,stage,before,after,live,filemode,logs,slot,[],install=install)
            def source_observation():
                destination()
                with local_descriptors(account,src) as (working,actual):
                    tip,index = read_ref(actual,src.ref),read_index(actual)
                    require(tip == removal and index == proposed_index,"saved source Git state changed")
                    check_worktree(working,after,filemode)
                    return {"ref":src.ref,"tip":tip,"index":encoded(index)}
            observed = source_observation()
            def unchanged():
                require(source_observation() == observed,"private source observation changed")
            evidence = token()
            completed_effect = dict(effect,evidence_id=evidence,outcome="observed")
            record,manifest,proof = advance(tx,account,record,manifest,dict(record,checkpoint="source-removed-local",
                effects=[record["effects"][0],completed_effect]),dict(manifest,next_effect="confirm-private-source",
                source_evidence=dict(observed,evidence_id=evidence)),password,code,observe=unchanged)
            live()
            record,manifest,proof = advance(tx,account,record,manifest,dict(record,checkpoint="source-confirmed"),
                dict(manifest,next_effect="complete"),password,code,observe=unchanged)
            live()
            verify_slot()
            unchanged()
            # Retire only files moved into this private directory by this transaction.
            # Keep both actual displaced files discoverable on any ambiguous comparison.
            retired = []
            for name,expected in (("source",before[path]),("0",before[IDENTITY])):
                check_contents(slot,name,expected,filemode)
                captured = os.stat(name,dir_fd=slot,follow_symlinks=False)
                retiring = "verified-"+name
                rename_guarded(slot,name,slot,retiring,exchange=False)
                sync_local(slot)
                require(os.path.samestat(captured,os.stat(retiring,dir_fd=slot,follow_symlinks=False)),
                        "displaced private source changed before retirement")
                check_contents(slot,retiring,expected,filemode)
                retired.append((retiring,expected))
            unchanged()
            verify_slot()
            for name,expected in retired:
                check_contents(slot,name,expected,filemode)
                os.unlink(name,dir_fd=slot)
                sync_local(slot)
        record,manifest,proof = advance(tx,account,record,manifest,dict(record,checkpoint="completed"),
            dict(manifest,next_effect="none"),password,code,observe=unchanged)
        verify(record)
        proof()
        unchanged()
    return {"transaction_id":ident,"checkpoint":"completed"}
