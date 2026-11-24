"""Reserve and publish the exact saved destination proposal of a confirmed transfer."""
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
import fcntl
import os
import subprocess

from store_checkout import check_contents, check_worktree, direct_ref, durable_file, read_index, read_ref, tree
from store_context import (StoreContextError, open_directory, require, require_approval_file, require_private_directory,
                           resolved_store, sync_local)
from store_journal import publication_receipt
from store_publish import require_plain_receiver, send
from store_read import local_inputs
from store_receive import IDENTITY, git, plain_git, registered_target
from store_transfer import writable_shared
from store_transfer_apply import advance, check_source, displaced_destination, exchange_files, selected, transfer_gates
from store_transfer_credentials import private_workspace
from store_transfer_intent import encoded, local_descriptors, private_path, reopen_intent, token, wire


def saved_checkout(load, plan, candidate, mapping, record, manifest):
    src, dst = plan.source,plan.destination
    source_snapshot = next(row["snapshot_id"] for row in record["stores"] if row["role"] == "source")
    check_source(plan.account,src,manifest["snapshots"][source_snapshot])
    with resolved_store(plan.account,src.context.store_id,src.context.generations) as (active,_,root,state):
        require(active == src.context and local_inputs(state,active.store_id) == src.local,"transfer source changed")
        if active.kind == "shared":
            with registered_target(plan.account,active,root,state) as (target,remote):
                require(target == src.target and direct_ref(remote,src.ref) == src.base,"registered shared source changed")
                require_plain_receiver(remote)
    destination_checkout(load,plan,candidate,mapping,record,manifest)


def destination_checkout(load, plan, candidate, mapping, record, manifest):
    src, dst = plan.source,plan.destination
    require(plan.account == load() and candidate.plan is plan and candidate.mapping is mapping
            and manifest["proposed"] == candidate.proposed and manifest["destination_ref"] == dst.ref
            and manifest["destination_base"] == dst.base and wire(manifest["target"]) == wire(asdict(plan.shared.target))
            and manifest["changes"] == {path:encoded(data) for path,data in candidate.changes.items()},
            "publication requires the exact saved transfer candidate")
    require(record["hosts"] == plan.host_keys(),
            "transfer host binding changed")
    with resolved_store(plan.account,dst.context.store_id,dst.context.generations) as (active,_,_,state):
        require(active == dst.context and local_inputs(state,active.store_id) == dst.local,
                "destination registration or SSH settings changed")
    with local_descriptors(plan.account,dst) as (checkout,repo):
        filemode,_ = plain_git(repo,bare=False)
        evidence = manifest["destination_evidence"]
        require(evidence["tip"] == candidate.proposed == read_ref(repo,dst.ref) and
                encoded(read_index(repo)) == evidence["index"], "saved destination branch or index changed")
        check_worktree(checkout,tree(candidate.stage,candidate.proposed,git),filemode)
    mapping.check_current(load,saved=True)


def confirm_private_destination(load, ident, plan, candidate, mapping, read_passphrase, password, code):
    record,manifest = reopen_intent(load,ident,password)
    require(plan.destination.context.kind == "private" and record["checkpoint"] == "destination-saved"
            and not record["attempts"] and manifest["next_effect"] == "confirm-private-destination",
            "private destination is not ready to confirm")
    phrase = read_passphrase(mapping.destination.store_id)
    mapping.verify_saved(load,lambda _store:phrase)
    with transfer_gates(load,plan,record,manifest) as (tx,verify):
        def current():
            saved_checkout(load,plan,candidate,mapping,record,manifest)
            displaced_destination(plan,candidate,record,manifest)
        record,manifest,proof = advance(tx,plan.account,record,manifest,dict(record,checkpoint="destination-confirmed"),
            dict(manifest,next_effect="source-removal",confirmed_destination=candidate.proposed),password,code,observe=current)
        verify(record)
        proof()
        current()
    return {"transaction_id":ident,"checkpoint":"destination-confirmed"}


def removed_checkout(load, plan, candidate, mapping, record, manifest):
    destination_checkout(load,plan,candidate,mapping,record,manifest)
    src = plan.source
    evidence = manifest["source_evidence"]
    require(manifest["confirmed_destination"] == candidate.proposed and manifest["source_proposed"] == candidate.source_proposed
            and len(record["effects"]) == 2 and record["effects"][1]["outcome"] == "observed"
            and record["effects"][1]["store_id"] == src.context.store_id
            and record["effects"][1]["evidence_id"] == evidence["evidence_id"],"shared source removal evidence changed")
    with resolved_store(plan.account,src.context.store_id,src.context.generations) as (active,_,_,state):
        require(active == src.context and local_inputs(state,active.store_id) == src.local,"shared source settings changed")
    with local_descriptors(plan.account,src) as (checkout,repo):
        filemode,_ = plain_git(repo,bare=False)
        require(evidence["ref"] == src.ref and evidence["tip"] == candidate.source_proposed == read_ref(repo,src.ref)
                and encoded(read_index(repo)) == evidence["index"],"saved source branch or index changed")
        check_worktree(checkout,tree(candidate.source_stage,candidate.source_proposed,git),filemode)


def publishable_source(load, plan, candidate, mapping, record, manifest):
    removed_checkout(load,plan,candidate,mapping,record,manifest)
    runtime = ("hostbook-transfer-v1",record["transaction_id"],record["effects"][1]["effect_id"])
    require(manifest["source_runtime"] == list(runtime),"source exchange binding changed")
    original = tree(candidate.source_stage,plan.source.base,git)
    with local_descriptors(plan.account,plan.source) as (_,repo),open_directory(repo,runtime) as slot:
        filemode,_ = plain_git(repo,bare=False)
        require_private_directory(slot,"shared source exchange")
        files = [("source","verified-source",original[candidate.source_path]),("0","verified-0",original[IDENTITY])]
        exchange_files(slot,files,filemode,manifest["source_evidence"].get("exchange"),
                       retiring=record["checkpoint"] == "source-confirmed")


def current_receiver(plan, expected, pinned=None, *, require_writes=False):
    shared = plan.shared
    with resolved_store(plan.account,shared.context.store_id,shared.context.generations) as (ctx,_,root,state):
        require(ctx == shared.context and ctx.effective_mode == "two-way","shared registration changed")
        with registered_target(plan.account,ctx,root,state) as (target,remote):
            require(target == shared.target and (pinned is None or os.path.samestat(os.fstat(remote),os.fstat(pinned)))
                    and direct_ref(remote,shared.ref) == expected,"shared publication requires reconciliation")
            if require_writes:
                require_plain_receiver(remote)


@contextmanager
def sender_lease(folder, expected):
    require_private_directory(folder,"transfer sender")
    fd = os.open("lease",os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW,dir_fd=folder)
    try:
        info = os.fstat(fd)
        require_approval_file(fd,info)
        require((info.st_dev,info.st_ino) == (expected["device"],expected["inode"]),"transfer sender lease changed")
        try:
            fcntl.flock(fd,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StoreContextError("transfer sender is still active") from None
        yield fd
    finally:
        os.close(fd)


def publish_destination(load, ident, plan, candidate, mapping, read_passphrase, password, code):
    require(plan.destination.context.kind == "shared","publication requires a shared destination")
    return publish_shared(load,ident,plan,candidate,mapping,read_passphrase,password,code,"destination")


def publish_source(load, ident, plan, candidate, mapping, read_passphrase, password, code):
    require(plan.source.context.kind == "shared","publication requires a shared source")
    return publish_shared(load,ident,plan,candidate,mapping,read_passphrase,password,code,"source")


def publish_shared(load, ident, plan, candidate, mapping, read_passphrase, password, code, role):
    record,manifest = reopen_intent(load,ident,password)
    if role == "destination":
        prior,stage,proposed,checkout = "destination-saved",candidate.stage,candidate.proposed,saved_checkout
    else:
        prior,stage,proposed,checkout = "source-removed-local",candidate.source_stage,candidate.source_proposed,publishable_source
    require(record["checkpoint"] == prior and not record["attempts"] and manifest["next_effect"] == role+"-publication",
            "transfer publication is not ready")
    phrase = read_passphrase(mapping.destination.store_id)
    mapping.verify_saved(load,lambda _store:phrase)
    account,shared = plan.account,plan.shared
    require(git(stage,"rev-list","--parents","-n","1",proposed).split() == [proposed.encode(),shared.base.encode()],
            "transfer proposal has an unexpected parent")
    attempt_id = token()
    with ExitStack() as transport:
        with transfer_gates(load,plan,record,manifest) as (tx,verify), writable_shared(plan,transfer=record) as remote:
            checkout(load,plan,candidate,mapping,record,manifest)
            displaced_destination(plan,candidate,record,manifest)
            pinned_tx,pinned_remote = os.dup(tx),os.dup(remote)
            transport.callback(os.close,pinned_tx)
            transport.callback(os.close,pinned_remote)
            with private_path(tx,("senders",)) as senders:
                os.mkdir(attempt_id,0o700,dir_fd=senders)
                sync_local(senders)
                attempt_folder = transport.enter_context(open_directory(senders,(attempt_id,)))
            require_private_directory(attempt_folder,"transfer sender")
            durable_file(attempt_folder,"lease",b"")
            fd = os.open("lease",os.O_RDWR | os.O_NOFOLLOW,dir_fd=attempt_folder)
            lease = transport.enter_context(os.fdopen(fd,"r+b"))
            fcntl.flock(fd,fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = os.fstat(fd)
            sync_local(attempt_folder)
            lease_bytes = {"device":info.st_dev,"inode":info.st_ino}
            attempt = {"attempt_id":attempt_id,"store_id":shared.context.store_id,"owner_id":record["owner"]["owner_id"],
                       "connection_generation":shared.context.generations.connection,"reservation_id":token(),
                       "target_id":shared.context.target_id,"intent_id":token(),"evidence_id":None,
                       "sender_state":"active","outcome":"reserved"}
            binding = {"attempt":attempt_id,"target":asdict(shared.target),"ref":shared.ref,"base":shared.base,
                       "proposed":proposed,"lease":lease_bytes,"reservation":attempt["reservation_id"],
                       "intent":attempt["intent_id"],"store":shared.context.store_id}
            def ready():
                checkout(load,plan,candidate,mapping,record,manifest)
                displaced_destination(plan,candidate,record,manifest)
                current_receiver(plan,shared.base,pinned_remote,require_writes=True)
            record,manifest,proof = advance(pinned_tx,account,record,manifest,dict(record,attempts=[attempt]),
                dict(manifest,publication=binding,next_effect="send-"+role),password,code,observe=ready)
            verify(record)
        started = False
        def start():
            nonlocal started
            proof()
            selected(pinned_tx,account,record)
            ready()
            with open_directory(pinned_tx,("senders",attempt_id)) as named:
                require(os.path.samestat(os.fstat(named),os.fstat(attempt_folder)) and
                        os.path.samestat(os.fstat(fd),os.stat("lease",dir_fd=named,follow_symlinks=False)),
                        "transfer sender binding changed")
            durable_file(attempt_folder,"send-started",attempt_id.encode())
            sync_local(attempt_folder)
            started = True
        try:
            with private_workspace() as (_,runtime):
                result = send(stage,pinned_remote,fd,shared.ref,shared.base,proposed,runtime,account,start)
        except (OSError, StoreContextError, subprocess.SubprocessError):
            result = "uncertain" if started else "not-accepted"
        finally:
            lease.close()
        historical = False
        try:
            with sender_lease(attempt_folder,lease_bytes), open_directory(pinned_tx,("senders",attempt_id)) as named:
                require(os.path.samestat(os.fstat(named),os.fstat(attempt_folder)) and
                        os.stat("lease",dir_fd=named,follow_symlinks=False).st_ino == lease_bytes["inode"],
                        "original transfer sender binding changed")
                ack = token()
                settled = dict(attempt,outcome=result,evidence_id=ack,sender_state="quiescent")
                receipt = publication_receipt(record["stores"],settled)
                durable_file(named,"outcome.json",wire(receipt))
                sync_local(named)
                with transfer_gates(load,plan,record,manifest) as (tx,verify):
                    proof()
                    record,manifest,proof = advance(tx,account,record,manifest,dict(record,attempts=[settled]),
                        dict(manifest,next_effect="reconcile-"+role,publication_evidence=receipt),password,code)
                    historical = True
                    verify(record)
                    if result == "accepted":
                        def confirmed():
                            checkout(load,plan,candidate,mapping,record,manifest)
                            displaced_destination(plan,candidate,record,manifest)
                            current_receiver(plan,proposed,pinned_remote)
                        confirmed()
                        updated = dict(manifest,next_effect="source-removal" if role == "destination" else "complete")
                        updated["confirmed_"+role] = proposed
                        record,manifest,proof = advance(tx,account,record,manifest,dict(record,checkpoint=role+"-confirmed"),
                            updated,password,code,observe=confirmed)
                        verify(record)
        except (OSError,StoreContextError):
            return {"transaction_id":ident,"attempt_id":attempt_id,"checkpoint":prior,
                    "outcome":result if historical else "uncertain"}
    return {"transaction_id":ident,"attempt_id":attempt_id,"checkpoint":record["checkpoint"],"outcome":result}
