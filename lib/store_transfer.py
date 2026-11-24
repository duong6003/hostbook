"""Compare one selected host across two registered stores before a transfer decision."""
from contextlib import contextmanager
import copy
from dataclasses import dataclass, replace
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import signal
from typing import Optional

from store_checkout import direct_ref, read_ref, tree
from store_context import (Account, DirectoryBinding, Generations, INDEX_NAME, StoreContext, StoreContextError, bound_directory,
                           index_rows, load_approved_account, parse_json, path_parts, read_file, require, resolved_store,
                           valid_id, valid_ref)
from store_publish import require_plain_receiver
from store_read import (DIRECTIVE, MAX_ROUTE_BYTES, SECRET_COMMENT, effective, jump_fields, local_inputs,
                        managed_credentials, render_jumps, ssh_view)
from store_receive import IDENTITY, fetch_direct, fetch_from_fd, git, plain_git, receive_binding
from store_transfer_credentials import CredentialRef, private_workspace, read_mapping_end


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

    def routes(self):
        rows = self.identity["hosts"]
        inputs, credentials = managed_credentials(self.files, rows)
        with ssh_view(inputs, self.local) as config:
            routes = {}
            for row in rows:
                route = effective(config,row["name"],complete=True)
                route["settings"] = dict(route["settings"])
                routes[row["host_id"]] = dict(route,name=row["name"],host_id=row["host_id"],
                                               credential=credentials.get(row["name"].lower()))
            return routes


def observe_store(account, store_id, expected, *, transfer=None):
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
        with receive_binding(account, store_id, expected, transfer=transfer) as (verified, target, remote, _, actual):
            require((ctx, binding) == (verified, actual), "registered transfer binding changed")
            plain_git(remote, bare=True)
            tip = direct_ref(remote, ref)
    return ObservedStore(ctx, ref, base, target, tip, identity, files, local)


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
    source_hosts: dict
    destination_hosts: dict
    decision: str = "new"
    chosen_destination_id: Optional[str] = None

    @property
    def shared(self):
        return self.source if self.source.context.kind == "shared" else self.destination

    @property
    def destination_key(self):
        return self.chosen_destination_id if self.decision == "update" else self.host_id

    def host_keys(self):
        return [{"store_id":self.source.context.store_id,"host_id":self.host_id},
                {"store_id":self.destination.context.store_id,"host_id":self.destination_key}]

    def revalidate(self, load, *, transfer=None):
        account = load()
        require(account == self.account, "transfer account approval changed")
        for prior in (self.source, self.destination):
            require(observe_store(account, prior.context.store_id, prior.context.generations,transfer=transfer) == prior,
                    "transfer inputs changed since comparison")

    def comparison(self):
        src, dst = self.source, self.destination
        host = self.source_hosts[self.host_id]
        matches = []
        for key, candidate in self.destination_hosts.items():
            reasons = []
            if key == self.host_id:
                reasons.append("stable-id")
            if candidate["name"].casefold() == self.alias.casefold():
                reasons.append("alias")
            if endpoint(candidate) == endpoint(host):
                reasons.append("endpoint")
            if reasons:
                matches.append(dict(candidate, reasons=reasons,revision=dst.base))
        shared, effect = (src, "source-removal") if src.context.kind == "shared" else (dst, "destination-save")
        users = [row["host_id"] for key, row in self.source_hosts.items() if key != self.host_id
                 and host["credential"] and row["credential"] == host["credential"]]
        reasons = {reason for match in matches for reason in match["reasons"]}
        choices = ["cancel"]
        if not matches:
            choices.insert(0,"new")
        elif "stable-id" in reasons:
            choices.insert(0,"reconcile")
        else:
            if "endpoint" in reasons:
                choices.insert(0,"update")
            if "alias" in reasons:
                choices.insert(0,"choose-free-alias")
            elif "endpoint" in reasons:
                choices.insert(0,"keep-both")
        changes = {}
        if self.decision == "update" and self.chosen_destination_id in self.destination_hosts:
            previous = self.destination_hosts[self.chosen_destination_id]
            def settings(row):
                return dict(row["settings"],**{k:row[k] for k in ("hostname","user","port","proxyjump")})
            old,new = settings(previous),settings(host)
            changes = {key:{"existing":old.get(key),"proposed":new.get(key)} for key in sorted(old.keys()|new.keys())
                       if old.get(key) != new.get(key)}
        return {
            "source":dict(host, store_id=src.context.store_id, kind=src.context.kind,ref=src.ref,revision=src.base),
            "destination":{"store_id":dst.context.store_id, "kind":dst.context.kind,
                           "proposed_alias":self.alias, "proposed_host_id":self.destination_key,"ref":dst.ref,"revision":dst.base},
            "matches":matches, "retained_credential_users":users,"choices":choices,
            "decision":self.decision,"chosen_destination_id":self.chosen_destination_id,"connection_changes":changes,
            "publications":[{"store_id":shared.context.store_id,"ref":shared.ref,"effect":effect}],
            "shared_mode":shared.context.effective_mode, "shared_checkout_at_remote_tip":shared.base == shared.remote_tip,
        }

    def verify_decision(self):
        comparison = self.comparison()
        if self.decision == "new":
            require(not comparison["matches"] and self.chosen_destination_id is None,
                    "destination match requires an explicit transfer decision")
            return
        require(self.decision in ("keep-both","update") and self.decision in comparison["choices"] and
                any(row["host_id"] == self.chosen_destination_id and "endpoint" in row["reasons"]
                    and (self.decision != "update" or row["name"] == self.alias) for row in comparison["matches"]), "duplicate decision does not match the reviewed destination")

    def choose(self, decision, host, source_revision, destination_revision):
        require((source_revision,destination_revision) == (self.source.base,self.destination.base),
                "transfer needs current reviewed revisions for both stores")
        updated = replace(self,decision=decision,chosen_destination_id=host)
        updated.verify_decision()
        return updated


def preview(load, source, source_generations, host_id, destination, destination_generations, alias=None):
    require(source != destination and valid_id(host_id), "invalid transfer selection")
    account = load()
    src = observe_store(account, source, source_generations)
    dst = observe_store(account, destination, destination_generations)
    require({src.context.kind, dst.context.kind} == {"private","shared"}, "select one private and one shared store")
    source_hosts, destination_hosts = src.routes(), dst.routes()
    require(host_id in source_hosts, "selected transfer host is missing")
    if alias is None:
        alias = source_hosts[host_id]["name"]
    require(isinstance(alias,str) and 0 < len(alias) <= 255 and not alias.startswith("-")
            and INDEX_NAME.fullmatch(alias), "invalid proposed transfer alias")
    plan = TransferPlan(account, src, dst, host_id, alias, source_hosts, destination_hosts)
    plan.revalidate(load)
    return plan


@contextmanager
def interruptible_preview():
    def stop(signum, _frame):
        raise SystemExit(128 + signum)
    saved = {}
    try:
        for sig in (signal.SIGTERM, signal.SIGHUP):
            saved[sig] = signal.signal(sig, stop)
        yield
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)


def preview_command(args):
    require(len(args) in (11,12), "source store and generations, host ID, destination store and generations are required")
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    with interruptible_preview():
        plan = preview(lambda: load_approved_account(home), args[0], Generations(*(int(x) for x in args[1:5])),
                       args[5], args[6], Generations(*(int(x) for x in args[7:11])), args[11] if len(args)==12 else None)
        print(json.dumps(plan.comparison()))
    return 0


@dataclass(frozen=True, repr=False)
class DestinationCandidate:
    stage: Path
    proposed: str
    changes: dict
    source_path: str
    plan: TransferPlan
    mapping: object
    source_stage: Path
    source_proposed: str
    source_identity: bytes
    replaced_project: Optional[str]
    previous_credential: Optional[tuple]


def standalone_document(store, host_id, alias, old_name, new_name, allow_key):
    selected = next(row for row in store.identity["hosts"] if row["host_id"] == host_id)
    require(len(selected["bindings"]) == 1, "transfer requires one independent host stanza")
    binding = selected["bindings"][0]
    path = binding["path"]
    require(binding["host_ordinal"] == 1 and binding["token_ordinal"] == 1
            and sum(other["path"] == path for row in store.identity["hosts"] for other in row["bindings"]) == 1,
            "transfer requires a project containing only the selected host")
    raw = store.files[path]
    document = []
    found = False
    for line in raw.split(b"\n"):
        text = line.rstrip(b"\r").strip(b" \t")
        marker = SECRET_COMMENT.fullmatch(text.decode())
        if marker:
            require(not found and old_name and marker[1] == old_name, "unexpected transfer credential annotation")
            line = b"# secret: " + new_name.encode()
        elif text and not text.startswith(b"#"):
            directive = DIRECTIVE.fullmatch(text.decode())
            require(directive, "unsupported transfer SSH source")
            key = directive[1].lower()
            if key == "host":
                name = re.fullmatch(rb"([ \t]*Host(?:[ \t]+=?[ \t]*|=[ \t]*))([A-Za-z0-9._-]+)([ \t]*(?:#[^\r\n]*)?\r?)",
                                    line, re.I)
                require(not found and name and name[2].decode().lower() == selected["name"].lower(),
                        "transfer requires a single literal Host")
                line = name[1] + alias.encode() + name[3]
                found = True
            else:
                require(found and key not in ("include","match"), "transfer requires independent host settings")
                if key == "identityfile":
                    require(allow_key, "unexpected transfer IdentityFile")
                    line = b"  IdentityFile secrets/" + new_name.encode() + b".pub"
        document.append(line)
    require(found, "transfer source host is missing")
    return path, b"\n".join(document)


def route_chains(store_id, identity, files, local):
    rows = identity["hosts"]
    inputs, credentials = managed_credentials(files, rows)
    keys = {r["name"].lower(): r["host_id"] for r in rows}
    chains = {}
    with ssh_view(inputs, local) as config:
        for row in rows:
            chain, visited = [], set()
            alias, options = row["name"], []
            for _ in range(32):
                route = effective(config,alias,options,complete=True)
                key = keys.get(alias.lower())
                binding = (store_id,key) if key else None
                step = (binding, alias.lower(), endpoint(route), route["proxyjump"], credentials.get(alias.lower()),
                        route["settings"])
                require(step not in visited, "transfer SSH jump route contains a loop")
                visited.add(step)
                chain.append(step)
                jump = route["proxyjump"]
                if not jump or jump.lower() == "none":
                    break
                remaining, _, hop = jump.rpartition(",")
                user, alias, port = jump_fields(hop)
                alias, options = alias.strip("[]"), []
                if user:
                    options += ["-l",user]
                if port:
                    options += ["-p",port]
                if remaining:
                    options += ["-J",render_jumps(remaining)]
            else:
                raise StoreContextError("transfer SSH jump route is too long")
            chains[row["host_id"]] = chain
    return chains


def verify_accepted_mapping(stage, existing, ref, observed):
    vault, record = observed.vault, observed.record
    expected = {"vault.pub":vault.recipient, "vault.key.age":vault.password_envelope,
                "vault.recovery.age":vault.recovery_envelope}
    name = ref.name
    for suffix, data in (("meta",record.metadata if record else None), ("age",record.ciphertext if record else None),
                         ("pub",record.public_key if record else None)):
        expected[name+"."+suffix] = data
    for name, data in expected.items():
        oid = existing.get("secrets/"+name)
        require((oid is None and data is None) or
                (oid is not None and data is not None and git(stage,"cat-file","blob",oid.decode()) == data),
                "accepted destination vault or credential differs from the reviewed mapping")


@contextmanager
def writable_shared(plan, *, transfer=None):
    dst = plan.shared
    with receive_binding(plan.account,dst.context.store_id,dst.context.generations,transfer=transfer) as (ctx,target,remote,_,_):
        require((ctx,target) == (dst.context,dst.target) and ctx.effective_mode == "two-way",
                "shared binding or mode changed")
        require_plain_receiver(remote)
        require(direct_ref(remote,dst.ref) == dst.base, "shared branch changed")
        yield remote


def fetch_reviewed_base(plan, store, stage):
    if store.context.kind == "shared":
        with writable_shared(plan) as remote:
            tip = fetch_direct(stage,remote,store.ref,"refs/hostbook/base")
    else:
        with resolved_store(plan.account,store.context.store_id,store.context.generations) as (ctx,binding,root,_):
            with bound_directory(root,path_parts(plan.account.root.path,binding.git_dir.path),binding.git_dir,
                                 "private Git binding changed") as repo:
                require(ctx == store.context and read_ref(repo,store.ref) == store.base, "private Git branch changed")
                tip = fetch_from_fd(stage,repo,store.ref,"refs/hostbook/base")
    require(tip == store.base, "reviewed transfer base changed")


def source_proposal(plan, folder, source_path, after, mapping):
    src = plan.source
    stage = folder/"source.git"
    stage.mkdir(mode=0o700)
    git(stage,"init","-q","--bare","--template=")
    fetch_reviewed_base(plan,src,stage)
    before = tree(stage,src.base,git)
    if mapping:
        verify_accepted_mapping(stage,before,mapping.source,mapping.source_before)
    require(parse_json(git(stage,"cat-file","blob",before[IDENTITY].decode())) == src.identity
            and all(path in before and git(stage,"cat-file","blob",before[path].decode()) == data
                    for path,data in src.files.items()), "source differs from its accepted revision")
    identity = json.dumps(after,separators=(",",":")).encode()+b"\n"
    git(stage,"read-tree",src.base)
    blob = git(stage,"hash-object","-w","--stdin",data=identity).strip()
    git(stage,"update-index","-z","--index-info",data=b"0 " + b"0"*len(src.base) + b"\t"+source_path.encode()+b"\0"
        + b"100644 "+blob+b"\t"+IDENTITY.encode()+b"\0")
    root = git(stage,"write-tree").strip().decode()
    commit = git(stage,"-c","user.name=Hostbook","-c","user.email=hostbook@localhost",
                 "commit-tree",root,"-p",src.base,"-m","Remove transferred host").strip().decode()
    return stage,commit,identity


@contextmanager
def prepare_candidate(load, plan, mapping):
    """Build a destination-only regular Git commit in private staging and verify its SSH effects."""
    src, dst = plan.source, plan.destination
    require({src.context.kind,dst.context.kind} == {"private","shared"} and plan.shared.context.effective_mode == "two-way"
            and plan.shared.base == plan.shared.remote_tip,"transfer requires a current two-way shared checkout")
    plan.verify_decision()
    source_name = plan.source_hosts[plan.host_id]["credential"]
    if source_name is None:
        require(mapping is None, "unexpected transfer credential mapping")
    else:
        require(mapping and mapping.source == CredentialRef(src.context.store_id,src.context.generations,source_name)
                and mapping.destination.store_id == dst.context.store_id
                and mapping.destination.generations == dst.context.generations, "transfer credential mapping changed")
        mapping.check_current(load)
    plan.revalidate(load)
    source_path, document = standalone_document(src,plan.host_id,plan.alias,source_name,
                                                mapping.destination.name if mapping else None,mapping and mapping.public_key)
    source_after = copy.deepcopy(src.identity)
    source_after["generation"] += 1
    source_after["hosts"] = [r for r in source_after["hosts"] if r["host_id"] != plan.host_id]
    source_after["sources"] = [r for r in source_after["sources"] if r["path"] != source_path]
    source_files = dict(src.files)
    del source_files[source_path]
    source_routes = route_chains(src.context.store_id,src.identity,src.files,src.local)
    remaining = route_chains(src.context.store_id,source_after,source_files,src.local)
    require(all(route == source_routes[key] for key,route in remaining.items()), "source removal changes a dependent route")

    identity = copy.deepcopy(dst.identity)
    identity["generation"] += 1
    digest = hashlib.sha256(document).hexdigest()
    replaced_project,previous_credential = None,None
    if plan.decision == "update":
        old_name = plan.destination_hosts[plan.destination_key]["credential"]
        if old_name:
            old_ref = CredentialRef(dst.context.store_id,dst.context.generations,old_name)
            old_end = read_mapping_end(plan.account,old_ref)
            require(old_end.record is not None,"prior destination credential is unavailable")
            previous_credential = old_ref,old_end
        project,_ = standalone_document(dst,plan.destination_key,plan.alias,old_name,old_name,
                                        previous_credential and old_end.record.public_key)
        replaced_project = project if dst.files[project] != document else None
        row = next(source for source in identity["sources"] if source["path"] == project)
        row["sha256"] = digest
    else:
        project = "host-" + plan.host_id + ".conf"
        require(project not in dst.files, "destination project already exists")
        identity["sources"].append({"path":project,"sha256":digest})
        identity["hosts"].append({"host_id":plan.host_id,"name":plan.alias,
                                  "bindings":[{"path":project,"host_ordinal":1,"token_ordinal":1}]})
    files = dict(dst.files)
    files[project] = document
    prior = route_chains(dst.context.store_id,dst.identity,dst.files,dst.local)
    after = route_chains(dst.context.store_id,identity,files,dst.local)
    require(all(after[key] == route for key,route in prior.items() if key != plan.destination_key), "destination changes a dependent route")
    old, new = source_routes[plan.host_id], after[plan.destination_key]
    require(old[0][2:4] == new[0][2:4] and old[0][5] == new[0][5] and old[1:] == new[1:]
            and new[0][4] == (mapping.destination.name if mapping else None), "destination changes the selected route")
    if mapping:
        settings = dict(new[0][5])
        methods = settings.get("preferredauthentications")
        def offered(method):
            return methods is None or method in methods.split(",")
        enabled = ("yes","true")
        if mapping.public_key:
            require(settings["pubkeyauthentication"] not in ("no","false") and offered("publickey"),
                    "selected route disables public key authentication")
        else:
            require(settings["batchmode"] not in enabled and int(settings["numberofpasswordprompts"]) > 0
                    and ((settings["passwordauthentication"] in enabled and offered("password"))
                         or (settings["kbdinteractiveauthentication"] in enabled and offered("keyboard-interactive"))),
                    "selected route disables interactive authentication")
    changes = {IDENTITY:json.dumps(identity,separators=(",",":")).encode()+b"\n"}
    if document != dst.files.get(project):
        changes[project] = document
    if mapping and not mapping.reused:
        for suffix, contents in (("meta",mapping.metadata),("age",mapping.ciphertext),("pub",mapping.public_key)):
            if contents is not None:
                changes["secrets/"+mapping.destination.name+"."+suffix] = contents

    with private_workspace() as (folder,_):
        stage = folder/"destination.git"
        stage.mkdir(mode=0o700)
        git(stage,"init","-q","--bare","--template=")
        fetch_reviewed_base(plan,dst,stage)
        existing = tree(stage,dst.base,git)
        if mapping:
            verify_accepted_mapping(stage,existing,mapping.destination,mapping.destination_before)
        if previous_credential:
            verify_accepted_mapping(stage,existing,*previous_credential)
        require(all(path in (IDENTITY,replaced_project) or path not in existing for path in changes), "destination file is occupied")
        for path, data in dst.files.items():
            require(path in existing and git(stage,"cat-file","blob",existing[path].decode()) == data,
                    "destination source differs from its accepted revision")
        require(parse_json(git(stage,"cat-file","blob",existing[IDENTITY].decode())) == dst.identity,
                "destination identity differs from its accepted revision")
        git(stage,"read-tree",dst.base)
        for path, data in changes.items():
            blob = git(stage,"hash-object","-w","--stdin",data=data).strip()
            git(stage,"update-index","-z","--index-info",data=b"100644 "+blob+b"\t"+path.encode()+b"\0")
        root = git(stage,"write-tree").strip().decode()
        proposed = git(stage,"-c","user.name=Hostbook","-c","user.email=hostbook@localhost",
                       "commit-tree",root,"-p",dst.base,"-m","Transfer registered host").strip().decode()
        tree(stage,proposed,git)
        source_stage,removal,source_identity = source_proposal(plan,folder,source_path,source_after,mapping)
        plan.revalidate(load)
        if mapping:
            mapping.check_current(load)
        with writable_shared(plan):
            yield DestinationCandidate(stage,proposed,changes,source_path,plan,mapping,source_stage,removal,source_identity,
                                       replaced_project,previous_credential)
