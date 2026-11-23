"""Read registered host routes using a private, store-scoped OpenSSH view."""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import sys

from store_context import (
    INDEX_NAME, JSON_LIMIT, Generations, StoreContextError, acquire_local_lock, bound_directory, fixture_request,
    index_rows, load_approved_account, open_directory, parse_json, path_parts, read_file, require,
    require_approval_file, require_private_directory, resolved_store, staged_local_file,
    store_state_directory, valid_id, verify_state_directory,
)


SSH = "/usr/bin/ssh"
ROUTES = "routes.json"
ROUTE_VERSION = 5
MAX_ROUTE_BYTES = 16 * 1024 * 1024
ROUTE_FIELDS = {"hostname", "user", "port", "proxyjump", "name"}
STAMP = re.compile(r"[0-9a-f]{64}")
SSH_OPTIONS = frozenset("""
    addressfamily batchmode checkhostip compression connectionattempts connecttimeout
    host hostname identitiesonly ipqos kbdinteractiveauthentication loglevel match numberofpasswordprompts
    passwordauthentication port preferredauthentications proxyjump pubkeyauthentication
    requesttty serveralivecountmax serveraliveinterval tcpkeepalive user
""".split())
LOCAL_OPTIONS = SSH_OPTIONS | {"stricthostkeychecking"}
INLINE_COMMENT = re.compile(r"(?:^|[ 	])#")
DIRECTIVE = re.compile(r"([A-Za-z][A-Za-z0-9]*)(?:[ \t]+=?[ \t]*|=[ \t]*)(.*)")
SSH_USER = re.compile(r"[A-Za-z0-9._@+-]+")
SSH_HOST = re.compile(r"(?:[A-Za-z0-9_.:\[\]-]|%h)+")
SECRET_COMMENT = re.compile(r"#[ 	]*secret[ 	]*:[ 	]*(.*)")
SECRET_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,39}")
JUMP = re.compile(r"(?:([A-Za-z0-9._@+-]+)@)?"
                  r"([A-Za-z0-9_][A-Za-z0-9_.-]*|\[[A-Fa-f0-9:]+(?:%+[A-Za-z0-9][A-Za-z0-9_.-]*)?\])"
                  r"(?::([0-9]+))?")


def jump_fields(hop, *, generated=False):
    uri = hop.startswith("ssh://")
    if uri:
        user, at, destination = hop[6:].rpartition("@")
        hop = user.replace("%40", "@") + at + destination
    match = JUMP.fullmatch(hop)
    require(match, "invalid SSH directive: ProxyJump")
    user, host, port = match.groups()
    address, zone_mark, zone = host.partition("%")
    if zone_mark:
        require(not uri and ":" in address and (generated or not zone.startswith("%")),
                "invalid SSH directive: ProxyJump")
        host = address + "%" + zone.lstrip("%")
    return user, host, port


def render_jumps(value, *, logical=False, expansion_budget=None):
    if expansion_budget is None:
        expansion_budget = JSON_LIMIT
    hops = value.split(",")
    rendered = []
    for index, part in enumerate(hops):
        user, host, port = jump_fields(part, generated=logical)
        if part.startswith("ssh://") and "%" not in part:
            rendered.append(part)
            continue
        if not logical and "%" in host:
            # Each enclosing native ProxyJump expands the percent escapes once.
            depth = len(hops) - index
            require(depth < (expansion_budget + 1).bit_length(), "SSH jump configuration exceeds size limit")
            copies = 1 << depth
            expansion_budget -= copies - 1
            host = host.replace("%", "%" * copies)
        rendered.append(f"{user + '@' if user else ''}{host}{':' + port if port else ''}")
    return ",".join(rendered)


def snapshot_config(contents, local=False):
    import shlex

    # Admit data-only directives before OpenSSH sees the view, even in inactive sections.
    output = []
    remaining = JSON_LIMIT - len(contents)
    for line in contents.decode("utf-8").split("\n"):
        output.append(line)
        line = line.rstrip("\r").strip(" \t")
        if not line or line.startswith("#"):
            continue
        match = DIRECTIVE.fullmatch(line)
        require(not re.search(r"[\x00-\x08\x0a-\x1f\x7f]", line) and match, "invalid SSH directive")
        option, value = match[1].lower(), match[2]
        require(option in (LOCAL_OPTIONS if local else SSH_OPTIONS), f"unapproved SSH directive: {option}")
        quoted = False
        if option in ("match", "proxyjump", "hostname", "user"):
            # OpenSSH considers '#' a comment only at the beginning of an argument.
            value = INLINE_COMMENT.split(value, maxsplit=1)[0].rstrip(" 	")
            quoted = option != "match" and value.startswith('"') and value.endswith('"')
            if quoted:
                value = value[1:-1]
            require(not any(mark in value for mark in ("\\", "'", '"')), f"unsupported SSH directive: {option}")
            args = re.split(r"[ 	]+", value) if option == "match" else [value]
        else:
            args = shlex.split(value, comments=True)
        require(args, "empty SSH directive")
        if option == "match":
            terms = iter(args)
            for term in terms:
                criterion = term.removeprefix("!").lower()
                if criterion not in ("all", "canonical", "final"):
                    require(criterion in ("host", "originalhost", "user", "localuser") and next(terms, None),
                            "unapproved SSH directive: Match condition")
        elif option == "proxyjump":
            jump = render_jumps(value, expansion_budget=remaining)
            replacement = f"ProxyJump {jump}"
            remaining += len(output[-1].encode("utf-8")) - len(replacement)
            output[-1] = replacement
        elif option in ("hostname", "user"):
            pattern = SSH_HOST if option == "hostname" else SSH_USER
            require(len(args) == 1 and pattern.fullmatch(args[0]) and not args[0].startswith("-"),
                    f"invalid SSH directive: {option}")
        elif option == "stricthostkeychecking":
            require(args == ["yes"], "invalid SSH directive: StrictHostKeyChecking")
    return "\n".join(output).encode("utf-8")


def managed_credentials(files, hosts):
    import shlex

    occurrences = {}
    for host in hosts:
        for binding in host["bindings"]:
            aliases = occurrences.setdefault((binding["path"], binding["host_ordinal"]), set())
            aliases.add(host["name"].lower())
    credentials, snapshots = {}, []
    for path, contents in files.items():
        ordinal, pending, secret = 0, "", ""
        output = []
        for line in contents.decode("utf-8").split("\n"):
            text = line.rstrip("\r").strip(" 	")
            annotation = SECRET_COMMENT.fullmatch(text)
            if annotation:
                name = annotation[1]
                require(SECRET_NAME.fullmatch(name) and name != "vault" and not name.startswith("vault.")
                        and (not pending or pending == name), "invalid store credential binding")
                pending = name
            elif directive := DIRECTIVE.fullmatch(text):
                option, value = directive[1].lower(), directive[2]
                if option == "host":
                    ordinal += 1
                    secret = pending
                    if secret:
                        aliases = occurrences.get((path, ordinal))
                        pattern_list = INLINE_COMMENT.split(value, maxsplit=1)[0].strip(" 	")
                        patterns = re.split(r"[ 	]+", pattern_list)
                        require(aliases and all(INDEX_NAME.fullmatch(pattern) for pattern in patterns),
                                "credential binding requires an unambiguous managed Host")
                        for alias in aliases:
                            require(credentials.setdefault(alias, secret) == secret, "ambiguous store credential binding")
                elif option == "match":
                    secret = ""
                elif option == "identityfile":
                    args = shlex.split(value, comments=True)
                    require(secret and len(args) == 1 and re.search(r"(?:^|/)secrets/"
                            + re.escape(secret) + r"\.pub$", args[0]),
                            "unapproved SSH directive: IdentityFile")
                    # The authorizing binding is the annotation. Never read this path from the SSH file.
                    continue
                pending = ""
            output.append(line)
        snapshots.append("\n".join(output).encode("utf-8"))
    return snapshots, credentials


def local_inputs(state_fd, store_id):
    try:
        with open_directory(state_fd, ("stores", "v1", store_id, "ssh")) as fd:
            require_private_directory(fd, "approved SSH inputs")
            selected = []
            for name in ("config", "known_hosts", "global_known_hosts"):
                try:
                    selected.append(read_file(fd, name, JSON_LIMIT, require_approval_file))
                except FileNotFoundError:
                    selected.append(None)
            return tuple(selected)
    except FileNotFoundError:
        return None, None, None


def route_inputs(account, context, binding, root_fd, state_fd, *, collect=False):
    files = {} if collect else None
    with bound_directory(root_fd, path_parts(account.root.path, binding.root.path),
                         binding.root, "checkout binding mismatch") as checkout_fd:
        identity, _ = index_rows(checkout_fd, context.store_id, files, MAX_ROUTE_BYTES)
    local = local_inputs(state_fd, context.store_id)
    fingerprint = hashlib.sha256(json.dumps(
        [ROUTE_VERSION, asdict(context), identity], default=os.fspath, sort_keys=True
    ).encode())
    for contents in local:
        fingerprint.update(b"-" if contents is None else hashlib.sha256(contents).digest())
    hosts = identity["hosts"]
    return fingerprint.hexdigest(), hosts, managed_credentials(files, hosts) if collect else None, local


@contextmanager
def ssh_view(files, local):
    import tempfile

    require(os.access(SSH, os.X_OK), f"registered SSH routing requires system OpenSSH at {SSH}")
    # OpenSSH passes -F to ProxyJump via the shell, without quoting the path.
    with tempfile.TemporaryDirectory(prefix="hb-route-", dir="/tmp") as folder:
        root = Path(folder)
        config = root / "config"
        defaults, known_hosts, global_known_hosts = local
        known_hosts_copy = root / "known_hosts"
        known_hosts_copy.write_bytes(known_hosts or b"")
        global_copy = "none"
        if global_known_hosts is not None:
            global_copy = root / "global_known_hosts"
            global_copy.write_bytes(global_known_hosts)
        lines = [
            f"UserKnownHostsFile {known_hosts_copy}", f"GlobalKnownHostsFile {global_copy}",
            "StrictHostKeyChecking yes", "UpdateHostKeys no",
            "CanonicalizeHostname no", "ControlMaster no", "ControlPath none", "IdentityAgent none",
            "IdentitiesOnly yes", "IdentityFile none", "ForwardAgent no", "ClearAllForwardings yes",
        ]
        for number, contents in enumerate(files + [defaults or b""]):
            copy = root / f"input-{number}.conf"
            copy.write_bytes(snapshot_config(contents, local=number == len(files)))
            lines.append(f"Include {copy}")
        lines.extend(("Host *", "ConnectTimeout 15"))
        config.write_text("\n".join(lines) + "\n")
        yield config


def effective(computed, alias, options=()):
    import subprocess

    require(not alias.startswith("-"), "invalid SSH alias")
    try:
        result = subprocess.run([SSH, "-F", str(computed), "-G", *options, "--", alias],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=5, check=True, text=True)
    except (OSError, subprocess.SubprocessError):
        raise StoreContextError("OpenSSH could not resolve the selected route") from None
    fields = {}
    for line in result.stdout.splitlines():
        name, _, value = line.partition(" ")
        if name in ("hostname", "user", "port", "proxyjump"):
            fields[name] = value
    require({"hostname", "user", "port"} <= fields.keys(), "incomplete OpenSSH route")
    jump = fields.get("proxyjump", "")
    fields["proxyjump"] = render_jumps(jump, logical=True) if jump else ""
    return fields


def refresh_routes(load_account, store_id, expected):
    account = load_account()
    with resolved_store(account, store_id, expected) as (context, binding, root_fd, state_fd):
        parts = path_parts(account.state.path, context.index.parent)
        with store_state_directory(state_fd, parts) as index_fd:
            acquire_local_lock(index_fd, "store index is already being refreshed")
            stamp, hosts, prepared, local = route_inputs(account, context, binding, root_fd, state_fd, collect=True)
            with ssh_view(prepared[0], local) as config:
                routes = {host["host_id"]: dict(effective(config, host["name"]), name=host["name"])
                          for host in hosts}

            payload = {"stamp": stamp, "hosts": routes}
            data = json.dumps(payload).encode()
            require(len(data) <= JSON_LIMIT, "store route index exceeds size limit")
            with staged_local_file(index_fd, ROUTES, data) as pending:
                require(load_account() == account, "store approval changed during index refresh")
                with resolved_store(account, store_id, expected) as (current, actual, root, state):
                    require(route_inputs(account, current, actual, root, state)[0] == stamp,
                            "SSH inputs changed during index refresh")
                    verify_state_directory(state, parts, index_fd, "index directory changed")
                    os.replace(pending, ROUTES, src_dir_fd=index_fd, dst_dir_fd=index_fd)

    return "".join(f"{store_id}\t{key}\t{route['name']}\t{stamp}\n" for key, route in routes.items())


def selected_route(load_account, store_id, expected, host_id, stamp, *, collect=False):
    require(valid_id(host_id) and STAMP.fullmatch(stamp), "invalid store host selection")
    account = load_account()
    with resolved_store(account, store_id, expected) as (context, binding, root_fd, state_fd):
        current, hosts, files, local = route_inputs(account, context, binding, root_fd, state_fd, collect=collect)
        require(current == stamp, "SSH selection changed; refresh the store hosts")
        names = {host["host_id"]: host["name"] for host in hosts}
        require(host_id in names, "selected host is unavailable")
        try:
            with open_directory(state_fd, path_parts(account.state.path, context.index.parent)) as index_fd:
                require_private_directory(index_fd, "store index")
                routes = parse_json(read_file(index_fd, ROUTES, JSON_LIMIT, require_approval_file))
            require(isinstance(routes, dict) and routes.keys() == {"stamp", "hosts"}
                    and routes["stamp"] == stamp and isinstance(routes["hosts"], dict), "invalid route index")
            route = routes["hosts"].get(host_id)
            require(isinstance(route, dict) and route.keys() == ROUTE_FIELDS
                    and all(isinstance(value, str) for value in route.values())
                    and route["name"] == names[host_id] and route["hostname"] and route["user"]
                    and route["port"].isascii() and route["port"].isdecimal()
                    and len(route["port"]) <= 5 and 0 < int(route["port"]) <= 65535, "invalid route")
        except (OSError, ValueError, RecursionError):
            raise StoreContextError("SSH selection changed; refresh the store hosts") from None
        return route, files, local


def credential_targets(config, alias, route, credentials):
    eligible, seen = {}, set()
    for _ in range(32):
        normalized = alias.lower()
        signature = normalized, route["user"], route["port"]
        require(signature not in seen, "SSH jump route contains a loop")
        seen.add(signature)
        if normalized in credentials:
            ordinary = effective(config, alias)
            bound = all(ordinary[key] == route[key] for key in ("hostname", "user", "port"))
            if eligible.get(normalized, bound) and bound:
                eligible[normalized] = True
            else:
                eligible[normalized] = False
        hops = route["proxyjump"]
        if not hops or hops.lower() == "none":
            return {alias: credentials[alias] for alias, bound in eligible.items() if bound}
        remainder, _, next_hop = hops.rpartition(",")
        user, alias, port = jump_fields(next_hop)
        alias = alias.strip("[]")
        options = []
        if user:
            options.extend(("-l", user))
        if port:
            options.extend(("-p", port))
        if remainder:
            options.extend(("-J", render_jumps(remainder)))
        route = effective(config, alias, options)
    raise StoreContextError("SSH jump route is too long to bind credentials")


def launch_ssh(config, alias):
    import subprocess

    env = {key: value for key, value in os.environ.items() if not key.startswith("SSH_")}
    env.update(SHELL="/bin/sh", LC_ALL="C", PATH="/usr/bin:/bin", SSH_ASKPASS_REQUIRE="never")
    result = subprocess.run([SSH, "-F", str(config), "--", alias], env=env)
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


def connect_route(load_account, store_id, expected, host_id, stamp, *, read_passphrase=None):
    from contextlib import ExitStack

    route, prepared, local = selected_route(load_account, store_id, expected, host_id, stamp, collect=True)
    require(local[1] is not None, "registered SSH requires store-local known_hosts")
    alias = route["name"]
    files, credentials = prepared
    with ExitStack() as resources:
        config = resources.enter_context(ssh_view(files, local))
        settings = effective(config, alias)
        require(settings == {key: value for key, value in route.items() if key != "name"},
                "effective SSH selection changed; refresh the store hosts")
        verify_credentials = None
        if credentials and (targets := credential_targets(config, alias, settings, credentials)):
            from store_login import scoped_login
            verify_credentials = resources.enter_context(
                scoped_login(load_account, store_id, expected, config, targets, read_passphrase)
            )
        require(selected_route(load_account, store_id, expected, host_id, stamp)[0] == route,
                "SSH selection changed; refresh the store hosts")
        if verify_credentials:
            verify_credentials()
        return launch_ssh(config, alias)


def route_command(action, args):
    listing = action.endswith("hosts")
    selector_count = 0 if listing else 2
    if action.startswith("_") and len(args) == selector_count:
        account, store_id, expected = fixture_request()
        def load():
            return account
    else:
        require(len(args) == 5 + selector_count, "store ID, expected generations and selection are required")
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
        store_id = args[0]
        expected = Generations(*(int(value) for value in args[1:5]))
        args = args[5:]
        def load():
            return load_approved_account(home)
    if listing:
        sys.stdout.write(refresh_routes(load, store_id, expected))
    else:
        host_id, stamp = args
        if action.endswith("connect"):
            try:
                return connect_route(load, store_id, expected, host_id, stamp)
            except KeyboardInterrupt:
                return 130
        route, _, _ = selected_route(load, store_id, expected, host_id, stamp)
        print(json.dumps(dict(route, store_id=store_id, host_id=host_id)))
    return 0
