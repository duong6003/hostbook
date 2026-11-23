"""Change a registered shared store's effective mode through its durable fence."""
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import tempfile
from uuid import uuid4

from store_checkout import direct_ref, durable_file, make_parents, rename_guarded
from store_context import (JSON_LIMIT, Generations, acquire_local_lock, entry_generations, fields, fixture_request, index_rows, load_approved_account,
                           open_directory, path_parts, read_registry, registry_entries, require, resolved_store, require_approval_file, require_private_directory,
                           staged_local_file, store_writer, sync_local, valid_id, validate_entry, verify_state_directory)
from store_journal import check_owner, control as validate_control, classify, private_json, require_idle
from store_publish import attempt_binding, boot_id, read_attempt, json_bytes, recovery_root, select_record, sync_named
from store_receive import absolute_directory, fetch_from_fd, git, oid, receive, receive_binding, require_no_receive_pending, verify_incoming
from store_read import local_inputs
import store_publish


def verify_live(load, account, state, writer, entry):
    require(load() == account, "registered account approval changed")
    with resolved_store(account, entry["store_id"], entry_generations(entry)) as (ctx, _, _, live_state):
        require(os.path.samestat(os.fstat(live_state), os.fstat(state))
                and registry_entries(live_state, account)[0].get(entry["store_id"]) == entry,
                "mode registry binding changed")
        verify_state_directory(live_state, path_parts(account.state.path, ctx.writer_lock), writer,
                               "mode writer binding changed")


def registry_update(header, entry, advance=1):
    replacement = dict(header, revision=header["revision"]+advance,
                       stores=[entry if row["store_id"] == entry["store_id"] else row for row in header["stores"]])
    contents = json.dumps(replacement, ensure_ascii=False, separators=(",",":")).encode()
    require(len(contents) <= JSON_LIMIT, "mode registry exceeds size limit")
    return contents


def replace_entry(state, account, old, new, verify):
    with open_directory(state, ("stores", "v1")) as device:
        acquire_local_lock(device, "device registry is busy")
        header, _ = read_registry(state)
        entries, _ = registry_entries(state, account)
        require(entries.get(old["store_id"]) == old and new["store_id"] == old["store_id"],
                "store registration changed during transition")
        validate_entry(new)
        with staged_local_file(device, "registry.json", registry_update(header, new)) as staged:
            sync_named(device, staged)
            with open_directory(state, ("stores", "v1")) as current:
                require(os.path.samestat(os.fstat(current), os.fstat(device)) and read_registry(state)[0] == header,
                        "registry changed during transition")
            verify(old)
            os.replace(staged, "registry.json", src_dir_fd=device, dst_dir_fd=device)
            sync_local(device)
            verify(new)


def mode_records(recovery, account, store_id, pointer):
    active, pending, retired, retries, inflight = [], [], {}, {}, []
    try:
        control_fd = os.open("control", os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=recovery)
    except FileNotFoundError:
        require(pointer is None, "mode transition evidence missing")
        return None, pending, inflight
    try:
        require_private_directory(control_fd, "recovery controls")
        for ident in os.listdir(control_fd):
            require(valid_id(ident), "unknown recovery control")
            with open_directory(control_fd, (ident,)) as folder:
                require_private_directory(folder, "recovery control")
                scope, terminal = classify(folder, account, ident, "control")
                if store_id not in scope:
                    continue
                with open_directory(folder, ("records",)) as history:
                    first = private_json(history, "1.json")
                    tail = private_json(history, f"{len(os.listdir(history))}.json")
                if first["operation"] == "registration-transition":
                    if terminal and tail["detail"]["phase"] == "applied":
                        for candidate in decision(recovery, first)["candidates"]:
                            retired[candidate["attempt_id"]] = candidate
                    if not terminal or pointer == ident:
                        active.append((first, tail))
                elif not terminal:
                    inflight.append(ident)
                else:
                    with open_directory(recovery, ("edits", ident)) as edit:
                        try:
                            retry = private_json(edit, "retry.json")
                        except FileNotFoundError:
                            retry = None
                    if retry:
                        fields(retry, "original_attempt")
                        require(valid_id(retry["original_attempt"]), "invalid retry evidence")
                        origin = candidate_record(recovery, account, store_id, retry["original_attempt"])
                        verify_retry(recovery, account, first, origin)
                        retries[ident] = (origin, tail["detail"]["outcome"])
                    elif tail["detail"]["outcome"] == "not-accepted":
                        pending.append(ident)
    finally:
        os.close(control_fd)
    require(len(active) <= 1 and (pointer is None or active and active[0][0]["record_id"] == pointer),
            "mode transition needs reconciliation")
    for origin, outcome in retries.values():
        if outcome == "accepted":
            retired[origin["attempt_id"]] = origin
    for ident, saved in retired.items():
        require(ident in pending and candidate_record(recovery, account, store_id, ident) == saved,
                "retired publication evidence disagrees")
    return active[0] if active else None, sorted(set(pending) - retired.keys()), sorted(inflight)


def decision(recovery, first):
    with open_directory(recovery, ("modes", first["record_id"])) as folder:
        require_private_directory(folder, "mode evidence")
        plan = private_json(folder, first["detail"]["proposed_binding_id"])
        fields(plan, "entry decision_sha256")
        record = private_json(folder, "decision.json")
        require(hashlib.sha256(json_bytes(record)).hexdigest() == plan["decision_sha256"],
                "mode decision evidence changed")
    fields(record, "action candidates" + (" send_attempt" if record.get("action") in ("send-confirmed","drain") else ""))
    if record["action"] == "send-confirmed":
        require(valid_id(record["send_attempt"]) and len(record["candidates"]) == 1, "invalid confirmed sender")
    if record["action"] == "drain":
        require(valid_id(record["send_attempt"]) and not record["candidates"], "invalid admitted sender")
    require(record["action"] in ("clean","discard-confirmed","send-confirmed","drain") and isinstance(record["candidates"],list)
            and bool(record["candidates"]) == (record["action"] in ("discard-confirmed","send-confirmed")), "invalid mode decision")
    return record


def candidate_record(recovery, account, store, attempt, verify_bundle=False):
    original, tail = read_attempt(recovery, account, store, attempt)
    require(tail["detail"]["outcome"] == "not-accepted", "candidate was not definitively rejected")
    detail = original["detail"]
    with open_directory(recovery, ("edits", attempt)) as edit:
        evidence = attempt_binding(edit, account, original)
        if verify_bundle:
            fd = os.open("proposal.bundle", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=edit)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(fd)
                require_approval_file(fd, info)
                require(0 < info.st_size <= 64 * 1024 * 1024, "unsupported candidate bundle")
                header = [stream.readline(4096) for _ in range(4)]
                require(header[0] == b"# v2 git bundle\n" and header[1].split(b" ")[0] == b"-"+detail["base_oid"].encode()
                        and header[2] == detail["proposed_oid"].encode()+b" refs/hostbook/proposal\n"
                        and header[3] == b"\n", "candidate bundle does not match reservation")
                pack = stream.read(info.st_size+1)
                digest = hashlib.sha256(b"".join(header))
                digest.update(pack)
                require(digest.hexdigest() == evidence["bundle_sha256"], "candidate bundle digest changed")
                require(pack.startswith(b"PACK") and hashlib.sha1(pack[:-20]).digest() == pack[-20:],
                        "candidate bundle integrity check failed")
    return {"attempt_id":attempt, "binding_id":detail["connection_binding_id"], "base_oid":detail["base_oid"],
            "proposed_oid":detail["proposed_oid"], "host_id":evidence["host_id"], "ref":evidence["ref"]}



def verify_retry(recovery, account, first, source):
    detail = first["detail"]
    require(all(detail[k] == source[k] for k in ("base_oid","proposed_oid")), "retry changed candidate")
    with open_directory(recovery, ("edits", first["record_id"])) as retry:
        require(private_json(retry, "retry.json") == {"original_attempt":source["attempt_id"]}, "retry owner mismatch")
        new = attempt_binding(retry, account, first)
    with open_directory(recovery, ("edits", source["attempt_id"])) as older:
        original, _ = read_attempt(recovery, account, first["store_id"], source["attempt_id"])
        old = attempt_binding(older, account, original)
    require(all(old[k] == new[k] for k in ("store_id","device_id","target_id","ref","bare_repository","uid","host_id","bundle_sha256"))
            and old["generations"]["connection"] == new["generations"]["connection"], "retry binding changed")


def preflight_mode(state, entry, wanted):
    header, _ = read_registry(state)
    current = entry["policy_generation"]
    fenced = dict(entry, transition_id="f"*36, policy_generation=current+1)
    for steps, future in ((1,fenced), (2,dict(fenced,effective_mode=wanted,policy_generation=current+2)),
                          (2,dict(entry,policy_generation=current+2)), (3,dict(entry,effective_mode=wanted,
                          policy_generation=current+3))):
        registry_update(header, future, steps)


def commit_mode_control(recovery, first):
    with open_directory(recovery, ("modes", first["record_id"])) as evidence:
        temp = str(uuid4())
        os.mkdir(temp, 0o700, dir_fd=evidence)
        with open_directory(evidence, (temp,)) as draft:
            store_publish.reserve(draft, recovery, first)


def recover_sender_links(recovery, account, store):
    try:
        drafts = os.open("mode-links", os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=recovery)
    except FileNotFoundError:
        return
    try:
        if store not in os.listdir(drafts):
            return
        with open_directory(drafts, (store,)) as links:
            require_private_directory(links, "mode links")
            for ident in os.listdir(links):
                require(valid_id(ident), "invalid mode link")
                first = private_json(links, ident)
                require(first["device_id"] == account.device_id and first["root_id"] == account.recovery_root_id
                        and first["store_id"] == store and first["record_id"] == ident and first["version"] == 1
                        and first["sequence"] == 1 and first["previous_sequence"] == 0
                        and first["operation"] == "registration-transition","invalid prepared mode owner")
                check_owner(first["owner"])
                validate_control(first,None)
                choice = decision(recovery, first)
                require(choice["action"] == "send-confirmed","invalid prepared mode decision")
                with open_directory(recovery, ("control",)) as controls:
                    existing = os.listdir(controls)
                    if ident in existing or choice["send_attempt"] not in existing:
                        continue
                sender, _ = read_attempt(recovery,account,store,choice["send_attempt"])
                verify_retry(recovery,account,sender,choice["candidates"][0])
                commit_mode_control(recovery,first)
    finally:
        os.close(drafts)


def prepare_transition(recovery, account, entry, wanted, action, candidates, send_attempt=None):
    ident = str(uuid4())
    old_id, new_id = str(uuid4()), str(uuid4())
    rec = {"format":"hostbook-control", "version":1, "record_id":ident, "device_id":account.device_id,
           "root_id":account.recovery_root_id, "sequence":1, "previous_sequence":0, "store_id":entry["store_id"],
           "owner":{"owner_id":str(uuid4()), "process_id":os.getpid(), "process_start_id":str(uuid4()), "boot_id":boot_id()},
           "operation":"registration-transition", "detail":{"old_binding_id":old_id, "proposed_binding_id":new_id,
           "phase":"fenced"}}
    with make_parents(recovery, ("modes",)) as modes:
        require_private_directory(modes, "mode evidence")
        os.mkdir(ident, 0o700, dir_fd=modes)
        sync_local(modes)
        with open_directory(modes, (ident,)) as evidence:
            choice = {"action":action, "candidates":candidates}
            if send_attempt is not None:
                choice["send_attempt"] = send_attempt
            saved = json_bytes(choice)
            durable_file(evidence, "decision.json", saved)
            durable_file(evidence, old_id, json_bytes(entry))
            proposed = dict(entry, effective_mode=wanted, policy_generation=entry["policy_generation"]+3)
            durable_file(evidence, new_id, json_bytes({"entry":proposed,"decision_sha256":hashlib.sha256(saved).hexdigest()}))
            sync_local(evidence)
            if action == "send-confirmed":
                with make_parents(recovery, ("mode-links",entry["store_id"])) as links:
                    with staged_local_file(evidence, ident, json_bytes(rec)) as temp:
                        sync_named(evidence,temp)
                        rename_guarded(evidence,temp,links,ident,exchange=False)
                        sync_local(links)
            else:
                commit_mode_control(recovery,rec)
    return rec, rec


def finish_record(recovery, first, tail, phase):
    with open_directory(recovery, ("control", first["record_id"])) as control:
        if tail["sequence"] == 1:
            tail = dict(first, sequence=2, previous_sequence=1, detail=dict(first["detail"], phase=phase))
            with open_directory(control, ("records",)) as history:
                with staged_local_file(control, "2.json", json_bytes(tail)) as temp:
                    sync_named(control, temp)
                    rename_guarded(control, temp, history, "2.json", exchange=False)
                    sync_local(history)
        require(tail["detail"]["phase"] == phase, "transition decision changed")
        select_record(control, tail)


def apply_transition(state, recovery, account, entry, active, requested, verify):
    first, tail = active
    ident, detail = first["record_id"], first["detail"]
    with open_directory(recovery, ("modes", ident)) as evidence:
        require_private_directory(evidence, "mode evidence")
        old = private_json(evidence, detail["old_binding_id"])
        proposed = private_json(evidence, detail["proposed_binding_id"])["entry"]
        choice = decision(recovery, first)
    validate_entry(old)
    validate_entry(proposed)
    wanted = proposed["effective_mode"]
    require(old["store_id"] == entry["store_id"] and old["kind"] == "shared" and old["transition_id"] is None
            and old["effective_mode"] in ("two-way", "receive-only") and wanted in ("two-way", "receive-only")
            and (wanted != old["effective_mode"] or (wanted == "two-way" and choice["action"] in
                ("send-confirmed","discard-confirmed"))) and proposed == dict(old, effective_mode=wanted,
                policy_generation=old["policy_generation"]+3), "invalid mode plan")
    fenced = dict(old, transition_id=ident, policy_generation=old["policy_generation"]+1)
    changed = dict(fenced, effective_mode=wanted, policy_generation=fenced["policy_generation"]+1)
    cancelled = dict(old, policy_generation=fenced["policy_generation"]+1)
    early_cancel = dict(old,policy_generation=old["policy_generation"]+1)
    require(requested in (wanted, "cancel") and entry in (old, fenced, changed, early_cancel), "mode plan and registration disagree")
    sender, outcome = None, None
    if choice["action"] in ("send-confirmed","drain"):
        sender, current = read_attempt(recovery, account, entry["store_id"], choice["send_attempt"])
        if choice["action"] == "send-confirmed":
            verify_retry(recovery, account, sender, choice["candidates"][0])
        else:
            with open_directory(recovery, ("edits",sender["record_id"])) as edit:
                attempt_binding(edit,account,sender)
        outcome = current["detail"]["outcome"]
        with open_directory(recovery, ("control",sender["record_id"])) as control:
            if private_json(control, "current.json")["sequence"] != current["sequence"]:
                outcome = "uncertain"
    require_idle(recovery, account, entry["store_id"], transition=first, sender=sender)
    verify(entry)
    if entry == early_cancel or (entry == old and requested == "cancel" and outcome not in ("reserved","uncertain")):
        if entry == old:
            replace_entry(state, account, old, early_cancel, verify)
        finish_record(recovery, first, tail, "cancelled")
        return early_cancel
    if entry == old:
        preflight_mode(state, entry, wanted)
        replace_entry(state, account, old, fenced, verify)
        entry = fenced
    if outcome in ("reserved","uncertain"):
        require(entry == fenced, "unsettled sender crossed the publication fence")
        return entry
    pending_after_drain = choice["action"] == "drain" and mode_records(recovery, account, entry["store_id"], ident)[1]
    if tail["detail"]["phase"] == "cancelled" or (entry == fenced and (requested == "cancel" or
                                                  outcome == "not-accepted" or pending_after_drain)):
        require(entry == fenced, "cancelled transition changed its effective mode")
        finish_record(recovery, first, tail, "cancelled")
        replace_entry(state, account, fenced, cancelled, verify)
        return cancelled
    if entry == fenced:
        require(tail["detail"]["phase"] == "fenced", "effective transition update missing")
        replace_entry(state, account, fenced, changed, verify)
    finish_record(recovery, first, tail, "applied")
    replace_entry(state, account, changed, proposed, verify)
    return proposed


def change_mode(load_account, store_id, expected, wanted, action="clean", confirmed=()):
    require(wanted in ("two-way", "receive-only", "cancel"), "unknown sync mode")
    keep_mode = action in ("retry","discard")
    if keep_mode:
        require(wanted == "two-way" and len(confirmed) == 1 and valid_id(confirmed[0]),
                "confirm the retained edit in two-way mode")
        action = "send-confirmed" if action == "retry" else "discard-confirmed"
    if action == "send-confirmed":
        require(wanted in ("two-way","receive-only") and len(confirmed) == 1 and valid_id(confirmed[0]),
                "confirm one exact candidate to send")
        return send_confirmed(load_account, store_id, expected, confirmed[0], wanted)
    require(action in ("clean", "discard-confirmed") and (action == "clean" or confirmed)
            and all(valid_id(ident) for ident in confirmed),
            "invalid mode decision")
    with store_writer(load_account, store_id, expected) as (account, context, state, writer):
        entries, _ = registry_entries(state, account)
        entry = entries[store_id]
        require(context.kind == "shared" and entry["connection"] and entry_generations(entry) == expected,
                "a current connected shared store is required")
        require(not keep_mode or context.effective_mode == wanted, "the effective mode changed")
        with recovery_root(account) as recovery:
            recover_sender_links(recovery, account, store_id)
            active, pending, inflight = mode_records(recovery, account, store_id, entry["transition_id"])
            if active is None and inflight and wanted != "cancel":
                require(len(inflight)==1 and action == "clean", "reconcile active publishers before changing mode")
                sender, _ = read_attempt(recovery,account,store_id,inflight[0])
                with open_directory(recovery, ("edits",inflight[0])) as edit:
                    attempt_binding(edit,account,sender)
                if wanted == entry["effective_mode"]:
                    return {"store_id":store_id, "mode":wanted, "settle_attempt":inflight[0],
                            "generations":asdict(expected)}
                preflight_mode(state,entry,wanted)
                active = prepare_transition(recovery,account,entry,wanted,"drain",[],inflight[0])
            if active is None:
                if wanted == "cancel":
                    return {"store_id":store_id, "mode":entry["effective_mode"], "generations":asdict(expected)}
                with ExitStack() as checked:
                    if pending:
                        # Reviewing or retiring private candidate evidence needs no active transport.
                        require_idle(recovery,account,store_id)
                        namespace = checked.enter_context(open_directory(state,("stores","v1",store_id)))
                        require_private_directory(namespace,"store state")
                        require_no_receive_pending(namespace,None)
                    else:
                        checked.enter_context(receive_binding(account,store_id,expected))
                    if wanted != entry["effective_mode"] or pending or confirmed:
                        candidates = [candidate_record(recovery, account, store_id, a, verify_bundle=True) for a in pending]
                        if pending and action == "clean":
                            if wanted == entry["effective_mode"] and len(pending) == 1:
                                choices = ["retry","discard","cancel"]
                            else:
                                choices = (["send-then-switch"] if len(pending)==1 else []) + ["discard-then-switch","cancel"]
                            return {"store_id":store_id, "mode":entry["effective_mode"], "pending":pending,
                                    "candidates":candidates, "choices":choices,
                                    "generations":asdict(expected)}
                        require(sorted(confirmed) == pending and (action != "clean") == bool(confirmed),
                                "confirm the exact pending publication candidates")
                        preflight_mode(state,entry,wanted)
                        active = prepare_transition(recovery, account, entry, wanted, action, candidates)
            if active is not None:
                def verify(current):
                    verify_live(load_account, account, state, writer, current)
                entry = apply_transition(state, recovery, account, entry, active, wanted, verify)
                verify(entry)
            result = {"store_id":store_id, "mode":entry["effective_mode"], "generations":asdict(entry_generations(entry))}
            if entry["transition_id"]:
                result["transition_id"] = entry["transition_id"]
                result["settle_attempt"] = decision(recovery,active[0])["send_attempt"]
            return result


def send_confirmed(load, store_id, expected, source, wanted):
    account = load()
    with recovery_root(account) as recovery, ExitStack() as opened:
        with store_writer(load, store_id, expected) as (actual, ctx, state, _):
            require(actual == account and ctx.effective_mode == "two-way" and ctx.transition_id is None,
                    "send before switching requires an unfenced two-way registration")
            recover_sender_links(recovery,account,store_id)
            active, pending, _ = mode_records(recovery, account, store_id, None)
            require(not active and pending == [source], "confirm the exact pending candidate")
            candidate = candidate_record(recovery, account, store_id, source, verify_bundle=True)
            local = local_inputs(state, store_id)
        host, tip, proposed = (candidate[k] for k in ("host_id", "base_oid", "proposed_oid"))
        bound = opened.enter_context(receive_binding(account, store_id, expected))
        current, target, target_fd, _, checkout = bound
        require(current == ctx, "registered sender changed")
        with open_directory(recovery, ("edits",source)) as original:
            stored, _ = read_attempt(recovery, account, store_id, source)
            evidence = attempt_binding(original, account, stored)
            require(evidence["ref"] == ctx.ref and evidence["target_id"] == ctx.target_id
                    and evidence["bare_repository"] == dict(asdict(target),path=str(target.path))
                    and evidence["generations"]["connection"] == expected.connection,
                    "confirmed candidate belongs to another connection")
            stage = Path(opened.enter_context(tempfile.TemporaryDirectory(prefix="confirmed-",dir=account.state.path)))
            bundle = stage/"proposal.bundle"
            fd = os.open("proposal.bundle", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=original)
            with os.fdopen(fd,"rb") as src, bundle.open("wb") as dest:
                require_approval_file(fd, os.fstat(fd))
                shutil.copyfileobj(src,dest)
        git(stage,"init","-q","--bare","--template=")
        store_publish.require_plain_receiver(target_fd)
        require(direct_ref(target_fd,ctx.ref) == tip and fetch_from_fd(stage,target_fd,ctx.ref,"refs/hostbook/base") == tip,
                "confirmed candidate base changed")
        git(stage,"bundle","verify",str(bundle))
        git(stage,"fetch","--no-tags","--",str(bundle),"refs/hostbook/proposal:refs/hostbook/proposal")
        require(oid(stage,"refs/hostbook/proposal") == proposed and
                git(stage,"show","-s","--format=%P",proposed).strip().decode() == tip,"candidate does not match reservation")
        verify_incoming(stage,proposed,tip,store_id,local)
        signature = store_publish.record_at(stage,tip,store_id,host)[4]
        with store_writer(load,store_id,expected) as (actual, locked, state, writer):
            require(actual == account and locked == ctx, "registered sender changed")
            with receive_binding(account,store_id,expected) as (again, remote, _, _, binding):
                require((again,remote,binding) == (ctx,target,checkout) and local_inputs(state,store_id)==local,
                        "registered sender changed")
                active, pending, _ = mode_records(recovery,account,store_id,None)
                require(not active and pending == [source] and candidate_record(recovery,account,store_id,source,verify_bundle=True)==candidate,
                        "confirmed candidate changed")
                with absolute_directory(ctx.checkout) as work:
                    info = os.fstat(work)
                    require((info.st_dev,info.st_ino)==(binding.root.device,binding.root.inode),"checkout changed")
                    files = {}
                    identity, _ = index_rows(work,store_id,files)
                    require(store_publish.stanza(identity,files.__getitem__,store_id,host)[4] == signature,
                            "confirmed local host revision changed")
                attempt = str(uuid4())
                entry = registry_entries(state,account)[0][store_id]
                preflight_mode(state,entry,wanted)
                active = prepare_transition(recovery,account,entry,wanted,"send-confirmed",[candidate],attempt)
                reservation = store_publish.reserve_edit(opened,recovery,account,ctx,target,host,tip,proposed,bundle,
                                                         attempt,str(uuid4()),retry_of=source)
                commit_mode_control(recovery,active[0])
                def verify(row):
                    verify_live(load,account,state,writer,row)
                fenced = apply_transition(state,recovery,account,entry,active,wanted,verify)
                require(fenced["transition_id"] == active[0]["record_id"] and fenced["effective_mode"] == "two-way",
                        "sender needs its original publication fence")
        generations = entry_generations(fenced)
        result = store_publish.launch_reserved(load,account,recovery,ctx,generations,stage,target_fd,reservation)
    mode = change_mode(load,store_id,generations,wanted)
    mode.update(result)
    if mode["outcome"] == "accepted" and mode["mode"] == wanted:
        from store_context import StoreContextError
        try:
            receive(load,store_id,Generations(**mode["generations"]))
            mode["local"] = "received"
        except (StoreContextError,OSError):
            mode["local"] = "reconciliation-required"
    return mode


def mode_command(action, args):
    if action == "_store_mode":
        require(len(args) >= 1, "one requested sync mode is required")
        account, store_id, expected = fixture_request()
        def load():
            return account
    else:
        require(len(args) >= 6, "store ID, four generations and requested mode are required")
        store_id, expected = args[0], Generations(*(int(x) for x in args[1:5]))
        args = args[5:]
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
        def load():
            return load_approved_account(home)
    result = change_mode(load, store_id, expected, args[0], args[1] if len(args) > 1 else "clean", args[2:])
    print(json.dumps(result))
    return 75 if "pending" in result or "settle_attempt" in result or result.get("outcome","accepted") != "accepted" else 0
