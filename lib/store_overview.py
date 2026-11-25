"""Read and show the first-release host inventory from approved checkouts."""
from pathlib import Path
import pwd
import subprocess

from store_context import (bound_directory, entry_generations, index_rows, load_approved_account,
                           path_parts, registry_entries, require, resolved_store, verified_account)
from store_read import MAX_ROUTE_BYTES


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
    if chosen in shared:
        shared = [chosen]
    rows = []
    if not shared:
        rows.append("shared-none")
    elif len(shared) > 1:
        rows.append("shared-choose")
        shared = []
    for key in private + shared:
        entry = entries[key]
        with resolved_store(account, key, entry_generations(entry)) as (context, binding, root, _):
            with bound_directory(root, path_parts(account.root.path, binding.root.path), binding.root,
                                 "checkout binding mismatch") as checkout_fd:
                identity, _ = index_rows(checkout_fd, key, max_source_bytes=MAX_ROUTE_BYTES)
            hosts = identity["hosts"]
            if context.kind == "shared":
                mode = "changing" if context.transition_id else context.effective_mode
                rows.append(f"summary-shared\t{len(hosts)}\t{mode}\t{context.ref}")
            else:
                rows.append(f"summary-private\t{len(hosts)}")
            rows.extend(f"host\t{context.kind}\t{host['name']}" for host in hosts)
    require(load() == account, "store approval changed during listing")
    with verified_account(account) as (_, state):
        require(registry_entries(state, account) == (entries, bindings), "store registrations changed during listing")
    return "\n".join(rows) + "\n"


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
        data = overview_rows(load, checkout)
        binary = compatible_picker(load(), locations, args[0])
        tools, env = operator_environment(scratch, home)
        (tools / "fzf").symlink_to(binary[0] / "fzf")
        hostbook = Path(__file__).resolve().parents[1] / "hostbook"
        result = subprocess.run(["/bin/bash", str(hostbook), "_store_overview_screen"],
                                input=data, text=True, env=env)
    return 0 if result.returncode == 130 else result.returncode
