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
    JSON_LIMIT, Generations, StoreContextError, acquire_local_lock, bound_directory, fixture_request,
    index_rows, load_approved_account, open_directory, path_parts, read_file, require,
    require_approval_file, require_private_directory, resolved_store, staged_local_file,
    store_state_directory, valid_id, verify_state_directory,
)


SSH = "/usr/bin/ssh"
ROUTES = "routes.json"
ROUTE_VERSION = 1
STAMP = re.compile(r"[0-9a-f]{64}")
SSH_OPTIONS = frozenset("""
    addressfamily batchmode checkhostip compression connectionattempts connecttimeout
    host hostname ipqos kbdinteractiveauthentication loglevel match numberofpasswordprompts
    passwordauthentication port preferredauthentications proxyjump pubkeyauthentication
    requesttty serveralivecountmax serveraliveinterval tcpkeepalive user
""".split())
LOCAL_OPTIONS = SSH_OPTIONS | {"globalknownhostsfile", "userknownhostsfile", "stricthostkeychecking"}
DIRECTIVE = re.compile(r"([A-Za-z][A-Za-z0-9]*)(?:[ \t]+=?[ \t]*|=[ \t]*)(.*)")
SSH_NAME = re.compile(r"[A-Za-z0-9._-]+")
SSH_HOST = re.compile(r"(?:[A-Za-z0-9_.:\[\]-]|%h)+")
SECRET_COMMENT = re.compile(rb"(?m)^[ \t]*#[ \t]*secret[ \t]*:")
JUMP = re.compile(r"(?:ssh://)?(?:[A-Za-z0-9._-]+@)?(?:[A-Za-z0-9_][A-Za-z0-9_.-]*|\[[A-Fa-f0-9:]+\])(?::[0-9]+)?")


def validate_config(contents, local=False):
    import shlex

    # Admit data-only directives before OpenSSH sees the view, even in inactive sections.
    for line in contents.decode("utf-8").split("\n"):
        line = line.rstrip("\r").strip(" \t")
        if not line or line.startswith("#"):
            continue
        match = DIRECTIVE.fullmatch(line)
        require(not re.search(r"[\x00-\x08\x0a-\x1f\x7f]", line) and match, "invalid SSH directive")
        option, value = match[1].lower(), match[2]
        require(option in (LOCAL_OPTIONS if local else SSH_OPTIONS), f"unapproved SSH directive: {option}")
        if option in ("match", "proxyjump", "hostname", "user"):
            # OpenSSH considers '#' a comment only at the beginning of an argument.
            value = re.split(r"(?:^|[ 	])#", value, maxsplit=1)[0].rstrip(" 	")
            require(not any(mark in value for mark in ("\\", "'", '"')), f"unsupported SSH directive: {option}")
            args = re.split(r"[ 	]+", value) if value else []
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
            require(len(args) == 1 and all(JUMP.fullmatch(hop) for hop in args[0].split(",")),
                    "invalid SSH directive: ProxyJump")
        elif option in ("hostname", "user"):
            pattern = SSH_HOST if option == "hostname" else SSH_NAME
            require(len(args) == 1 and pattern.fullmatch(args[0]) and not args[0].startswith("-"),
                    f"invalid SSH directive: {option}")
        elif option == "stricthostkeychecking":
            require(args in (["yes"], ["ask"]), "invalid SSH directive: StrictHostKeyChecking")


def local_inputs(state_fd, store_id):
    stack = (state_fd, ("stores", "v1", store_id, "ssh"))
    try:
        with open_directory(*stack) as fd:
            require_private_directory(fd, "approved SSH inputs")
            return read_file(fd, "config", JSON_LIMIT, require_approval_file)
    except FileNotFoundError:
        return b""


def route_inputs(account, context, binding, root_fd, state_fd):
    files = {}
    with bound_directory(root_fd, path_parts(account.root.path, binding.root.path),
                         binding.root, "checkout binding mismatch") as checkout_fd:
        identity, _ = index_rows(checkout_fd, context.store_id, files)
    local = local_inputs(state_fd, context.store_id)
    fingerprint = hashlib.sha256(json.dumps(
        [ROUTE_VERSION, asdict(context), identity], default=os.fspath, sort_keys=True
    ).encode())
    fingerprint.update(local)
    return fingerprint.hexdigest(), identity["hosts"], list(files.values()), local


@contextmanager
def ssh_view(files, local):
    import tempfile

    # OpenSSH passes -F to ProxyJump via the shell, without quoting the path.
    with tempfile.TemporaryDirectory(prefix="hb-route-", dir="/tmp") as folder:
        root = Path(folder)
        config = root / "config"
        lines = [
            "CanonicalizeHostname no", "ControlMaster no", "ControlPath none", "IdentityAgent none",
            "IdentitiesOnly yes", "IdentityFile none", "ForwardAgent no", "ClearAllForwardings yes",
        ]
        for number, contents in enumerate(files + [local]):
            validate_config(contents, local=number == len(files))
            copy = root / f"input-{number}.conf"
            copy.write_bytes(contents)
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
    fields.setdefault("proxyjump", "")
    return fields


def refresh_routes(load_account, store_id, expected):
    account = load_account()
    with resolved_store(account, store_id, expected) as (context, binding, root_fd, state_fd):
        parts = path_parts(account.state.path, context.index.parent)
        with store_state_directory(state_fd, parts) as index_fd:
            acquire_local_lock(index_fd, "store index is already being refreshed")
            stamp, hosts, files, local = route_inputs(account, context, binding, root_fd, state_fd)
            with ssh_view(files, local) as config:
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


def selected_route(load_account, store_id, expected, host_id, stamp):
    require(valid_id(host_id) and STAMP.fullmatch(stamp), "invalid store host selection")
    account = load_account()
    with resolved_store(account, store_id, expected) as (context, binding, root_fd, state_fd):
        current, hosts, files, local = route_inputs(account, context, binding, root_fd, state_fd)
        require(current == stamp, "SSH selection changed; refresh the store hosts")
        names = {host["host_id"]: host["name"] for host in hosts}
        require(host_id in names, "selected host is unavailable")
        with open_directory(state_fd, path_parts(account.state.path, context.index.parent)) as index_fd:
            require_private_directory(index_fd, "store index")
            routes = json.loads(read_file(index_fd, ROUTES, JSON_LIMIT, require_approval_file))
        require(routes["stamp"] == stamp and host_id in routes["hosts"]
                and routes["hosts"][host_id]["name"] == names[host_id],
                "SSH selection changed; refresh the store hosts")
        return routes["hosts"][host_id], files, local


def connect_route(load_account, store_id, expected, host_id, stamp):
    import subprocess

    selection = selected_route(load_account, store_id, expected, host_id, stamp)
    route, files, local = selection
    require(not any(SECRET_COMMENT.search(contents) for contents in files),
            "store SSH credential binding requires an approved login")
    alias = route["name"]
    with ssh_view(files, local) as config:
        require(effective(config, alias) == {key: value for key, value in route.items() if key != "name"},
                "effective SSH selection changed; refresh the store hosts")
        require(selected_route(load_account, store_id, expected, host_id, stamp) == selection,
                "SSH selection changed; refresh the store hosts")
        env = {key: value for key, value in os.environ.items() if not key.startswith("SSH_")}
        env.update(SHELL="/bin/sh", LC_ALL="C", PATH="/usr/bin:/bin")
        result = subprocess.run([SSH, "-F", str(config), "--", alias], env=env)
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


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
            return connect_route(load, store_id, expected, host_id, stamp)
        route, _, _ = selected_route(load, store_id, expected, host_id, stamp)
        print(json.dumps(dict(route, store_id=store_id, host_id=host_id)))
    return 0
