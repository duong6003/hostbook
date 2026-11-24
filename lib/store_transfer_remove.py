"""Remove a confirmed source and preserve displaced files until required publication settles."""
import os

from store_checkout import (check_contents, check_destinations, check_worktree, durable_file,
                            fast_forward, install_sources, read_index, read_ref, rename_guarded, tree)
from store_context import (StoreContextError, open_directory, require, require_private_directory,
                           sync_local)
from store_journal import sender_receipt
from store_receive import IDENTITY, git, plain_git, prepare_adoption
from store_transfer_apply import advance, check_source, displaced_destination, exchange_files, retire_exchange, transfer_gates
from store_transfer_intent import encoded, local_descriptors, private_path, reopen_intent, token
from store_transfer_publish import current_receiver, destination_checkout, removed_checkout


def finish_private_source(load, ident, plan, candidate, mapping, read_passphrase, password, code):
    require(plan.source.context.kind == "private","expected a private transfer source")
    return remove_source(load,ident,plan,candidate,mapping,read_passphrase,password,code)


def remove_source(load, ident, plan, candidate, mapping, read_passphrase, password, code):
    record,manifest = reopen_intent(load,ident,password)
    private = plan.source.context.kind == "private"
    require(record["checkpoint"] == "destination-confirmed" and manifest["confirmed_destination"] == candidate.proposed
            and manifest["next_effect"] == "source-removal" and len(record["effects"]) == 1
            and record["effects"][0]["outcome"] == "observed", "source removal requires a confirmed destination")
    attempts = record["attempts"]
    require((len(attempts) == 1 and attempts[0]["outcome"] == "accepted" and attempts[0]["sender_state"] == "quiescent"
             and attempts[0]["store_id"] == plan.destination.context.store_id) if private else not attempts,
            "destination acceptance is unsettled")
    require(candidate.source_proposed == manifest["source_proposed"]
            and encoded(candidate.source_identity) == manifest["source_identity"]
            and candidate.source_path == manifest["source_path"], "source removal proposal changed")
    account,src = plan.account,plan.source
    phrase = read_passphrase(mapping.destination.store_id)
    mapping.verify_saved(load,lambda _store:phrase)
    source = manifest["snapshots"][next(row["snapshot_id"] for row in record["stores"] if row["role"] == "source")]
    stage,removal,path = candidate.source_stage,candidate.source_proposed,candidate.source_path
    before,after = tree(stage,src.base,git),tree(stage,removal,git)
    require(git(stage,"rev-list","--parents","-n","1",removal).split() == [removal.encode(),src.base.encode()]
            and set(before)-set(after) == {path} and not set(after)-set(before)
            and {p for p in after if after[p] != before[p]} == {IDENTITY}
            and git(stage,"cat-file","blob",after[IDENTITY].decode()) == candidate.source_identity,
            "source removal differs from its confirmed bytes")
    effect = {"effect_id":token(),"store_id":src.context.store_id,"intent_id":token(),"evidence_id":None,"outcome":"intended"}
    with transfer_gates(load,plan,record,manifest) as (tx,verify),local_descriptors(account,src) as (checkout,repo):
        check_source(account,src,source)
        filemode,logs = plain_git(repo,bare=False)
        original_index = read_index(repo)
        prepare_adoption(stage,original_index,before,src.base,removal)
        proposed_index = (stage/"index").read_bytes()
        def destination():
            destination_checkout(load,plan,candidate,mapping,record,manifest)
            if private:
                with open_directory(tx,("senders",attempts[0]["attempt_id"])) as sender:
                    require(sender_receipt(sender) == manifest["publication_evidence"],
                            "original destination acceptance evidence changed")
                current_receiver(plan,candidate.proposed)
            else:
                current_receiver(plan,src.base,require_writes=True)
        destination()
        def before_removal():
            displaced_destination(plan,candidate,record,manifest)
            destination()
            check_source(account,src,source)
        runtime = ("hostbook-transfer-v1",ident,effect["effect_id"])
        record,manifest,proof = advance(tx,account,record,manifest,dict(record,effects=record["effects"]+[effect]),
            dict(manifest,next_effect="remove-source",source_runtime=list(runtime)),password,code,observe=before_removal)
        def live():
            displaced_destination(plan,candidate,record,manifest)
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
                require(source_observation() == observed,"source observation changed")
            files = [("source","verified-source",before[path]),("0","verified-0",before[IDENTITY])]
            binding,_ = exchange_files(slot,files,filemode)
            def capture_originals():
                unchanged()
                verify_slot()
                exchange_files(slot,files,filemode,binding)
            evidence = token()
            completed_effect = dict(effect,evidence_id=evidence,outcome="observed")
            record,manifest,proof = advance(tx,account,record,manifest,dict(record,checkpoint="source-removed-local",
                effects=[record["effects"][0],completed_effect]),dict(manifest,next_effect="confirm-private-source" if private else "source-publication",
                source_evidence=dict(observed,evidence_id=evidence,exchange=binding)),password,code,observe=capture_originals)
            live()
            if not private:
                unchanged()
                return {"transaction_id":ident,"checkpoint":"source-removed-local"}
            record,manifest,proof = advance(tx,account,record,manifest,dict(record,checkpoint="source-confirmed"),
                dict(manifest,next_effect="complete"),password,code,observe=unchanged)
            live()
            verify_slot()
            unchanged()
            retire_displaced(slot,before[path],before[IDENTITY],filemode,unchanged,verify_slot,binding)
        displaced_destination(plan,candidate,record,manifest,retire=True)
        record,manifest,proof = advance(tx,account,record,manifest,dict(record,checkpoint="completed"),
            dict(manifest,next_effect="none"),password,code,observe=unchanged)
        verify(record)
        proof()
        unchanged()
    return {"transaction_id":ident,"checkpoint":"completed"}


def retire_displaced(slot, source_blob, identity_blob, filemode, unchanged, verify_slot, binding):
    def current():
        unchanged()
        verify_slot()
    files = [("source","verified-source",source_blob),("0","verified-0",identity_blob)]
    retire_exchange(slot,files,filemode,binding,current)


def finish_shared_source(load, ident, plan, candidate, mapping, read_passphrase, password, code):
    record,manifest = reopen_intent(load,ident,password)
    src = plan.source
    require(src.context.kind == "shared" and record["checkpoint"] == "source-confirmed"
            and manifest["confirmed_source"] == candidate.source_proposed and manifest["next_effect"] == "complete"
            and len(record["attempts"]) == 1 and len(record["effects"]) == 2,"shared source publication is unconfirmed")
    attempt = record["attempts"][0]
    require(attempt["outcome"] == "accepted" and attempt["sender_state"] == "quiescent"
            and attempt["store_id"] == src.context.store_id,"shared source acceptance is unsettled")
    runtime = ("hostbook-transfer-v1",ident,record["effects"][1]["effect_id"])
    require(manifest["source_runtime"] == list(runtime),"shared source exchange binding changed")
    phrase = read_passphrase(mapping.destination.store_id)
    mapping.verify_saved(load,lambda _store:phrase)
    before = tree(candidate.source_stage,src.base,git)
    with transfer_gates(load,plan,record,manifest) as (tx,verify),local_descriptors(plan.account,src) as (_,repo):
        filemode,_ = plain_git(repo,bare=False)
        def current():
            removed_checkout(load,plan,candidate,mapping,record,manifest)
            current_receiver(plan,candidate.source_proposed)
            with open_directory(tx,("senders",attempt["attempt_id"])) as sender:
                require(sender_receipt(sender) == manifest["publication_evidence"],
                        "original source acceptance evidence changed")
        current()
        with open_directory(repo,runtime) as slot:
            def verify_slot():
                with local_descriptors(plan.account,src) as (_,named),open_directory(named,runtime) as actual:
                    require_private_directory(actual,"shared source exchange")
                    require(os.path.samestat(os.fstat(actual),os.fstat(slot)),"shared source exchange changed")
            verify_slot()
            original_binding = manifest["source_evidence"].get("exchange")
            if original_binding is None or (candidate.replaced_project and not manifest["destination_evidence"].get("exchange")):
                files = [("source","verified-source",before[candidate.source_path]),("0","verified-0",before[IDENTITY])]
                def originals():
                    current()
                    verify_slot()
                    src,_ = exchange_files(slot,files,filemode,original_binding,retiring=original_binding is not None)
                    dst = displaced_destination(plan,candidate,record,manifest)
                    return original_binding or src,manifest["destination_evidence"].get("exchange") or dst
                capture = originals()
                def unchanged():
                    require(originals() == capture,"original transfer retirement evidence changed")
                record,manifest,proof = advance(tx,plan.account,record,manifest,record,
                    dict(manifest,source_evidence=dict(manifest["source_evidence"],exchange=capture[0]),
                         destination_evidence=dict(manifest["destination_evidence"],exchange=capture[1])),
                    password,code,observe=unchanged)
                verify(record)
                proof()
            retire_displaced(slot,before[candidate.source_path],before[IDENTITY],filemode,current,verify_slot,
                             manifest["source_evidence"]["exchange"])
        displaced_destination(plan,candidate,record,manifest,retire=True)
        record,manifest,proof = advance(tx,plan.account,record,manifest,dict(record,checkpoint="completed"),
            dict(manifest,next_effect="none"),password,code,observe=current)
        verify(record)
        proof()
        current()
    return {"transaction_id":ident,"checkpoint":"completed"}
