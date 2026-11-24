"""Compensate a saved private destination while its shared source is still intact."""
import os

from store_checkout import check_contents, check_worktree, durable_file, fast_forward, install_sources, read_index, read_ref, rename_guarded, tree
from store_context import JSON_LIMIT, StoreContextError, open_directory, parse_json, require, require_private_directory, resolved_store, sync_local
from store_login import verified_age_tools
from store_read import local_inputs
from store_receive import IDENTITY, git, plain_git, prepare_adoption
from store_transfer_apply import adopt_terminal, advance, check_source, displaced_destination, inode, transfer_gates
from store_transfer_credentials import read_mapping_end, unlock_end
from store_transfer_intent import encoded, local_descriptors, private_path, reopen_intent, require_no_senders, token, wire
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


def inverse_paths(candidate, before, after):
    replacements = sorted(path for path in candidate.changes if path in after)
    removals = sorted(set(before)-set(after))
    require(set(replacements) == ({IDENTITY,candidate.replaced_project} if candidate.replaced_project else {IDENTITY})
            and set(replacements+removals) == set(candidate.changes),"private rollback paths changed")
    return replacements,removals


def source_intact(load, plan, record, manifest, mapping, tx):
    require(load() == plan.account,"private compensation account changed")
    require_no_senders(tx)
    original = next(r for r in record["stores"] if r["role"] == "source")
    check_source(plan.account,plan.source,manifest["snapshots"][original["snapshot_id"]])
    current_receiver(plan,plan.source.base)
    require(read_mapping_end(plan.account,mapping.source) == mapping.source_before,"surviving source credential changed")


def recorded_inverse(candidate, plan, rollback):
    stage = candidate.stage
    require((rollback["ref"],rollback["base"]) == (plan.destination.ref,candidate.proposed),"private inverse binding changed")
    archive = stage/"rollback.bundle"
    directory = os.open(stage,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        durable_file(directory,archive.name,decode(rollback["bundle"]))
    finally:
        os.close(directory)
    require(git(stage,"bundle","list-heads",str(archive)).splitlines() == [rollback["tip"].encode()+b" refs/hostbook/compensation"],
            "private inverse bundle changed")
    git(stage,"bundle","verify",str(archive))
    git(stage,"bundle","unbundle",str(archive))
    tip = rollback["tip"]
    require(git(stage,"rev-list","--parents","-n","1",tip).split() == [tip.encode(),candidate.proposed.encode()],
            "private inverse parent changed")
    prior,after = tree(stage,plan.destination.base,git),tree(stage,tip,git)
    require(dict(after,**{IDENTITY:prior[IDENTITY]}) == prior and
            parse_json(git(stage,"cat-file","blob",after[IDENTITY].decode())) == dict(plan.destination.identity,
            generation=parse_json(candidate.changes[IDENTITY])["generation"]+1),"private inverse tree changed")
    return tree(stage,candidate.proposed,git),after


def finish_inverse(load, plan, candidate, mapping, record, manifest, tx, verify, before, after, phrases, password, code):
    rollback,dest = manifest["rollback"],plan.destination
    runtime = ("hostbook-transfer-v1",record["transaction_id"],record["effects"][1]["effect_id"])
    require(rollback["exchange"] == list(runtime) and "binding" in rollback,"partial private rollback needs reconciliation")
    binding = rollback["binding"]
    replacements,removals = inverse_paths(candidate,before,after)
    files = [(str(i),p) for i,p in enumerate(replacements)]+[("removed-"+str(i),p) for i,p in enumerate(removals)]
    with local_descriptors(plan.account,dest) as (checkout,repo),open_directory(repo,runtime) as slot:
        mode,_ = plain_git(repo,bare=False)
        def observed():
            source_intact(load,plan,record,manifest,mapping,tx)
            with resolved_store(plan.account,dest.context.store_id,dest.context.generations) as (ctx,_,_,state):
                require(ctx == dest.context and local_inputs(state,ctx.store_id) == dest.local,"private compensation registration changed")
            with local_descriptors(plan.account,dest) as (working,named),open_directory(named,runtime) as actual:
                require_private_directory(actual,"private rollback exchange")
                require(inode(os.fstat(actual)) == binding["directory"] and os.path.samestat(os.fstat(slot),os.fstat(actual))
                        and os.path.samestat(os.fstat(checkout),os.fstat(working)) and os.path.samestat(os.fstat(repo),os.fstat(named)),
                        "private rollback exchange or checkout changed")
            require(read_ref(repo,dest.ref) == rollback["tip"] and encoded(read_index(repo)) == rollback["index"],
                    "private inverse is not fully applied; reconcile the existing checkout and retained files")
            check_worktree(checkout,after,mode)
            require(set(os.listdir(slot)) == {name for name,_ in files},"private inverse exchange is incomplete")
            for name,path in files:
                check_contents(slot,name,before[path],mode)
                require(inode(os.stat(name,dir_fd=slot,follow_symlinks=False)) == binding["files"][path],"displaced private inode changed")
            if candidate.previous_credential:
                ref,end = candidate.previous_credential
                require(read_mapping_end(plan.account,ref) == end,"prior destination credential changed")
            displaced_destination(plan,candidate,record,manifest)
            require(read_mapping_end(plan.account,mapping.destination) == mapping.destination_before,"restored private credential mapping changed")
        observed()
        source_pass,destination_pass = phrases
        verify_credential(load,plan.account,mapping.source,mapping.source_before,manifest["credentials"]["source"],source_pass)
        tools = verified_age_tools(plan.account)
        unlock_end(VaultUnlockCache(tools[0]),tools,plan.account,load,mapping.destination,lambda _:destination_pass)
        if candidate.previous_credential:
            verify_credential(load,plan.account,*candidate.previous_credential,manifest["credentials"]["prior_destination"],destination_pass)
        original = adopt_terminal(tx,plan.account,record,manifest,"rolled-back",password,code,observed)
        if original is not None:
            verify(original)
            return
        evidence = token()
        effect = dict(record["effects"][1],outcome="observed",evidence_id=evidence)
        witness,start = new_owner()
        record,manifest,proof = advance(tx,plan.account,record,manifest,dict(record,checkpoint="rolled-back",
            effects=[record["effects"][0],effect]),dict(manifest,next_effect="none",rollback=dict(rollback,
            evidence_id=evidence,observer=witness,observer_start=start)),password,code,observe=observed)
        verify(record)
        proof()
        observed()


def rollback_private(load, ident, password, code, secret, confirm):
    record,manifest = reopen_intent(load,ident,password)
    pending = record["checkpoint"] == "rollback-pending"
    require(record["operation"] == "transfer" and not record["attempts"] and record["effects"] and record["effects"][0]["outcome"] == "observed"
            and ((pending and len(record["effects"]) == 2 and record["effects"][1]["outcome"] == "intended" and manifest["next_effect"] == "restore-private-destination")
            or (record["checkpoint"] == "destination-confirmed" and manifest["next_effect"] == "source-removal" and len(record["effects"]) == 1)),
            "rollback requires an intact shared source and its exact private destination effect")
    plan = restored_plan(load(),record,manifest)
    require(plan.destination.context.kind == "private" and record["effects"][0]["store_id"] == plan.destination.context.store_id,
            "private rollback needs the original private destination")
    with restored_candidate(plan,manifest) as (candidate,mapping):
        require(manifest["confirmed_destination"] == candidate.proposed,"private destination confirmation changed")
        source_pass,destination_pass = secret("source"),secret("destination")
        material = manifest["credentials"]
        verify_credential(load,plan.account,mapping.source,mapping.source_before,material["source"],source_pass)
        if not pending:
            mapping.verify_saved(load,lambda _:destination_pass)
        prior_credential = candidate.previous_credential
        def prior_login():
            if prior_credential:
                verify_credential(load,plan.account,*prior_credential,material["prior_destination"],destination_pass)
        prior_login()
        if pending:
            rollback = manifest["rollback"]
            inverse = rollback["tip"]
            before,after = recorded_inverse(candidate,plan,rollback)
        else:
            inverse,bundle,before,after = inverse_proposal(plan,candidate,mapping)
        review = {"action":"rollback","transaction_id":ident,"source":dict(manifest["comparison"]["source"],revision=plan.source.base),
                  "destination":{"store_id":plan.destination.context.store_id,"host_id":plan.destination_key,"alias":plan.alias,
                                 "ref":plan.destination.ref,"revision":candidate.proposed,"inverse":inverse},
                  "credential":mapping.destination.name,"keep_credential":mapping.reused,
                  "required_aliases":[plan.alias] if manifest["comparison"]["requires_typed_alias"] else []}
        old = plan.destination_hosts.get(plan.destination_key,{})
        review["restore_credential"] = old.get("credential")
        review["connection_changes"] = {key:{"existing":value["proposed"],"proposed":value["existing"]}
                                        for key,value in plan.comparison()["connection_changes"].items()}
        review["decision"] = plan.decision
        answer = confirm(review)
        aliases = review["required_aliases"]
        require((([answer] if isinstance(answer,str) else answer) == aliases) if aliases else answer is True,
                "explicit rollback confirmation required")
        if pending:
            with transfer_gates(load,plan,record,manifest,recovery=True,finishing="rolled-back") as (tx,verify):
                finish_inverse(load,plan,candidate,mapping,record,manifest,tx,verify,before,after,(source_pass,destination_pass),password,code)
            return {"transaction_id":ident,"checkpoint":"rolled-back"}
        dest = plan.destination
        effect = {"effect_id":token(),"store_id":dest.context.store_id,"intent_id":token(),"evidence_id":None,"outcome":"intended"}
        runtime = ("hostbook-transfer-v1",ident,effect["effect_id"])
        rollback = {"ref":dest.ref,"base":candidate.proposed,"tip":inverse,"bundle":bundle,"exchange":list(runtime)}
        with transfer_gates(load,plan,record,manifest,recovery=True) as (tx,verify),local_descriptors(plan.account,dest) as (checkout,repo):
            def unchanged():
                source_intact(load,plan,record,manifest,mapping,tx)
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
                source_intact(load,plan,record,manifest,mapping,tx)
                with local_descriptors(plan.account,dest) as (working,actual):
                    require(os.path.samestat(os.fstat(working),os.fstat(checkout)) and os.path.samestat(os.fstat(actual),os.fstat(repo)),
                            "rollback destination binding changed")
            live()
            with private_path(repo,runtime[:-1]) as parent:
                os.mkdir(runtime[-1],0o700,dir_fd=parent)
                sync_local(parent)
            with open_directory(repo,runtime) as slot:
                require_private_directory(slot,"rollback exchange")
                replacements,removals = inverse_paths(candidate,before,after)
                def input_binding():
                    unchanged()
                    with local_descriptors(plan.account,dest) as (_,named),open_directory(named,runtime) as actual:
                        require_private_directory(actual,"private rollback exchange")
                        require(os.path.samestat(os.fstat(actual),os.fstat(slot)),"rollback exchange changed")
                    expected = {}
                    for path in replacements+removals:
                        parts = path.split("/")
                        with open_directory(checkout,parts[:-1]) as parent:
                            check_contents(parent,parts[-1],before[path],mode)
                            expected[path] = inode(os.stat(parts[-1],dir_fd=parent,follow_symlinks=False))
                    return {"directory":inode(os.fstat(slot)),"files":expected}
                binding = input_binding()
                rollback = dict(rollback,binding=binding,index=encoded(expected_index))
                def pinned():
                    require(input_binding() == binding,"private rollback inputs changed")
                record,manifest,proof = advance(tx,plan.account,record,manifest,record,
                    dict(manifest,rollback=rollback),password,code,observe=pinned)
                verify(record)
                proof()
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
                finish_inverse(load,plan,candidate,mapping,record,manifest,tx,verify,before,after,(source_pass,destination_pass),password,code)
    return {"transaction_id":ident,"checkpoint":"rolled-back"}
