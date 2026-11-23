"""Change a registered shared store's effective mode through its durable fence."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import pwd
from uuid import uuid4

from store_checkout import durable_file, make_parents, rename_guarded
from store_context import (Generations, acquire_local_lock, entry_generations, fields, fixture_request, load_approved_account,
                           open_directory, read_registry, registry_entries, require, require_private_directory,
                           staged_local_file, store_writer, sync_local, valid_id, validate_entry)
from store_journal import classify, private_json, require_idle
from store_publish import boot_id, json_bytes, recovery_root, reserve, select_record, sync_named
from store_receive import receive_binding


def replace_entry(state, account, old, new):
    with open_directory(state, ("stores", "v1")) as device:
        acquire_local_lock(device, "device registry is busy")
        header, _ = read_registry(state)
        entries, _ = registry_entries(state, account)
        require(entries.get(old["store_id"]) == old and new["store_id"] == old["store_id"],
                "store registration changed during transition")
        validate_entry(new)
        replacement = dict(header, revision=header["revision"]+1,
                           stores=[new if row["store_id"] == old["store_id"] else row for row in header["stores"]])
        with staged_local_file(device, "registry.json", json_bytes(replacement)) as staged:
            sync_named(device, staged)
            with open_directory(state, ("stores", "v1")) as current:
                require(os.path.samestat(os.fstat(current), os.fstat(device)) and read_registry(state)[0] == header,
                        "registry changed during transition")
            os.replace(staged, "registry.json", src_dir_fd=device, dst_dir_fd=device)
            sync_local(device)


def mode_records(recovery, account, store_id, pointer):
    active, pending = [], []
    try:
        control_fd = os.open("control", os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=recovery)
    except FileNotFoundError:
        require(pointer is None, "mode transition evidence missing")
        return None, pending
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
                    if not terminal or pointer == ident:
                        active.append((first, tail))
                elif terminal and tail["detail"]["outcome"] == "not-accepted":
                    pending.append(ident)
    finally:
        os.close(control_fd)
    require(len(active) <= 1 and (pointer is None or active and active[0][0]["record_id"] == pointer),
            "mode transition needs reconciliation")
    return active[0] if active else None, sorted(pending)


def prepare_transition(recovery, account, entry, wanted):
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
            durable_file(evidence, old_id, json_bytes(entry))
            proposed = dict(entry, effective_mode=wanted, policy_generation=entry["policy_generation"]+3)
            durable_file(evidence, new_id, json_bytes(proposed))
            sync_local(evidence)
            reserve(evidence, recovery, rec)
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


def apply_transition(state, recovery, account, entry, active, requested):
    first, tail = active
    ident, detail = first["record_id"], first["detail"]
    with open_directory(recovery, ("modes", ident)) as evidence:
        require_private_directory(evidence, "mode evidence")
        old, proposed = (private_json(evidence, detail[k]) for k in ("old_binding_id", "proposed_binding_id"))
    validate_entry(old)
    validate_entry(proposed)
    wanted = proposed["effective_mode"]
    require(old["store_id"] == entry["store_id"] and old["kind"] == "shared" and old["transition_id"] is None
            and old["effective_mode"] in ("two-way", "receive-only") and wanted in ("two-way", "receive-only")
            and wanted != old["effective_mode"] and proposed == dict(old, effective_mode=wanted,
                policy_generation=old["policy_generation"]+3), "invalid mode plan")
    fenced = dict(old, transition_id=ident, policy_generation=old["policy_generation"]+1)
    changed = dict(fenced, effective_mode=wanted, policy_generation=fenced["policy_generation"]+1)
    cancelled = dict(old, policy_generation=fenced["policy_generation"]+1)
    require(requested in (wanted, "cancel") and entry in (old, fenced, changed), "mode plan and registration disagree")
    require_idle(recovery, account, entry["store_id"], transition=first)
    if entry == old:
        replace_entry(state, account, old, fenced)
        entry = fenced
    if tail["detail"]["phase"] == "cancelled" or (requested == "cancel" and entry == fenced):
        require(entry == fenced, "cancelled transition changed its effective mode")
        finish_record(recovery, first, tail, "cancelled")
        replace_entry(state, account, fenced, cancelled)
        return cancelled
    if entry == fenced:
        require(tail["detail"]["phase"] == "fenced", "effective transition update missing")
        replace_entry(state, account, fenced, changed)
    finish_record(recovery, first, tail, "applied")
    replace_entry(state, account, changed, proposed)
    return proposed


def change_mode(load_account, store_id, expected, wanted):
    require(wanted in ("two-way", "receive-only", "cancel"), "unknown sync mode")
    with store_writer(load_account, store_id, expected) as (account, context, state, _):
        entries, _ = registry_entries(state, account)
        entry = entries[store_id]
        require(context.kind == "shared" and entry["connection"] and entry_generations(entry) == expected,
                "a current connected shared store is required")
        with recovery_root(account) as recovery:
            active, pending = mode_records(recovery, account, store_id, entry["transition_id"])
            if active is None:
                if wanted == "cancel":
                    return {"store_id":store_id, "mode":entry["effective_mode"], "generations":asdict(expected)}
                with receive_binding(account, store_id, expected):
                    if wanted != entry["effective_mode"]:
                        if pending:
                            return {"store_id":store_id, "mode":entry["effective_mode"], "pending":pending,
                                    "generations":asdict(expected)}
                        active = prepare_transition(recovery, account, entry, wanted)
            if active is not None:
                entry = apply_transition(state, recovery, account, entry, active, wanted)
            return {"store_id":store_id, "mode":entry["effective_mode"], "generations":asdict(entry_generations(entry))}


def mode_command(action, args):
    if action == "_store_mode":
        require(len(args) == 1, "one requested sync mode is required")
        account, store_id, expected = fixture_request()
        def load():
            return account
    else:
        require(len(args) == 6, "store ID, four generations and requested mode are required")
        store_id, expected = args[0], Generations(*(int(x) for x in args[1:5]))
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
        def load():
            return load_approved_account(home)
    result = change_mode(load, store_id, expected, args[-1])
    print(json.dumps(result))
    return 75 if "pending" in result else 0
