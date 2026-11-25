"""Search hosts across approved store checkouts."""
from collections import Counter
from dataclasses import dataclass
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess

from store_context import (Generations, JSON_LIMIT, StoreContextError, entry_generations, load_approved_account,
                           path_parts, registry_entries, require, resolved_store, verified_account)
from store_read import connect_route, refresh_routes, route_inputs, selected_route
from store_receive import connection_target_record


@dataclass(frozen=True)
class OverviewStore:
    generations: Generations
    origin: str
    source: str
    ref: str
    mode: str


def source_label(account, context, state):
    generation = context.generations.connection
    if context.effective_mode is None:
        generation -= 1  # Disconnect advances the connection and keeps the previous target record.
    try:
        record, target = connection_target_record(state, context.store_id, generation)
        if context.effective_mode is not None:
            require((record["target_id"], record["ref"]) == (context.target_id, context.ref),
                    "registered transport binding mismatch")
        source = "/".join(path_parts(account.root.path, target.path))
    except (OSError, StoreContextError):
        return ""
    return json.dumps(source, ensure_ascii=True)[1:-1]


def overview_rows(load, checkout=None, selected=None):
    account = load()
    with verified_account(account) as (_, state):
        entries, bindings = registry_entries(state, account)
    require(checkout is None or any(b.root.path == checkout for b in bindings.values()),
            "selected checkout is not registered")
    inventory, stores = {}, {}
    for key in sorted(bindings, key=lambda key: entries[key]["kind"] != "private"):
        expected = entry_generations(entries[key])
        with resolved_store(account, key, expected) as (context, binding, root, state):
            origin, source = "local", ""
            if context.kind == "shared":
                origin = "connected" if context.effective_mode else "offline"
                source = source_label(account, context, state)
            mode = "changing" if context.transition_id else context.effective_mode
            stores[key] = OverviewStore(expected, origin, source, context.ref or "", mode or "")
            if selected is None or key == selected:
                stamp, hosts, _, _ = route_inputs(account, context, binding, root, state)
                inventory[key] = stamp, hosts
    require(load() == account, "store approval changed during listing")
    with verified_account(account) as (_, state):
        require(registry_entries(state, account) == (entries, bindings), "store registrations changed during listing")

    labels = Counter(store.source for store in stores.values() if store.origin != "local")
    rows, selections = [], {}
    for key, store in stores.items():
        if store.source and labels[store.source] > 1:
            location = "/".join(path_parts(account.root.path, bindings[key].root.path))
            location = json.dumps(location, ensure_ascii=True)[1:-1]
            source = f"{store.source} ({location})"
            store = stores[key] = OverviewStore(store.generations, store.origin, source, store.ref, store.mode)
        if key not in inventory:
            continue
        stamp, hosts = inventory[key]
        if selected == key:
            rows.append(f"scope\t{store.origin}\t{len(hosts)}\t{store.mode or '-'}\t{store.ref or '-'}\t{store.source}")
        for host in hosts:
            token = str(len(selections) + 1)
            selections[token] = (key, host["host_id"], stamp)
            rows.append(f"host\t{host['name']}\t{token}\t{store.origin}\t{store.source}")
    return "".join(row + "\n" for row in rows), selections, stores


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


def pick_store(screen, purpose, stores, keys):
    options = []
    for key in keys:
        if key is None:
            options.append("all")
        else:
            store = stores[key]
            options.append(f"{store.origin}\t{store.source}")
    choice = choose(screen, "_store_overview_pick", {str(i) for i in range(len(keys))}, purpose, *options)
    return (False, None) if choice is None else (True, keys[int(choice)])


def store_action(load, screen, stores, connected, private):
    from store_private import add_private
    from store_receive import receive

    choices = (["add", "import"] if private else []) + (["receive", "mode", "disconnect"] if connected else [])
    if stores:
        choices.append("recover")
    if not choices:
        return
    choice = choose(screen, "_store_overview_tools", {str(i) for i in range(len(choices))}, *choices)
    if choice is None:
        return
    action = choices[int(choice)]
    if action in ("receive", "mode", "disconnect"):
        key = connected[0]
        if len(connected) > 1:
            confirmed, key = pick_store(screen, action, stores, connected)
            if not confirmed:
                return
        shared = key, stores[key].generations
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

        recover(load, screen, set(stores))
        return
    else:
        from store_mode import disconnect_keep_checkout

        if screen("_store_overview_disconnect", stores[key].source).returncode != 0:
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

    selected, query = None, ""
    while True:
        rows, selections, stores = overview_rows(load, checkout, selected)
        if selected is not None and selected not in stores:
            selected = None
            continue
        private = next(((key, store.generations) for key, store in stores.items()
                        if store.origin == "local"), None)
        connected = [key for key, store in stores.items() if store.origin == "connected"]
        result = screen("_store_overview_screen", query, data=rows)
        query, _, output = result.stdout.partition("\n")
        command, _, selection = output.partition("\n")
        if result.returncode in (0, 1) and command == "ctrl-f":
            confirmed, key = pick_store(screen, "filter", stores, [None, *stores])
            if confirmed:
                selected = key
            continue
        if result.returncode in (0, 1) and command == "esc":
            if query:
                query = ""
            elif selected is not None:
                selected = None
            else:
                return 0
            continue
        if result.returncode in (0, 1) and command == "ctrl-o":
            scoped = connected if selected is None else [key for key in connected if key == selected]
            store_action(load, screen, stores, scoped, private)
            continue
        if result.returncode in (1, 130):
            return 0
        selection = selection.strip()
        require(result.returncode == 0 and not command and selection in selections, "invalid host selection")
        key, host, stamp = selections[selection]
        store = stores[key]
        expected = store.generations
        refreshed = refresh_routes(load, key, expected)
        require(any(row[1] == host and row[3] == stamp for row in
                    (line.split("\t") for line in refreshed.splitlines())),
                "store host changed; reopen the list to review it")
        route, _, _ = selected_route(load, key, expected, host, stamp)
        fields = (json.dumps(route[k], ensure_ascii=True)[1:-1]
                  for k in ("name", "hostname", "user", "port", "proxyjump"))
        can_transfer = (store.origin == "local" and connected) or (store.origin == "connected" and private)
        result = screen("_store_overview_host", store.origin, *fields, "transfer" if can_transfer else "", store.source)
        if result.returncode in (1, 130):
            continue
        require(result.returncode == 0 and result.stdout.strip() in ("0", "1", "2"), "invalid host action")
        if result.stdout.strip() == "0":
            return connect(load, key, expected, host, stamp)
        if result.stdout.strip() == "2":
            from store_overview_transfer import move_host

            require(can_transfer, "select a connected shared store and a private store")
            destination = private
            if store.origin == "local":
                target = connected[0]
                if len(connected) > 1:
                    confirmed, target = pick_store(screen, "transfer", stores, connected)
                    if not confirmed:
                        continue
                destination = target, stores[target].generations
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
