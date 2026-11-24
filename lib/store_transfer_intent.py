"""Persist an explicitly confirmed transfer preparation before local or remote store effects."""
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
import base64
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from store_checkout import (check_index, check_worktree, durable_file, make_parents, read_index, read_ref,
                            rename_guarded, tree)
from store_context import (JSON_LIMIT, StoreContextError, bound_directory, open_directory, parse_json, path_parts,
                           read_file, require, require_approval_file, require_private_directory, resolved_store, store_writer, sync_local,
                           valid_id, verify_state_directory)
from store_journal import classify, private_json, require_idle, transaction
from store_login import verified_age_tools
from store_mode import mode_records
from store_publish import boot_id, recovery_root
from store_receive import IDENTITY, fetch_from_fd, git, plain_git
from store_transfer import writable_destination
from store_transfer_credentials import private_workspace, unlock_end
from store_transfer_recovery import capture, read_payload, seal_payload, verify_backup
from store_vault import VaultUnlockCache, file_evidence


def token():
    return str(uuid4())


def encoded(value):
    return base64.b64encode(value).decode("ascii")


def wire(value):
    return json.dumps(value, sort_keys=True, separators=(",",":"), default=os.fspath).encode()


@contextmanager
def local_descriptors(account, store):
    ctx = store.context
    with resolved_store(account,ctx.store_id,ctx.generations) as (current,binding,root,_):
        require(current == ctx, "transfer store changed")
        with ExitStack() as fds:
            def opened(directory):
                return fds.enter_context(bound_directory(root,path_parts(account.root.path,directory.path),directory,
                                                         "transfer checkout binding changed"))
            yield opened(binding.root), opened(binding.git_dir)


def clean_checkout(account, store):
    with local_descriptors(account,store) as (checkout,repo), private_workspace() as (stage,_):
        filemode, _ = plain_git(repo,bare=False)
        index = read_index(repo)
        git(stage,"init","-q","--bare","--template=")
        require(fetch_from_fd(stage,repo,store.ref,"refs/hostbook/base") == store.base, "transfer branch changed")
        files = tree(stage,store.base,git)
        check_index(stage,index,files,git)
        check_worktree(checkout,files,filemode)
        def blob(path):
            return git(stage,"cat-file","blob",files[path].decode())
        require(parse_json(blob(IDENTITY)) == store.identity and
                all(path in files and blob(path) == data for path,data in store.files.items()),
                "transfer SSH sources differ from the accepted checkout")
        groups = blob("groups.tsv") if "groups.tsv" in files else b""
        require(not groups.strip(), "transfer requires review of existing group memberships")
        return files, index, blob(IDENTITY), filemode


def capture_credentials(load, mapping, read_passphrase, password, code):
    account = load()
    tools = verified_age_tools(account)
    cache = VaultUnlockCache(tools[0])
    material = {}
    for role, ref, end in (("source",mapping.source,mapping.source_before),
                           ("destination",mapping.destination,mapping.destination_before)):
        phrase = read_passphrase(ref.store_id)
        require(phrase != password and phrase != code, "transaction recovery must be independent of the store password")
        identity = unlock_end(cache,tools,account,load,ref,lambda _store:phrase)
        vault = end.vault
        backup = {"name":ref.name,"vault_pub":encoded(vault.recipient),"vault_key":encoded(vault.password_envelope),
                  "vault_recovery":encoded(vault.recovery_envelope),
                  "vault_evidence":vault.evidence,"credential_evidence":end.file_evidence}
        if end.record:
            record = end.record
            backup.update(meta=encoded(record.metadata),age=encoded(record.ciphertext),
                          public_key=encoded(record.public_key) if record.public_key else None,
                          value=encoded(cache.open_secret(identity,record.ciphertext)))
        material[role] = backup
    mapping.check_current(load)
    require(verified_age_tools(account) == tools, "transfer vault tools changed")
    return material


def initial_record(plan, ident):
    stores = [{"store_id":store.context.store_id, "role":role, "snapshot_id":token(),
               "checkout_generation":store.context.generations.checkout,
               "connection_generation":store.context.generations.connection,
               "identity_generation":store.identity["generation"],"vault_generation":1}
              for role,store in (("source",plan.source),("destination",plan.destination))]
    record = {"format":"hostbook-transaction","version":1,"transaction_id":ident,
              "device_id":plan.account.device_id,"root_id":plan.account.recovery_root_id,"sequence":1,"previous_sequence":0,
              "operation":"transfer","checkpoint":"prepared","owner":{"owner_id":token(),"process_id":os.getpid(),
              "process_start_id":token(),"boot_id":boot_id()},"stores":stores,"hosts":[
              {"store_id":store["store_id"],"host_id":plan.host_id} for store in stores],
              "payload_id":token(),"recovery_generation":1,"effects":[],"attempts":[],
              "collection":{"decision_id":None,"payload_state":"retained","payload_ids":[],"recovery_generations":[]}}
    transaction(record,None)
    return record


@contextmanager
def private_path(root, parts):
    with ExitStack() as stack:
        parent = root
        for name in parts:
            parent = stack.enter_context(make_parents(parent,(name,)))
            require_private_directory(parent,"transfer recovery")
        yield parent


@contextmanager
def staged_transaction(recovery, record, protected, manifest, account, password, code):
    scratch = ".prepare-" + token()
    os.mkdir(scratch,0o700,dir_fd=recovery)
    with open_directory(recovery,(scratch,)) as draft:
        try:
            require_private_directory(draft,"transfer staging")
            with private_path(draft,("records",)) as records:
                durable_file(records,"1.json",wire(record))
                sync_local(records)
            with private_path(draft,("payloads",)) as payloads, private_path(draft,("recovery","1")) as envelopes:
                for name,data in (("vault.pub",protected.recipient),("vault.key.age",protected.password_envelope),
                                  ("vault.recovery.age",protected.recovery_envelope)):
                    durable_file(envelopes,name,data)
                durable_file(payloads,record["payload_id"]+".age",protected.ciphertext)
                sync_local(payloads)
                sync_local(envelopes)
                current = {key:record[key] for key in ("version","transaction_id","sequence")}
                durable_file(draft,"current.json",wire(current))
                sync_local(draft)
                classify(draft,account,record["transaction_id"],"transactions")
                names = ("vault.key.age","vault.recovery.age")
                prior = capture(envelopes,payloads,record["payload_id"],names)
                verify_backup(account,envelopes,payloads,record["payload_id"],password,code,manifest)
                def verify():
                    require(capture(envelopes,payloads,record["payload_id"],names) == prior,
                            "verified transfer recovery material changed")
                    with open_directory(draft,("records",)) as history:
                        require(private_json(history,"1.json") == record and private_json(draft,"current.json") == current,
                                "transfer preparation record changed")
                    with open_directory(draft,("payloads",)) as p, open_directory(draft,("recovery","1")) as e:
                        require(os.path.samestat(os.fstat(p),os.fstat(payloads)) and
                                os.path.samestat(os.fstat(e),os.fstat(envelopes)), "transfer recovery directory changed")
                yield scratch, draft, verify
        finally:
            # Only clean our still-unpublished directory and the exact files created herein.
            try:
                here = os.stat(scratch,dir_fd=recovery,follow_symlinks=False)
            except FileNotFoundError:
                here = None
            if here is not None and os.path.samestat(here,os.fstat(draft)):
                for path in ("current.json","records/1.json","payloads/"+record["payload_id"]+".age",
                             "recovery/1/vault.pub","recovery/1/vault.key.age","recovery/1/vault.recovery.age"):
                    parts = path.split("/")
                    try:
                        with open_directory(draft,parts[:-1]) as parent:
                            os.unlink(parts[-1],dir_fd=parent)
                    except FileNotFoundError:
                        pass
                for path in (("records",),("payloads",),("recovery","1"),("recovery",)):
                    try:
                        with open_directory(draft,path[:-1]) as parent:
                            os.rmdir(path[-1],dir_fd=parent)
                    except FileNotFoundError:
                        pass
                os.rmdir(scratch,dir_fd=recovery)


def prepare_intent(load, plan, candidate, mapping, read_passphrase, password, code, confirm):
    """Publish a complete encrypted preparation. Both live checkouts remain at their original revisions."""
    require(mapping is not None and candidate.plan is plan and candidate.mapping is mapping,
            "preparation requires the reviewed destination credential and Git candidate")
    plan.revalidate(load)
    mapping.check_current(load)
    comparison = dict(plan.comparison(),destination_credential=mapping.destination.name,
                      credential_action="reuse" if mapping.reused else "create",source_groups=[])
    # The supported source project holds exactly one concrete host. Any PROD annotation requires its alias.
    prod = any(b"prod" in line.split(b":",1)[1].lower().split() for line in plan.source.files[candidate.source_path].split(b"\n")
               if re.match(rb"^[ \t]*#[ \t]*tags[ \t]*:",line,re.I))
    comparison["requires_typed_alias"] = prod
    checks = [clean_checkout(plan.account,store) for store in (plan.source,plan.destination)]
    answer = confirm(comparison)
    require((isinstance(answer,str) and answer == plan.source_hosts[plan.host_id]["name"]) if prod else answer is True,
            "explicit transfer confirmation required")
    record = initial_record(plan,token())
    credentials = capture_credentials(load,mapping,read_passphrase,password,code)
    snapshots = {}
    for row,store,observed in zip(record["stores"],(plan.source,plan.destination),checks):
        snapshots[row["snapshot_id"]] = {"context":asdict(store.context),"base":store.base,"ref":store.ref,
            "index":encoded(observed[1]),"identity":encoded(observed[2]),"files":{k:encoded(v) for k,v in store.files.items()},
            "local":[encoded(v) if v is not None else None for v in store.local],"groups":None,"tracked":{
            path:oid.decode() for path,oid in observed[0].items()}}
    git(candidate.stage,"update-ref","refs/hostbook/transfer",candidate.proposed)
    bundle = candidate.stage/"transfer.bundle"
    git(candidate.stage,"bundle","create",str(bundle),"refs/hostbook/transfer","^"+plan.destination.base)
    require(bundle.stat().st_size <= JSON_LIMIT, "transfer proposal exceeds recovery size limit")
    manifest = wire({"record":record,"comparison":comparison,"snapshots":snapshots,"source_path":candidate.source_path,
                     "destination_ref":plan.destination.ref,"target":asdict(plan.destination.target),
                     "destination_base":plan.destination.base,"proposed":candidate.proposed,"bundle":encoded(bundle.read_bytes()),
                     "changes":{path:encoded(data) for path,data in candidate.changes.items()},
                     "credentials":credentials,"next_effect":"destination-save"})
    protected = seal_payload(plan.account,manifest,password,code)
    with recovery_root(plan.account) as root, staged_transaction(root,record,protected,manifest,plan.account,password,code) as staged:
        scratch,draft,verify = staged
        with ExitStack() as gates:
            writers = {}
            for store in sorted((plan.source,plan.destination),key=lambda s:s.context.store_id):
                ctx = store.context
                account, active, _state, writer = gates.enter_context(store_writer(load,ctx.store_id,ctx.generations))
                require((account,active) == (plan.account,ctx), "transfer writer binding changed")
                writers[ctx.store_id] = writer
                require_idle(root,account,ctx.store_id)
            active,pending,inflight = mode_records(root,plan.account,plan.destination.context.store_id,None)
            require(not active and not pending and not inflight, "settle existing shared work before transfer")
            plan.revalidate(load)
            mapping.check_current(load)
            for store,check in zip((plan.source,plan.destination),checks):
                with local_descriptors(plan.account,store) as (checkout,repo):
                    require(read_ref(repo,store.ref) == store.base and read_index(repo) == check[1],
                            "transfer checkout/index changed after confirmation")
                    check_worktree(checkout,check[0],check[3])
                ctx = store.context
                with resolved_store(plan.account,ctx.store_id,ctx.generations) as (_,_,_,state):
                    verify_state_directory(state,path_parts(plan.account.state.path,ctx.writer_lock),writers[ctx.store_id],
                                           "transfer writer changed")
            with writable_destination(plan), recovery_root(load()) as fresh, private_path(root,("transactions",)) as txns:
                require(load() == plan.account and os.path.samestat(os.fstat(root),os.fstat(fresh))
                        and os.path.samestat(os.stat(scratch,dir_fd=root,follow_symlinks=False),os.fstat(draft)),
                        "transfer recovery authority changed")
                with open_directory(fresh,("transactions",)) as current:
                    require(os.path.samestat(os.fstat(txns),os.fstat(current)), "transaction authority directory changed")
                verify()
                ident = record["transaction_id"]
                try:
                    rename_guarded(root,scratch,txns,ident,exchange=False)
                    sync_local(txns)
                    sync_local(root)
                    for row in record["stores"]:
                        with private_path(root,("stores",row["store_id"])) as hints:
                            durable_file(hints,ident+".ref",wire({"version":1,"transaction_id":ident}))
                            sync_local(hints)
                    with recovery_root(load()) as current, open_directory(current,("transactions",ident)) as selected:
                        require(load() == plan.account and os.path.samestat(os.fstat(selected),os.fstat(draft)),
                                "selected transfer authority changed")
                        classify(selected,plan.account,ident,"transactions")
                        verify()
                except (OSError, StoreContextError):
                    raise StoreContextError("transfer " + ident + " preparation requires reconciliation") from None
    return record["transaction_id"]


def reopen_intent(load, ident, phrase, *, recovery=False):
    require(valid_id(ident), "invalid transfer transaction ID")
    account = load()
    with recovery_root(account) as root, open_directory(root,("transactions",ident)) as tx:
        require_private_directory(tx,"transfer transaction")
        classify(tx,account,ident,"transactions")
        def pinned(parent,name):
            require_private_directory(parent,"transfer authority")
            evidence = []
            def verify(fd,info):
                require_approval_file(fd,info)
                evidence.append(file_evidence(info))
            return read_file(parent,name,JSON_LIMIT,verify),evidence
        selector_stamp = pinned(tx,"current.json")
        selected = parse_json(selector_stamp[0])
        name = str(selected["sequence"])+".json"
        with open_directory(tx,("records",)) as history:
            record_stamp = pinned(history,name)
        record = parse_json(record_stamp[0])
        generation = ("recovery",str(record["recovery_generation"]))
        names = ("vault.recovery.age" if recovery else "vault.key.age",)
        with open_directory(tx,generation) as env, open_directory(tx,("payloads",)) as payloads:
            material = capture(env,payloads,record["payload_id"],names)
            manifest = parse_json(read_payload(account,env,payloads,record["payload_id"],phrase,recovery=recovery))
        with recovery_root(load()) as fresh, open_directory(fresh,("transactions",ident)) as actual:
            with open_directory(actual,("records",)) as hist, open_directory(actual,generation) as env, open_directory(actual,("payloads",)) as payloads:
                require(manifest["record"] == record and os.path.samestat(os.fstat(tx),os.fstat(actual))
                        and pinned(actual,"current.json") == selector_stamp and pinned(hist,name) == record_stamp
                        and capture(env,payloads,record["payload_id"],names) == material and load() == account,
                        "transfer recovery manifest, record or selector changed")
        return record,manifest
