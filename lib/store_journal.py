"""Classify verified local journal authority before a registered store effect."""
import os
import re

from store_context import (JSON_LIMIT, StoreContextError, fields, open_directory, parse_json, positive, read_file,
                           require, require_approval_file, require_private_directory, valid_id)


def private_json(parent, name):
    return parse_json(read_file(parent, name, JSON_LIMIT, require_approval_file))


def primary_id(record, key):
    require(valid_id(record[key]), "invalid recovery identity")
    return record[key]


def check_owner(owner):
    fields(owner, "owner_id process_id process_start_id boot_id")
    require(positive(owner["process_id"]) and all(valid_id(owner[k]) for k in ("owner_id", "process_start_id", "boot_id")),
            "invalid recovery owner")


def attempt_outcome(detail):
    sender, outcome = detail["sender_state"], detail["outcome"]
    require(sender in ("active", "unknown", "quiescent") and
            outcome in ("reserved", "uncertain", "accepted", "not-accepted"), "invalid publication state")
    if outcome in ("accepted", "not-accepted"):
        require(sender == "quiescent", "publication is not drained")
        return True
    return False


def control(record, prior):
    fields(record, "format version record_id device_id root_id sequence previous_sequence store_id owner operation detail")
    require(record["format"] == "hostbook-control", "invalid control record")
    store = primary_id(record, "store_id")
    detail = record["detail"]
    if prior:
        require(all(prior[k] == record[k] for k in ("store_id", "operation", "owner")), "control binding changed")
    if record["operation"] == "registration-transition":
        fields(detail, "old_binding_id proposed_binding_id phase")
        require(valid_id(detail["old_binding_id"]) and valid_id(detail["proposed_binding_id"])
                and detail["old_binding_id"] != detail["proposed_binding_id"], "invalid transition bindings")
        phase = detail["phase"]
        require(phase in ("fenced", "applied", "cancelled") and
                (prior is not None or phase == "fenced"), "invalid transition phase")
        if prior:
            old = prior["detail"]
            require(old["phase"] == "fenced" and all(old[k] == detail[k] for k in ("old_binding_id", "proposed_binding_id")),
                    "invalid transition progression")
        return {store}, phase in ("applied", "cancelled")
    require(record["operation"] == "publish", "unknown control operation")
    fields(detail, "attempt_id connection_generation connection_binding_id base_oid proposed_oid sender_state outcome")
    for name in ("attempt_id", "connection_binding_id"):
        primary_id(detail, name)
    require(positive(detail["connection_generation"]) and all(isinstance(detail[k], str)
            and re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", detail[k]) for k in ("base_oid", "proposed_oid"))
            and len(detail["base_oid"]) == len(detail["proposed_oid"]) and detail["base_oid"] != detail["proposed_oid"],
            "invalid publication binding")
    drained = attempt_outcome(detail)
    if prior:
        old = prior["detail"]
        require(not attempt_outcome(old) and all(old[k] == detail[k] for k in detail
                if k not in ("sender_state", "outcome")) and not
                (old["outcome"] == "uncertain" and detail["outcome"] == "reserved"), "invalid publication progression")
    else:
        require(detail["outcome"] == "reserved" and detail["sender_state"] == "active", "missing publication reservation")
    return {store}, drained


TRANSFER = ("prepared", "destination-saved", "destination-confirmed", "source-removed-local", "source-confirmed", "completed")
MIGRATION = ("prepared", "staged", "verified", "cutover-local", "publication-pending", "completed")
TERMINAL = ("completed", "cancelled", "rolled-back")


def unique_rows(value, key):
    require(isinstance(value, list), "invalid recovery list")
    ids = [primary_id(row, key) for row in value]
    require(len(ids) == len(set(ids)), "duplicate recovery identity")
    return value


def transaction(record, prior):
    fields(record, "format version transaction_id device_id root_id sequence previous_sequence operation checkpoint "
                   "owner stores hosts payload_id recovery_generation effects attempts collection")
    op, state = record["operation"], record["checkpoint"]
    require(record["format"] == "hostbook-transaction" and op in ("transfer", "migration")
            and positive(record["recovery_generation"]), "invalid transaction")
    route = TRANSFER if op == "transfer" else MIGRATION
    require(state in (*route, "cancelled", "rolled-back", "rollback-pending") and
            (prior or state == "prepared"), "invalid transaction checkpoint")
    stores = set()
    for item in unique_rows(record["stores"], "store_id"):
        fields(item, "store_id role snapshot_id checkout_generation connection_generation identity_generation vault_generation")
        require(item["role"] in ("source", "destination", "affected") and valid_id(item["snapshot_id"])
                and all(positive(item[k]) for k in ("checkout_generation", "connection_generation"))
                and all(type(item[k]) is int and item[k] >= 0 for k in ("identity_generation", "vault_generation")),
                "invalid transaction store")
        stores.add(item["store_id"])
    require(stores and isinstance(record["hosts"], list), "missing transaction stores or hosts")
    hosts = set()
    for item in record["hosts"]:
        fields(item, "store_id host_id")
        key = item["store_id"], primary_id(item, "host_id")
        require(key[0] in stores and key not in hosts, "invalid transaction host")
        hosts.add(key)
    require(op == "migration" or (hosts and len(stores) == 2 and
            {item["role"] for item in record["stores"]} == {"source", "destination"}), "invalid transfer scope")
    if prior:
        older = prior["checkpoint"]
        require(prior["operation"] == op and {item["store_id"] for item in prior["stores"]} == stores
                and (older not in TERMINAL or state == older)
                and (state not in route or older == state or older in route and route.index(state) == route.index(older) + 1),
                "invalid transaction progression")
    for effect in unique_rows(record["effects"], "effect_id"):
        fields(effect, "effect_id store_id intent_id evidence_id outcome")
        require(effect["store_id"] in stores and valid_id(effect["intent_id"])
                and (effect["evidence_id"] is None or valid_id(effect["evidence_id"]))
                and effect["outcome"] in ("intended", "observed", "blocked", "reconciled"), "invalid transaction effect")
    drained = True
    for attempt in unique_rows(record["attempts"], "attempt_id"):
        fields(attempt, "attempt_id store_id owner_id connection_generation reservation_id target_id intent_id "
                        "evidence_id sender_state outcome")
        require(attempt["store_id"] in stores and positive(attempt["connection_generation"])
                and all(valid_id(attempt[k]) for k in ("owner_id", "reservation_id", "target_id", "intent_id"))
                and (attempt["evidence_id"] is None or valid_id(attempt["evidence_id"])), "invalid transaction attempt")
        drained = attempt_outcome(attempt) and drained
    collection = record["collection"]
    fields(collection, "decision_id payload_state payload_ids recovery_generations")
    collected = collection["payload_state"]
    require(collected in ("retained", "collection-intended", "collected") and
            (record["payload_id"] is None if collected == "collected" else valid_id(record["payload_id"]))
            and (collection["decision_id"] is None or valid_id(collection["decision_id"])), "invalid retained payload")
    for k, valid in (("payload_ids", valid_id), ("recovery_generations", positive)):
        items = collection[k]
        require(isinstance(items, list) and all(valid(item) for item in items)
                and len(items) == len(set(items)), "invalid collection manifest")
    if prior:
        for category, identifier in (("attempts", "attempt_id"), ("effects", "effect_id")):
            latest = {row[identifier]: row for row in record[category]}
            for old in prior[category]:
                new = latest.get(old[identifier])
                require(new and all(old[k] == new[k] for k in old if k not in ("evidence_id", "sender_state", "outcome")),
                        "transaction evidence changed identity")
                if category == "attempts" and attempt_outcome(old):
                    require(old == new, "settled transaction attempt changed")
    # This reader can prove cancellation before any data effect. Completion and compensation
    # of performed effects need the protected outcome evidence of their recovery worker.
    complete = (state == "cancelled" and prior is not None and prior["checkpoint"] in ("prepared", "cancelled")
                and drained and not record["effects"] and not record["attempts"] and collected == "retained"
                and not collection["payload_ids"] and not collection["recovery_generations"]
                and collection["decision_id"] is None)
    # Provisional migration claims may overlap a checkout before it has a registry row.
    return (None if op == "migration" and not complete else stores), complete


def classify(folder, account, ident, kind):
    key = "record_id" if kind == "control" else "transaction_id"
    selector = private_json(folder, "current.json")
    fields(selector, "version " + key + " sequence")
    require(type(selector["version"]) is int and selector["version"] == 1 and selector[key] == ident
            and positive(selector["sequence"]), "invalid recovery selector")
    with open_directory(folder, ("records",)) as history:
        require_private_directory(history, "recovery revisions")
        names = set(os.listdir(history))
        require(all(re.fullmatch(r"[1-9][0-9]*\.json", name) for name in names)
                and selector["sequence"] <= len(names), "incomplete recovery history")
        prior = None
        unsettled_terminal = False
        affected = set()
        for seq in range(1, len(names) + 1):
            record = private_json(history, f"{seq}.json")
            require(record[key] == ident and type(record["version"]) is int and record["version"] == 1
                    and type(record["sequence"]) is int and record["sequence"] == seq
                    and type(record["previous_sequence"]) is int and record["previous_sequence"] == seq-1
                    and record["device_id"] == account.device_id and record["root_id"] == account.recovery_root_id,
                    "invalid recovery revision")
            check_owner(record["owner"])
            scope, terminal = (control if kind == "control" else transaction)(record, prior)
            if kind == "transactions" and record["checkpoint"] in TERMINAL and not terminal:
                unsettled_terminal = True
                if scope is None:
                    affected = None
            if seq >= selector["sequence"]:
                if scope is None:
                    affected = None
                elif affected is not None:
                    affected.update(scope)
            prior = record
        # A later unselected revision cannot narrow the scope of the selected authority.
        return affected, terminal and not unsettled_terminal and selector["sequence"] == len(names)


def require_idle(recovery_fd, account, store_id):
    for kind in ("transactions", "control"):
        try:
            container = os.open(kind, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=recovery_fd)
        except FileNotFoundError:
            continue
        try:
            require_private_directory(container, "recovery records")
            for ident in os.listdir(container):
                require(valid_id(ident), "unknown recovery record")
                with open_directory(container, (ident,)) as folder:
                    require_private_directory(folder, "recovery record")
                    try:
                        scope, terminal = classify(folder, account, ident, kind)
                    except (TypeError, KeyError) as exc:
                        raise StoreContextError("invalid recovery record") from exc
                    require(terminal or (scope is not None and store_id not in scope),
                            "store has unresolved recovery")
        finally:
            os.close(container)
