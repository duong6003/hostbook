"""Compensate a saved private destination while its shared source is still intact."""
import os

from store_checkout import check_contents, check_worktree, durable_file, fast_forward, install_sources, read_index, read_ref, rename_guarded, tree
from store_context import JSON_LIMIT, StoreContextError, open_directory, parse_json, require, require_private_directory, resolved_store, sync_local
from store_login import verified_age_tools
from store_read import local_inputs
from store_receive import IDENTITY, git, plain_git, prepare_adoption
from store_transfer_apply import advance, check_source, displaced_destination, transfer_gates
from store_transfer_credentials import read_mapping_end, unlock_end
from store_transfer_intent import encoded, local_descriptors, private_path, reopen_intent, token, wire
from store_transfer_owner import new_owner
from store_transfer_publish import current_receiver, saved_checkout
from store_transfer_resume import decode, restored_candidate, restored_plan
from store_vault import VaultUnlockCache


def inverse_proposal(plan, candidate, mapping):
    stage,dst = candidate.stage,plan.destination
    prior = tree(stage,dst.base,git)
    saved = tree(stage,candidate.proposed,git)
    require(set(prior) <= set(saved) and {p for p in saved if prior.get(p) != saved[p]} == set(candidate.changes),
            "private transfer candidate differs from its original changed paths")
    if not mapping.reused:
        require(all(row["credential"] != mapping.destination.name for row in plan.destination_hosts.values()),
                "another destination host requires the mapped credential")
    previous_identity = parse_json(git(stage,"cat-file","blob",prior[IDENTITY].decode()))
    saved_identity = parse_json(candidate.changes[IDENTITY])
    require(previous_identity == dst.identity and saved_identity["generation"] == previous_identity["generation"]+1,
            "original destination identity generation changed")
    identity = wire(dict(previous_identity,generation=saved_identity["generation"]+1))+b"\n"
    git(stage,"read-tree",dst.base)
    blob = git(stage,"hash-object","-w","--stdin",data=identity).strip()
    git(stage,"update-index","-z","--index-info",data=b"100644 "+blob+b"\t"+IDENTITY.encode()+b"\0")
    root = git(stage,"write-tree").strip().decode()
    inverse = git(stage,"-c","user.name=Hostbook","-c","user.email=hostbook@localhost","commit-tree",root,
                  "-p",candidate.proposed,"-m","Roll back private host transfer").strip().decode()
    git(stage,"update-ref","refs/hostbook/compensation",inverse)
    bundle = stage/"rollback.bundle"
    git(stage,"bundle","create",str(bundle),"refs/hostbook/compensation","^"+candidate.proposed)
    require(bundle.stat().st_size <= JSON_LIMIT,"private rollback exceeds recovery limit")
    return inverse,encoded(bundle.read_bytes()),saved,tree(stage,inverse,git)


def verify_credential(load, account, ref, end, protected, phrase):
    tools = verified_age_tools(account)
    cache = VaultUnlockCache(tools[0])
    require(end.record and read_mapping_end(account,ref) == end,"surviving transfer credential changed")
    identity = unlock_end(cache,tools,account,load,ref,lambda _:phrase)
    require(cache.open_secret(identity,end.record.ciphertext) == decode(protected["value"])
            and read_mapping_end(account,ref) == end and verified_age_tools(account) == tools,
            "surviving transfer credential cannot be verified")


def rollback_private(load, ident, password, code, secret, confirm):
    record,manifest = reopen_intent(load,ident,password)
    require(record["operation"] == "transfer" and record["checkpoint"] == "destination-confirmed" and not record["attempts"]
            and manifest["next_effect"] == "source-removal" and len(record["effects"]) == 1
            and record["effects"][0]["outcome"] == "observed","rollback requires an intact shared source and one saved private destination")
    plan = restored_plan(load(),record,manifest)
    require(plan.destination.context.kind == "private" and record["effects"][0]["store_id"] == plan.destination.context.store_id,
            "private rollback needs the original private destination")
    require(plan.decision in ("new","keep-both"),"replacement rollback needs separate review")
    with restored_candidate(plan,manifest) as (candidate,mapping):
        require(manifest["confirmed_destination"] == candidate.proposed,"private destination confirmation changed")
        source_pass,destination_pass = secret("source"),secret("destination")
        material = manifest["credentials"]
        verify_credential(load,plan.account,mapping.source,mapping.source_before,material["source"],source_pass)
        mapping.verify_saved(load,lambda _:destination_pass)
        inverse,bundle,before,after = inverse_proposal(plan,candidate,mapping)
        review = {"action":"rollback","transaction_id":ident,"source":dict(manifest["comparison"]["source"],revision=plan.source.base),
                  "destination":{"store_id":plan.destination.context.store_id,"host_id":plan.destination_key,"alias":plan.alias,
                                 "ref":plan.destination.ref,"revision":candidate.proposed,"inverse":inverse},
                  "credential":mapping.destination.name,"keep_credential":mapping.reused,
                  "required_aliases":manifest["comparison"].get("required_aliases",[])}
        if manifest["comparison"]["requires_typed_alias"] and not review["required_aliases"]:
            review["required_aliases"] = [review["source"]["name"]]
        answer = confirm(review)
        aliases = review["required_aliases"]
        require((([answer] if isinstance(answer,str) else answer) == aliases) if aliases else answer is True,
                "explicit rollback confirmation required")
        dest,source = plan.destination,plan.source
        effect = {"effect_id":token(),"store_id":dest.context.store_id,"intent_id":token(),"evidence_id":None,"outcome":"intended"}
        runtime = ("hostbook-transfer-v1",ident,effect["effect_id"])
        rollback = {"ref":dest.ref,"base":candidate.proposed,"tip":inverse,"bundle":bundle,"exchange":list(runtime)}
        snapshot = next(manifest["snapshots"][r["snapshot_id"]] for r in record["stores"] if r["role"] == "source")
        with transfer_gates(load,plan,record,manifest,recovery=True) as (tx,verify),local_descriptors(plan.account,dest) as (checkout,repo):
            def intact_source():
                require(load() == plan.account and "senders" not in os.listdir(tx),"private compensation has an unverified sender")
                check_source(plan.account,source,snapshot)
                current_receiver(plan,source.base)
                require(read_mapping_end(plan.account,mapping.source) == mapping.source_before,"surviving source credential changed")
            def unchanged():
                intact_source()
                saved_checkout(load,plan,candidate,mapping,record,manifest)
                displaced_destination(plan,candidate,record,manifest)
            unchanged()
            mode,logs = plain_git(repo,bare=False)
            original_index = read_index(repo)
            prepare_adoption(candidate.stage,original_index,before,candidate.proposed,inverse)
            expected_index = (candidate.stage/"index").read_bytes()
            owner,start = new_owner()
            previous = {"owner":record["owner"],"native_start":manifest["native_start"],"sequence":record["sequence"]}
            record,manifest,proof = advance(tx,plan.account,record,manifest,dict(record,owner=owner,checkpoint="rollback-pending",
                effects=record["effects"]+[effect]),dict(manifest,native_start=start,
                previous_owners=manifest.get("previous_owners",[])+[previous],rollback=rollback,next_effect="restore-private-destination"),
                password,code,observe=unchanged)
            def live():
                proof()
                verify(record)
                intact_source()
                with local_descriptors(plan.account,dest) as (working,actual):
                    require(os.path.samestat(os.fstat(working),os.fstat(checkout)) and os.path.samestat(os.fstat(actual),os.fstat(repo)),
                            "rollback destination binding changed")
            live()
            with private_path(repo,runtime[:-1]) as parent:
                os.mkdir(runtime[-1],0o700,dir_fd=parent)
                sync_local(parent)
            with open_directory(repo,runtime) as slot:
                require_private_directory(slot,"rollback exchange")
                replacements = sorted(path for path in candidate.changes if path in after)
                removals = sorted(set(before)-set(after))
                require(replacements == [IDENTITY] and set(replacements+removals) == set(candidate.changes),
                        "rollback changed paths outside the confirmed transfer")
                for i,path in enumerate(replacements):
                    durable_file(slot,str(i),git(candidate.stage,"cat-file","blob",after[path].decode()))
                sync_local(slot)
                def confirmed_slot():
                    with local_descriptors(plan.account,dest) as (_,named),open_directory(named,runtime) as actual:
                        require_private_directory(actual,"rollback exchange")
                        require(os.path.samestat(os.fstat(actual),os.fstat(slot)),"rollback exchange changed")
                def install(working,capture,paths,old,new,filemode):
                    for i,path in enumerate(removals):
                        live()
                        confirmed_slot()
                        parts = path.split("/")
                        name,retained = parts[-1],"removed-"+str(i)
                        with open_directory(working,parts[:-1]) as parent:
                            check_contents(parent,name,old[path],filemode)
                            rename_guarded(parent,name,capture,retained,exchange=False)
                            sync_local(parent)
                            sync_local(capture)
                            try:
                                check_contents(capture,retained,old[path],filemode)
                            except (OSError,StoreContextError):
                                try:
                                    rename_guarded(capture,retained,parent,name,exchange=False)
                                    sync_local(capture)
                                    sync_local(parent)
                                except FileExistsError:
                                    pass
                                raise StoreContextError("concurrent editor save needs transfer rollback recovery") from None
                    live()
                    confirmed_slot()
                    install_sources(working,capture,paths,old,new,filemode)
                fast_forward(checkout,repo,dest.ref,candidate.proposed,inverse,original_index,candidate.stage,before,after,
                             live,mode,logs,slot,replacements,install=install)
                def observed():
                    intact_source()
                    confirmed_slot()
                    with resolved_store(plan.account,dest.context.store_id,dest.context.generations) as (ctx,_,_,state):
                        require(ctx == dest.context and local_inputs(state,ctx.store_id) == dest.local,
                                "private compensation registration changed")
                    require(read_ref(repo,dest.ref) == inverse and read_index(repo) == expected_index,"private compensation Git state changed")
                    check_worktree(checkout,after,mode)
                    for i,path in enumerate(removals):
                        check_contents(slot,"removed-"+str(i),before[path],mode)
                    for i,path in enumerate(replacements):
                        check_contents(slot,str(i),before[path],mode)
                    require(read_mapping_end(plan.account,mapping.destination) == mapping.destination_before,
                            "restored private credential mapping changed")
                observed()
                verify_credential(load,plan.account,mapping.source,mapping.source_before,material["source"],source_pass)
                evidence = token()
                effect = dict(effect,outcome="observed",evidence_id=evidence)
                record,manifest,proof = advance(tx,plan.account,record,manifest,dict(record,checkpoint="rolled-back",
                    effects=[record["effects"][0],effect]),dict(manifest,next_effect="none",rollback=dict(rollback,
                    evidence_id=evidence,index=encoded(expected_index))),password,code,observe=observed)
                verify(record)
                proof()
                observed()
    return {"transaction_id":ident,"checkpoint":"rolled-back"}
