"""Wire the registered host picker to the exact transfer review and recovery commands."""
from contextlib import redirect_stdout
from dataclasses import astuple
import io
import json

from store_context import StoreContextError, require, valid_id
from store_overview import choose
from store_transfer import preview
from store_transfer_command import transfer_command


def move_host(load, screen, source, destination):
    plan = preview(load, *source, *destination)
    comparison = plan.comparison()
    name = comparison["destination"]["proposed_alias"]
    while True:
        result = screen("_store_overview_transfer_alias", name)
        if result.returncode in (1, 130):
            return
        require(result.returncode == 0, "transfer alias input failed")
        name = result.stdout.strip()
        plan = preview(load, *source, *destination, alias=name)
        comparison = plan.comparison()
        choices, matches = comparison["choices"], comparison["matches"]
        decision = ()
        if "new" in choices:
            break
        actions = []
        for match in matches:
            if "endpoint" not in match["reasons"]:
                continue
            for kind in ("update", "keep-both"):
                if kind in choices and (kind != "update" or match["name"] == name):
                    actions.append((kind, match["name"], match["host_id"]))
        if "reconcile" in choices:
            screen("_store_overview_result", "transfer_reconcile")
            return
        fields = [f"{kind}|{alias} · {key}" for kind, alias, key in actions] + ["rename|", "cancel|"]
        picked = choose(screen, "_store_overview_transfer_duplicate",
                        {str(i) for i in range(len(fields))}, *fields)
        if picked is None or int(picked) == len(fields) - 1:
            return
        if int(picked) == len(actions):
            continue
        kind, _, key = actions[int(picked)]
        decision = (kind, key, comparison["source"]["revision"], comparison["destination"]["revision"])
        plan.choose(*decision)
        break

    credential = comparison["source"]["credential"]
    require(credential, "this transfer requires a managed source credential")
    result = screen("_store_overview_transfer_credential", credential)
    if result.returncode in (1, 130):
        return
    require(result.returncode == 0 and len(result.stdout.splitlines()) == 2, "invalid transfer credential choice")
    alias, action = result.stdout.splitlines()
    src, src_generations, host = source
    dst, dst_generations = destination
    args = [src, *map(str, astuple(src_generations)), host, dst, *map(str, astuple(dst_generations)),
            name, alias, action, *decision]
    status, records = transfer_result(args)
    transaction = next((row["transaction_id"] for row in reversed(records) if "transaction_id" in row), "")
    screen("_store_overview_result", "transferred" if status == 0 else "transfer_pending", transaction)


def transfer_result(args, *, recovery=False):
    report = io.StringIO()
    try:
        with redirect_stdout(report):
            status = transfer_command(args, recovery=recovery)
    except (OSError, StoreContextError):
        if not any("transaction_id" in json.loads(line) for line in report.getvalue().splitlines()):
            raise
        status = 75
    return status, [json.loads(line) for line in report.getvalue().splitlines()]


def recover(screen):
    result = screen("_store_overview_recovery_id")
    if result.returncode in (1, 130):
        return
    ident = result.stdout.strip()
    require(result.returncode == 0 and valid_id(ident), "invalid transfer transaction ID")
    actions = ("status", "resume", "settle", "cancel", "rollback")
    choice = choose(screen, "_store_overview_recovery_action", {str(i) for i in range(len(actions))}, ident)
    if choice is None:
        return
    code, records = transfer_result([ident, actions[int(choice)]], recovery=True)
    checkpoint = next((row["checkpoint"] for row in reversed(records) if "checkpoint" in row), "")
    screen("_store_overview_result", "transfer_pending" if code else "recovery", ident, checkpoint)
