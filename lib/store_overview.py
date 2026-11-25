"""Read and show the first-release host inventory from approved checkouts."""
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess

from store_context import (JSON_LIMIT, entry_generations, load_approved_account, registry_entries,
                           require, resolved_store, verified_account)
from store_read import connect_route, refresh_routes, route_inputs, selected_route


def overview_rows(load, checkout=None):
    account = load()
    with verified_account(account) as (_, state):
        entries, bindings = registry_entries(state, account)
    chosen = None
    if checkout is not None:
        chosen = next((key for key, b in bindings.items() if b.root.path == checkout), None)
        require(chosen is not None, "selected checkout is not registered")
    private = [key for key, row in entries.items() if row["kind"] == "private"]
    shared = [key for key, row in entries.items() if row["connection"] is not None]
    if chosen is not None and entries[chosen]["kind"] == "shared":
        shared = [chosen]
    shared_binding = (shared[0], entry_generations(entries[shared[0]])) if (
        len(shared) == 1 and entries[shared[0]]["connection"] is not None) else None
    rows, selections = [], {}
    if not shared:
        rows.append("shared-none")
    elif len(shared) > 1:
        rows.append("shared-choose")
        shared = []
    for key in private + shared:
        entry = entries[key]
        expected = entry_generations(entry)
        with resolved_store(account, key, expected) as (context, binding, root, state):
            stamp, hosts, _, _ = route_inputs(account, context, binding, root, state)
            if context.kind == "shared":
                if context.effective_mode is None:
                    rows.append(f"summary-offline\t{len(hosts)}")
                else:
                    mode = "changing" if context.transition_id else context.effective_mode
                    rows.append(f"summary-shared\t{len(hosts)}\t{mode}\t{context.ref}")
            else:
                rows.append(f"summary-private\t{len(hosts)}")
            for host in hosts:
                token = str(len(selections) + 1)
                selections[token] = (key, expected, host["host_id"], stamp, context.kind)
                rows.append(f"host\t{context.kind}\t{host['name']}\t{token}")
    require(load() == account, "store approval changed during listing")
    with verified_account(account) as (_, state):
        require(registry_entries(state, account) == (entries, bindings), "store registrations changed during listing")
    private_binding = (private[0], entry_generations(entries[private[0]])) if private else None
    return "\n".join(rows) + "\n", selections, shared_binding, private_binding


def choose(screen, action, choices, *fields):
    result = screen(action, *fields)
    if result.returncode in (1, 130):
        return None
    choice = result.stdout.strip()
    require(result.returncode == 0 and choice in choices, "invalid store screen choice")
    return choice


def shared_mode(load, screen, binding):
    from store_mode import change_mode

    modes = {"0": "receive-only", "1": "two-way"}
    choice = choose(screen, "_store_overview_mode", modes.keys() | {"2"})
    if choice not in modes:
        return None
    wanted = modes[choice]
    result = change_mode(load, *binding, wanted)
    pending = result.get("pending")
    if pending:
        decisions = {
            "retry": "retry", "discard": "discard",
            "send-then-switch": "send-confirmed", "discard-then-switch": "discard-confirmed",
        }
        offered = result["choices"]
        attempts = ", ".join(pending)
        evidence = "\n".join("\t".join(row[key] for key in ("host_id", "ref", "base_oid", "proposed_oid"))
                             for row in result["candidates"])
        choice = choose(screen, "_store_overview_pending", {str(i) for i in range(len(offered))},
                        attempts, evidence, *offered)
        if choice is None or offered[int(choice)] == "cancel":
            return None
        if offered[int(choice)].startswith("discard") and screen("_store_overview_discard_confirm", attempts, evidence).returncode:
            return None
        result = change_mode(load, *binding, wanted, decisions[offered[int(choice)]], pending)
    return ("settle" if "settle_attempt" in result or result.get("outcome", "accepted") != "accepted"
            else "mode")


def store_action(load, screen, shared, private):
    from store_private import add_private
    from store_receive import receive

    choices = (["add", "import"] if private else []) + (["receive", "mode", "disconnect"] if shared else [])
    if not choices:
        return
    choices.append("recover")
    choice = choose(screen, "_store_overview_tools", {str(i) for i in range(len(choices))}, *choices)
    if choice is None:
        return
    action = choices[int(choice)]
    if action in ("add", "import"):
        result = screen("_store_overview_private_input", action)
        if result.returncode in (1, 130):
            return
        require(result.returncode == 0, "private store input failed")
        if action == "add":
            name, address = result.stdout.splitlines()
            require(not any(c.isspace() for c in name+address), "invalid private host fields")
            document = f"Host {name}\n  HostName {address}\n".encode()
        else:
            path = Path(result.stdout.rstrip("\n")).expanduser()
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
                require(stat.S_ISREG(os.fstat(source.fileno()).st_mode), "expected an OpenSSH configuration file")
                document = source.read(JSON_LIMIT + 1)
        add_private(load, document, destination=private)
        outcome = "private"
    elif action == "receive":
        from store_login import prompt_passphrase

        def unlock():
            result = screen("_store_overview_vault_prompt")
            require(result.returncode == 0 and result.stdout, "store vault prompt is unavailable")
            return prompt_passphrase(result.stdout + " ")

        receive(load, *shared, read_passphrase=unlock)
        outcome = "received"
    elif action == "mode":
        outcome = shared_mode(load, screen, shared)
    elif action == "recover":
        from store_overview_transfer import recover

        recover(load, screen, {scope[0] for scope in (shared, private) if scope})
        return
    else:
        from store_mode import disconnect_keep_checkout

        if screen("_store_overview_disconnect").returncode != 0:
            return
        disconnect_keep_checkout(load, *shared)
        outcome = "disconnected"
    if outcome:
        screen("_store_overview_result", outcome)


def browse(reload_account, checkout, screen, *, connect=connect_route):
    account = reload_account()

    def load():
        require(reload_account() == account, "store approval changed while the overview was open")
        return account

    while True:
        rows, selections, shared, private = overview_rows(load, checkout)
        result = screen("_store_overview_screen", data=rows)
        command, _, selection = result.stdout.partition("\n")
        if result.returncode in (0, 1) and command == "ctrl-o":
            store_action(load, screen, shared, private)
            continue
        if result.returncode in (1, 130):
            return 0
        selection = selection.strip()
        require(result.returncode == 0 and not command and selection in selections, "invalid host selection")
        key, expected, host, stamp, kind = selections[selection]
        refreshed = refresh_routes(load, key, expected)
        require(any(row[1] == host and row[3] == stamp for row in
                    (line.split("\t") for line in refreshed.splitlines())),
                "store host changed; reopen the list to review it")
        route, _, _ = selected_route(load, key, expected, host, stamp)
        fields = (json.dumps(route[k], ensure_ascii=True)[1:-1]
                  for k in ("name", "hostname", "user", "port", "proxyjump"))
        destination = shared if kind == "private" else private if shared else None
        result = screen("_store_overview_host", kind, *fields, "transfer" if destination else "")
        if result.returncode in (1, 130):
            continue
        require(result.returncode == 0 and result.stdout.strip() in ("0", "1", "2"), "invalid host action")
        if result.stdout.strip() == "0":
            return connect(load, key, expected, host, stamp)
        if result.stdout.strip() == "2":
            from store_overview_transfer import move_host

            require(destination, "select a connected shared store and a private store")
            selected_route(load, key, expected, host, stamp)
            move_host(load, screen, (key, expected, host), destination)


def overview_command(args):
    from store_login import age_installations
    from store_transfer import interruptible_preview
    from store_transfer_command import compatible_picker, operator_environment
    from store_transfer_credentials import private_workspace

    require(len(args) in (1, 2), "expected one picker version and an optional registered checkout")
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    checkout = Path(args[1]).resolve(strict=True) if len(args) == 2 else None

    def load():
        return load_approved_account(home)

    locations = [(directory, cellar.with_name("fzf") if cellar else None)
                 for directory, cellar in age_installations()]
    with interruptible_preview(), private_workspace() as (scratch, _):
        binary = compatible_picker(load(), locations, args[0])
        tools, env = operator_environment(scratch, home)
        (tools / "fzf").symlink_to(binary[0] / "fzf")
        hostbook = Path(__file__).resolve().parents[1] / "hostbook"
        def screen(action, *fields, data=None):
            return subprocess.run(["/bin/bash", str(hostbook), action, *fields],
                                  input=data, text=True, stdout=subprocess.PIPE, env=env)

        return browse(load, checkout, screen)
