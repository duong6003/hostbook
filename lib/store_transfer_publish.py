"""Reserve and publish the exact saved destination proposal of a confirmed transfer."""
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
import fcntl
import os
import subprocess

from store_checkout import check_worktree, direct_ref, durable_file, read_index, read_ref, tree
from store_context import (StoreContextError, open_directory, require, require_approval_file, require_private_directory,
                           resolved_store, sync_local)
from store_publish import require_plain_receiver, send
from store_read import local_inputs
from store_receive import git, plain_git, registered_target
from store_transfer import observe_store, writable_destination
from store_transfer_apply import advance, check_source, selected, transfer_gates
from store_transfer_credentials import private_workspace
from store_transfer_intent import encoded, local_descriptors, private_path, reopen_intent, token, wire


def saved_checkout(load, plan, candidate, mapping, record, manifest):
    src, dst = plan.source,plan.destination
    source_snapshot = next(row["snapshot_id"] for row in record["stores"] if row["role"] == "source")
    check_source(plan.account,src,manifest["snapshots"][source_snapshot])
    require(observe_store(plan.account,src.context.store_id,src.context.generations) == src,"transfer source changed")
    destination_checkout(load,plan,candidate,mapping,record,manifest)


def destination_checkout(load, plan, candidate, mapping, record, manifest):
    src, dst = plan.source,plan.destination
    require(plan.account == load() and candidate.plan is plan and candidate.mapping is mapping
            and manifest["proposed"] == candidate.proposed and manifest["destination_ref"] == dst.ref
            and manifest["destination_base"] == dst.base and wire(manifest["target"]) == wire(asdict(dst.target))
            and manifest["changes"] == {path:encoded(data) for path,data in candidate.changes.items()},
            "publication requires the exact saved transfer candidate")
    require(record["hosts"] == [{"store_id":end.context.store_id,"host_id":plan.host_id} for end in (src,dst)],
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
        yield
    finally:
        os.close(fd)


def publish_destination(load, ident, plan, candidate, mapping, read_passphrase, password, code):
    record,manifest = reopen_intent(load,ident,password)
    require(record["checkpoint"] == "destination-saved" and not record["attempts"]
            and manifest["next_effect"] == "destination-publication","transfer destination publication is not ready")
    phrase = read_passphrase(mapping.destination.store_id)
    mapping.verify_saved(load,lambda _store:phrase)
    account,dst = plan.account,plan.destination
    require(git(candidate.stage,"rev-list","--parents","-n","1",candidate.proposed).split() ==
            [candidate.proposed.encode(),dst.base.encode()],"transfer proposal has an unexpected parent")
    attempt_id = token()
    with ExitStack() as transport:
        with transfer_gates(load,plan,record,manifest) as (tx,verify), writable_destination(plan,transfer=record) as remote:
            saved_checkout(load,plan,candidate,mapping,record,manifest)
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
            attempt = {"attempt_id":attempt_id,"store_id":dst.context.store_id,"owner_id":record["owner"]["owner_id"],
                       "connection_generation":dst.context.generations.connection,"reservation_id":token(),
                       "target_id":dst.context.target_id,"intent_id":token(),"evidence_id":None,
                       "sender_state":"active","outcome":"reserved"}
            binding = {"attempt":attempt_id,"target":asdict(dst.target),"ref":dst.ref,"base":dst.base,
                       "proposed":candidate.proposed,"lease":lease_bytes,"reservation":attempt["reservation_id"],
                       "intent":attempt["intent_id"],"store":dst.context.store_id}
            def ready():
                saved_checkout(load,plan,candidate,mapping,record,manifest)
                require_plain_receiver(pinned_remote)
                require(direct_ref(pinned_remote,dst.ref) == dst.base, "destination remote base changed")
            record,manifest,proof = advance(pinned_tx,account,record,manifest,dict(record,attempts=[attempt]),
                dict(manifest,publication=binding,next_effect="send-destination"),password,code,observe=ready)
            verify(record)
        started = False
        def start():
            nonlocal started
            proof()
            selected(pinned_tx,account,record)
            with open_directory(pinned_tx,("senders",attempt_id)) as named:
                require(os.path.samestat(os.fstat(named),os.fstat(attempt_folder)) and
                        os.path.samestat(os.fstat(fd),os.stat("lease",dir_fd=named,follow_symlinks=False)),
                        "transfer sender binding changed")
            durable_file(attempt_folder,"send-started",attempt_id.encode())
            sync_local(attempt_folder)
            started = True
        try:
            with private_workspace() as (_,runtime):
                result = send(candidate.stage,pinned_remote,fd,dst.ref,dst.base,candidate.proposed,runtime,account,start)
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
                receipt = {"attempt_id":attempt_id,"evidence_id":ack,"sender_state":"quiescent","outcome":result}
                durable_file(named,"outcome.json",wire(receipt))
                sync_local(named)
                with transfer_gates(load,plan,record,manifest) as (tx,verify):
                    proof()
                    settled = dict(attempt,outcome=result,evidence_id=ack,sender_state="quiescent")
                    record,manifest,proof = advance(tx,account,record,manifest,dict(record,attempts=[settled]),
                        dict(manifest,next_effect="reconcile-destination",publication_evidence=receipt),password,code)
                    historical = True
                    verify(record)
                    if result == "accepted":
                        def confirmed():
                            saved_checkout(load,plan,candidate,mapping,record,manifest)
                            with resolved_store(account,dst.context.store_id,dst.context.generations) as (ctx,_,root,state):
                                require(ctx == dst.context,"destination registration changed")
                                with registered_target(account,ctx,root,state) as (target,actual):
                                    require(target == dst.target and os.path.samestat(os.fstat(pinned_remote),os.fstat(actual))
                                            and direct_ref(actual,dst.ref) == candidate.proposed,
                                            "destination publication requires reconciliation")
                        confirmed()
                        record,manifest,proof = advance(tx,account,record,manifest,dict(record,checkpoint="destination-confirmed"),
                            dict(manifest,next_effect="source-removal",confirmed_destination=candidate.proposed),
                            password,code,observe=confirmed)
                        verify(record)
        except (OSError,StoreContextError):
            return {"transaction_id":ident,"attempt_id":attempt_id,"checkpoint":"destination-saved",
                    "outcome":result if historical else "uncertain"}
    return {"transaction_id":ident,"attempt_id":attempt_id,"checkpoint":record["checkpoint"],"outcome":result}
