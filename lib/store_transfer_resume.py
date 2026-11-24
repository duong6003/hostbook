"""Reopen the original authenticated transfer after its verified owner exits."""
import base64
import os
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, replace

from store_checkout import durable_file, tree
from store_context import (Generations, StoreContextError, open_directory, parse_directory_binding, parse_json, read_file, require, resolved_store,
                           require_approval_file, require_private_directory, valid_id)
from store_journal import classify, private_json, publication_receipt, sender_receipt
from store_publish import observe_original_ref, recovery_root
from store_read import local_inputs
from store_receive import fetch_from_fd, git
from store_transfer import DestinationCandidate, ObservedStore, TransferPlan
from store_transfer_apply import advance, check_source, displaced_destination, payload_pin, save_destination, transfer_gates, write_once
from store_transfer_credentials import CredentialRecord, CredentialRef, StagedCredential, private_workspace, read_mapping_end
from store_transfer_intent import local_descriptors, reopen_intent, token, wire
from store_transfer_owner import new_owner
from store_transfer_publish import confirm_private_destination, current_receiver, publish_destination, publish_source, publishable_source, saved_checkout, sender_lease
from store_transfer_remove import finish_shared_source, remove_source


def decode(value):
    return base64.b64decode(value,validate=True)


def restored_plan(account, record, manifest):
    stores = {}
    for row in record["stores"]:
        snapshot = manifest["snapshots"][row["snapshot_id"]]
        expected = Generations(**snapshot["context"]["generations"])
        with resolved_store(account,row["store_id"],expected) as (ctx,_,_,state):
            require(wire(asdict(ctx)) == wire(snapshot["context"]),"original transfer registration changed")
            local = tuple(decode(v) if v is not None else None for v in snapshot["local"])
            require(local == local_inputs(state,ctx.store_id),"original transfer SSH settings changed")
        shared = ctx.kind == "shared"
        observed = ObservedStore(ctx,snapshot["ref"],snapshot["base"],
            parse_directory_binding(manifest["target"]) if shared else None,snapshot["base"] if shared else None,
            parse_json(decode(snapshot["identity"])),{p:decode(v) for p,v in snapshot["files"].items()},local)
        stores[row["role"]] = observed
    source,destination = stores["source"],stores["destination"]
    review = manifest["comparison"]
    key = review["source"]["host_id"]
    plan = TransferPlan(account,source,destination,key,review["destination"]["proposed_alias"],source.routes(),destination.routes(),
                        review.get("decision","new"),review.get("chosen_destination_id"))
    require(plan.host_keys() == record["hosts"] and plan.destination_key == review["destination"]["proposed_host_id"],
            "original transfer host keys changed")
    plan.verify_decision()
    return plan


def prior_end(account, store, protected, *, saved_new=False):
    ref = CredentialRef(store.context.store_id,store.context.generations,protected["name"])
    current = read_mapping_end(account,ref)
    vault = current.vault
    require(wire(vault.evidence) == wire(protected["vault_evidence"]) and vault.recipient == decode(protected["vault_pub"])
            and vault.password_envelope == decode(protected["vault_key"]) and vault.recovery_envelope == decode(protected["vault_recovery"]),
            "original transfer vault changed")
    old = None
    if "age" in protected:
        old = CredentialRecord(decode(protected["meta"]),decode(protected["age"]),
                               decode(protected["public_key"]) if protected["public_key"] else None)
    if saved_new:
        require(old is None and not protected["credential_evidence"],"destination creation has inconsistent prior evidence")
        return ref,replace(current,record=None,file_evidence=())
    require(current.record == old and wire(current.file_evidence) == wire(protected["credential_evidence"]),
            "original transfer credential changed")
    return ref,current


def restored_mapping(plan, manifest, changes):
    review,credentials = manifest["comparison"],manifest["credentials"]
    src,original = prior_end(plan.account,plan.source,credentials["source"])
    reused = review["credential_action"] == "reuse"
    require(reused or review["credential_action"] == "create","unknown saved transfer credential choice")
    dst,target = prior_end(plan.account,plan.destination,credentials["destination"],saved_new=not reused)
    if reused:
        result = target.record
    else:
        prefix = "secrets/"+dst.name+"."
        result = CredentialRecord(changes[prefix+"meta"],changes[prefix+"age"],changes.get(prefix+"pub"))
    require(result and original.record,"incomplete saved transfer credential")
    mapping = StagedCredential(result.metadata,result.ciphertext,result.public_key,src,dst,plan.account,original,target,reused)
    previous = prior_end(plan.account,plan.destination,credentials["prior_destination"]) if "prior_destination" in credentials else None
    return mapping,previous


def restore_stage(folder, directory, plan, store, bundle, tip, role):
    stage = folder/(role+".git")
    stage.mkdir(mode=0o700)
    git(stage,"init","-q","--bare","--template=")
    with local_descriptors(plan.account,store) as (_,repo):
        fetch_from_fd(stage,repo,store.ref,"refs/hostbook/current")
    archive = folder/(role+".bundle")
    durable_file(directory,archive.name,decode(bundle))
    require(git(stage,"bundle","list-heads",str(archive)).splitlines() == [tip.encode()+b" refs/hostbook/transfer"],
            "recovered Git bundle differs from its proposal")
    git(stage,"bundle","verify",str(archive))
    git(stage,"bundle","unbundle",str(archive))
    require(git(stage,"rev-list","--parents","-n","1",tip).split() == [tip.encode(),store.base.encode()],
            "recovered Git proposal has the wrong parent")
    return stage


@contextmanager
def restored_candidate(plan, manifest):
    changes = {path:decode(value) for path,value in manifest["changes"].items()}
    mapping,previous = restored_mapping(plan,manifest,changes)
    with private_workspace() as (folder,directory):
        dst = restore_stage(folder,directory,plan,plan.destination,manifest["bundle"],manifest["proposed"],"destination")
        src = restore_stage(folder,directory,plan,plan.source,manifest["source_bundle"],manifest["source_proposed"],"source")
        previous_tree = tree(dst,plan.destination.base,git)
        final = tree(dst,manifest["proposed"],git)
        require(set(previous_tree) <= set(final) and {p for p in final if previous_tree.get(p) != final[p]} == set(changes)
                and all(git(dst,"cat-file","blob",final[p].decode()) == data for p,data in changes.items()),
                "recovered destination tree differs from its reviewed changes")
        candidate = DestinationCandidate(dst,manifest["proposed"],changes,manifest["source_path"],plan,mapping,
            src,manifest["source_proposed"],decode(manifest["source_identity"]),manifest.get("replaced_project"),previous)
        yield candidate,mapping


def transfer_status(load, ident):
    require(valid_id(ident),"invalid transfer transaction ID")
    account = load()
    with recovery_root(account) as root,open_directory(root,("transactions",ident)) as tx:
        require_private_directory(tx,"transfer transaction")
        for _ in range(2):
            selector = private_json(tx,"current.json")
            _,released = classify(tx,account,ident,"transactions")
            if private_json(tx,"current.json") != selector:
                continue
            with open_directory(tx,("records",)) as history:
                name = str(selector["sequence"])+".json"
                record = private_json(history,name)
                tail = len(os.listdir(history))-selector["sequence"]
            require(record["operation"] == "transfer","unexpected recovery operation")
            with recovery_root(load()) as fresh,open_directory(fresh,("transactions",ident)) as named:
                require(load() == account and os.path.samestat(os.fstat(tx),os.fstat(named)),"original transfer directory changed")
                with open_directory(named,("records",)) as current:
                    if (classify(named,account,ident,"transactions")[1] == released and private_json(named,"current.json") == selector
                            and private_json(current,name) == record and len(os.listdir(current)) == selector["sequence"]+tail):
                        break
        else:
            raise StoreContextError("transfer status changed during observation")
        return {"transaction_id":ident,"checkpoint":record["checkpoint"],"sequence":record["sequence"],
                "stores":[{key:row[key] for key in ("store_id","role")} for row in record["stores"]],
                "hosts":record["hosts"],"effects":[{key:e[key] for key in ("effect_id","outcome")} for e in record["effects"]],
                "attempts":[{key:a[key] for key in ("attempt_id","sender_state","outcome")} for a in record["attempts"]],
                "fenced":not released,"unselected_revisions":tail}


def cancel_prepared(load, ident, password, code, confirm):
    record,manifest = reopen_intent(load,ident,password)
    require(record["operation"] == "transfer" and record["checkpoint"] == "prepared"
            and not record["attempts"] and not record["effects"] and manifest["next_effect"] == "destination-save",
            "transfer already has a data effect and needs reconciliation")
    plan = restored_plan(load(),record,manifest)
    details = {"action":"cancel","transaction_id":ident,"checkpoint":"prepared",
               "source":plan.source.context.store_id,"destination":plan.destination.context.store_id}
    require(confirm(details) is True,"explicit transfer cancellation confirmation required")
    with transfer_gates(load,plan,record,manifest,recovery=True) as (tx,verify):
        def unchanged():
            require(load() == plan.account,"transfer account approval changed")
            lease = os.stat("owner.lease",dir_fd=tx,follow_symlinks=False)
            require(manifest["lease"] == {"device":lease.st_dev,"inode":lease.st_ino},"transfer ownership lease changed")
            require("senders" not in os.listdir(tx),"unrecorded transfer sender requires reconciliation")
        unchanged()
        owner,start = new_owner()
        previous = {"owner":record["owner"],"native_start":manifest["native_start"],"sequence":record["sequence"]}
        current,_,proof = advance(tx,plan.account,record,manifest,dict(record,owner=owner,checkpoint="cancelled"),
            dict(manifest,native_start=start,next_effect="none",previous_owners=manifest.get("previous_owners",[])+[previous]),
            password,code,observe=unchanged)
        verify(current)
        proof()
    return {"transaction_id":ident,"checkpoint":"cancelled"}


@contextmanager
def original_sender(tx, plan, record, manifest, proposed):
    shared = plan.shared
    require(len(record["attempts"]) == 1,"missing original publication acceptance")
    attempt = record["attempts"][0]
    binding = manifest["publication"]
    require(attempt["store_id"] == binding["store"] == shared.context.store_id
            and attempt["connection_generation"] == shared.context.generations.connection
            and attempt["target_id"] == shared.context.target_id and wire(binding["target"]) == wire(asdict(shared.target))
            and (binding["attempt"],binding["ref"],binding["base"],binding["proposed"],binding["reservation"],binding["intent"]) ==
            (attempt["attempt_id"],shared.ref,shared.base,proposed,attempt["reservation_id"],attempt["intent_id"]),
            "original publication needs reconciliation")
    path = ("senders",attempt["attempt_id"])
    with open_directory(tx,path) as sender,sender_lease(sender,binding["lease"]) as lease:
        def check():
            with open_directory(tx,path) as named:
                require_private_directory(named,"transfer sender")
                require(os.path.samestat(os.fstat(sender),os.fstat(named)) and
                        os.path.samestat(os.fstat(lease),os.stat("lease",dir_fd=named,follow_symlinks=False)),
                        "original transfer sender binding changed")
                require_approval_file(lease,os.fstat(lease))
        check()
        yield sender,check


@contextmanager
def accepted_sender(tx, plan, record, manifest, proposed):
    attempt = record["attempts"][0]
    require(attempt["outcome"] == "accepted" and attempt["sender_state"] == "quiescent","original sender is unsettled")
    shared = plan.shared
    receipt = publication_receipt(record["stores"],attempt)
    if (plan.destination == shared and "decision" not in manifest["comparison"] and not manifest["comparison"]["matches"]
            and plan.decision == "new"):
        brief = {key:attempt[key] for key in ("attempt_id","evidence_id","sender_state","outcome")}
        if manifest["publication_evidence"] == brief:
            receipt = brief
    require(manifest["publication_evidence"] == receipt,"original acceptance receipt differs from the attempt")
    with original_sender(tx,plan,record,manifest,proposed) as (sender,pinned):
        def check():
            pinned()
            require(sender_receipt(sender) == receipt,"original acceptance receipt changed")
            current_receiver(plan,proposed)
        check()
        yield check


def settle_transfer(load, ident, password, code):
    record,manifest = reopen_intent(load,ident,password)
    phase = record["checkpoint"]
    require(record["operation"] == "transfer" and phase in ("destination-saved","source-removed-local")
            and len(record["attempts"]) == 1,"transfer has no original publication to reconcile")
    plan = restored_plan(load(),record,manifest)
    role = "destination" if plan.destination == plan.shared else "source"
    require(phase == ("destination-saved" if role == "destination" else "source-removed-local")
            and manifest["next_effect"] in ("send-"+role,"reconcile-"+role),"inconsistent original publication phase")
    proposed = manifest["proposed" if role == "destination" else "source_proposed"]
    attempt = record["attempts"][0]
    with transfer_gates(load,plan,record,manifest,recovery=True) as (tx,verify),original_sender(tx,plan,record,manifest,proposed) as (sender,pinned):
        pin = payload_pin(tx,plan.account,record)
        require(reopen_intent(load,ident,code,recovery=True) == (record,manifest)
                and payload_pin(tx,plan.account,record) == pin,"protected original transfer evidence changed")
        try:
            receipt = sender_receipt(sender)
        except FileNotFoundError:
            receipt = None
            require("observed-outcome.json" not in os.listdir(sender),"missing previous sender receipt")
        if receipt is not None:
            candidate = dict(attempt,outcome=receipt.get("outcome"),evidence_id=receipt.get("evidence_id"),sender_state="quiescent")
            require(candidate["outcome"] in ("accepted","not-accepted","uncertain") and valid_id(candidate["evidence_id"])
                    and receipt == publication_receipt(record["stores"],candidate),"sender receipt differs from admitted attempt")
        if attempt["outcome"] in ("accepted","not-accepted"):
            require(receipt == manifest["publication_evidence"] == publication_receipt(record["stores"],attempt),
                    "settled sender receipt changed")
        try:
            marker = read_file(sender,"send-started",64,require_approval_file)
        except FileNotFoundError:
            marker = None
        require(marker is None or marker == attempt["attempt_id"].encode(),"transfer sender start evidence changed")
        if marker is None:
            require(receipt is None or receipt["outcome"] == "not-accepted","started native sender proof is missing")
            result = "not-accepted"
        elif receipt is not None and receipt["outcome"] != "uncertain":
            result = receipt["outcome"]
        else:
            result = observe_original_ref(plan.account,plan.shared.target,plan.shared.ref,plan.shared.base,proposed)
        if receipt is None or result != receipt["outcome"]:
            settled = dict(attempt,outcome=result,sender_state="quiescent",evidence_id=token())
            evidence = publication_receipt(record["stores"],settled)
            require(receipt is None or (receipt["outcome"] == "uncertain" and result == "accepted"),"contradictory publication evidence")
            pinned()
            verify(record)
            require(payload_pin(tx,plan.account,record) == pin,"protected original transfer evidence changed")
            write_once(tx,sender,"outcome.json" if receipt is None else "observed-outcome.json",wire(evidence))
        else:
            settled,evidence = candidate,receipt
        def observed():
            pinned()
            require(sender_receipt(sender) == evidence,"original sender settlement changed")
        if settled != attempt:
            next_record,_,proof = advance(tx,plan.account,record,manifest,dict(record,attempts=[settled]),
                dict(manifest,next_effect="reconcile-"+role,publication_evidence=evidence),password,code,observe=observed)
            verify(next_record)
            proof()
        observed()
    return {"transaction_id":ident,"checkpoint":phase,"attempt_id":attempt["attempt_id"],"outcome":result}


def resume_confirmed(load, ident, password, code, read_passphrase, confirm):
    require(valid_id(ident),"invalid transfer transaction ID")
    record,manifest = reopen_intent(load,ident,password)
    phase = record["checkpoint"]
    require(record["operation"] == "transfer" and phase in ("prepared","destination-saved","destination-confirmed","source-removed-local","source-confirmed")
            and all(row["outcome"] == "observed" for row in record["effects"]),
            "transfer needs separate reconciliation before resuming")
    plan = restored_plan(load(),record,manifest)
    finished_source = phase in ("source-removed-local","source-confirmed")
    shared_role = "source" if plan.source == plan.shared else "destination"
    upgrade = phase in ("destination-saved","source-removed-local")
    prepared = phase == "prepared"
    require((prepared and not record["effects"] and not record["attempts"] and manifest["next_effect"] == "destination-save")
            or (not finished_source and len(record["effects"]) == 1 and
             ((not upgrade and manifest["next_effect"] == "source-removal") or
              (upgrade and shared_role == "destination" and manifest["next_effect"] == "reconcile-destination")))
            or (finished_source and shared_role == "source" and len(record["effects"]) == 2 and
                manifest["next_effect"] == ("reconcile-source" if upgrade else "complete")),
            "transfer has incomplete local evidence")
    public = dict(manifest["comparison"])
    public.update(plan.comparison())
    public.update(transaction_id=ident,action="resume",checkpoint=record["checkpoint"])
    aliases = public.get("required_aliases",[])
    if public["requires_typed_alias"] and not aliases:
        aliases = [public["source"]["name"]]
    public["required_aliases"] = aliases
    public.setdefault("retained_destination_groups",[])
    answer = confirm(public)
    require((([answer] if isinstance(answer,str) else answer) == aliases) if aliases else answer is True,
            "explicit transfer confirmation required")
    with restored_candidate(plan,manifest) as (candidate,mapping):
        phrase = read_passphrase(mapping.destination.store_id)
        def unlock(store):
            require(store == mapping.destination.store_id,"unexpected recovery vault")
            return phrase
        mapping.verify_decryption(load,unlock,saved=not prepared)
        with transfer_gates(load,plan,record,manifest,recovery=True) as (tx,verify),ExitStack() as held:
            accepted = None
            if not prepared and (finished_source or plan.destination == plan.shared):
                proposed = candidate.source_proposed if finished_source else candidate.proposed
                accepted = held.enter_context(accepted_sender(tx,plan,record,manifest,proposed))
            else:
                require(not record["attempts"],"unexpected publication before shared source removal")
            def current():
                if prepared:
                    require("senders" not in os.listdir(tx),"unrecorded transfer sender requires reconciliation")
                    for store in (plan.source,plan.destination):
                        row = next(r for r in record["stores"] if r["store_id"] == store.context.store_id)
                        check_source(plan.account,store,manifest["snapshots"][row["snapshot_id"]])
                    mapping.check_current(load)
                    current_receiver(plan,plan.shared.base,require_writes=True)
                    return
                checkout = publishable_source if finished_source else saved_checkout
                checkout(load,plan,candidate,mapping,record,manifest)
                displaced_destination(plan,candidate,record,manifest)
                if accepted is not None:
                    accepted()
            current()
            owner,start = new_owner()
            previous = {"owner":record["owner"],"native_start":manifest["native_start"],"sequence":record["sequence"]}
            successor = dict(record,owner=owner)
            updated = dict(manifest,native_start=start,previous_owners=manifest.get("previous_owners",[])+[previous])
            if upgrade:
                successor["checkpoint"] = shared_role+"-confirmed"
                updated["next_effect"] = "complete" if finished_source else "source-removal"
                updated["confirmed_"+shared_role] = proposed
            record,manifest,proof = advance(tx,plan.account,record,manifest,successor,updated,password,code,observe=current)
            verify(record)
            proof()
        args = load,ident,plan,candidate,mapping,unlock,password,code
        if finished_source:
            return finish_shared_source(*args)
        if prepared:
            save_destination(*args)
            confirm_destination = publish_destination if plan.destination == plan.shared else confirm_private_destination
            destination = confirm_destination(*args)
            if destination["checkpoint"] != "destination-confirmed":
                return destination
        result = remove_source(*args)
        if plan.source.context.kind == "shared":
            result = publish_source(*args)
            if result["checkpoint"] == "source-confirmed":
                result = finish_shared_source(*args)
        return result
