"""Compare one selected host across two registered stores before a transfer decision."""
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import pwd
from typing import Optional

from store_checkout import direct_ref, read_ref
from store_context import (Account, DirectoryBinding, Generations, INDEX_NAME, StoreContext, bound_directory,
                           index_rows, load_approved_account, path_parts, read_file, require, resolved_store,
                           valid_id, valid_ref)
from store_read import MAX_ROUTE_BYTES, effective, local_inputs, managed_credentials, ssh_view
from store_receive import plain_git, receive_binding


@dataclass(frozen=True, repr=False)
class ObservedStore:
    context: StoreContext
    ref: str
    base: str
    target: Optional[DirectoryBinding]
    remote_tip: Optional[str]
    identity: dict
    files: dict
    local: tuple
    hosts: dict


def observe_store(account, store_id, expected):
    with resolved_store(account, store_id, expected) as (ctx, binding, root, state):
        require(binding.git_dir.path == ctx.checkout/".git" and binding.git_common_dir == binding.git_dir,
                "transfer requires a regular Git checkout")
        with bound_directory(root, path_parts(account.root.path, binding.git_dir.path), binding.git_dir,
                             "transfer Git binding changed") as repo:
            plain_git(repo, bare=False)
            branch = read_file(repo, "HEAD", 4096).decode().strip()
            require(branch.startswith("ref: ") and valid_ref(branch[5:]), "transfer requires an attached branch")
            ref = branch[5:]
            require(ctx.ref is None or ctx.ref == ref, "registered transfer branch changed")
            base = read_ref(repo, ref)
        files = {}
        with bound_directory(root, path_parts(account.root.path, binding.root.path), binding.root,
                             "transfer checkout changed") as checkout:
            identity, _ = index_rows(checkout, store_id, files, MAX_ROUTE_BYTES)
        local = local_inputs(state, store_id)
    target, tip = None, None
    if ctx.kind == "shared":
        with receive_binding(account, store_id, expected) as (verified, target, remote, _, actual):
            require((ctx, binding) == (verified, actual), "registered transfer binding changed")
            plain_git(remote, bare=True)
            tip = direct_ref(remote, ref)
    inputs, credentials = managed_credentials(files, identity["hosts"])
    with ssh_view(inputs, local) as config:
        hosts = {row["host_id"]: dict(effective(config, row["name"]), name=row["name"], host_id=row["host_id"],
                                      credential=credentials.get(row["name"].lower())) for row in identity["hosts"]}
    return ObservedStore(ctx, ref, base, target, tip, identity, files, local, hosts)


def endpoint(host):
    address = host["hostname"]
    try:
        address = str(ipaddress.ip_address(address))
    except ValueError:
        address = address.casefold()
    return address, host["user"], host["port"]


@dataclass(frozen=True, repr=False)
class TransferPlan:
    account: Account
    source: ObservedStore
    destination: ObservedStore
    host_id: str
    alias: str

    def comparison(self):
        src, dst = self.source, self.destination
        host = src.hosts[self.host_id]
        matches = []
        for key, candidate in dst.hosts.items():
            reasons = []
            if key == self.host_id:
                reasons.append("stable-id")
            if candidate["name"].casefold() == self.alias.casefold():
                reasons.append("alias")
            if endpoint(candidate) == endpoint(host):
                reasons.append("endpoint")
            if reasons:
                matches.append(dict(candidate, reasons=reasons))
        shared, effect = (src, "source-removal") if src.context.kind == "shared" else (dst, "destination-save")
        users = [row["host_id"] for key, row in src.hosts.items() if key != self.host_id
                 and host["credential"] and row["credential"] == host["credential"]]
        return {
            "source":dict(host, store_id=src.context.store_id, kind=src.context.kind),
            "destination":{"store_id":dst.context.store_id, "kind":dst.context.kind,
                           "proposed_alias":self.alias, "proposed_host_id":self.host_id},
            "matches":matches, "retained_credential_users":users,
            "publications":[{"store_id":shared.context.store_id,"ref":shared.ref,"effect":effect}],
            "shared_mode":shared.context.effective_mode, "shared_checkout_at_remote_tip":shared.base == shared.remote_tip,
        }


def preview(load, source, source_generations, host_id, destination, destination_generations, alias=None):
    require(source != destination and valid_id(host_id), "invalid transfer selection")
    account = load()
    src = observe_store(account, source, source_generations)
    dst = observe_store(account, destination, destination_generations)
    require({src.context.kind, dst.context.kind} == {"private","shared"}, "select one private and one shared store")
    require(host_id in src.hosts, "selected transfer host is missing")
    if alias is None:
        alias = src.hosts[host_id]["name"]
    require(isinstance(alias,str) and 0 < len(alias) <= 255 and not alias.startswith("-")
            and INDEX_NAME.fullmatch(alias), "invalid proposed transfer alias")
    require(load() == account, "transfer account approval changed")
    return TransferPlan(account, src, dst, host_id, alias)


def preview_command(args):
    require(len(args) in (11,12), "source store and generations, host ID, destination store and generations are required")
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    plan = preview(lambda: load_approved_account(home), args[0], Generations(*(int(x) for x in args[1:5])),
                   args[5], args[6], Generations(*(int(x) for x in args[7:11])), args[11] if len(args)==12 else None)
    print(json.dumps(plan.comparison()))
    return 0
