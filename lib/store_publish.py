"""Conditionally publish one existing registered host's literal address."""
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
from uuid import uuid4

from store_checkout import durable_file, make_parents, read_ref, rename_guarded, tree
from store_context import (JSON_LIMIT, Generations, StoreContextError, bound_directory, fields, fixture_request,
                           index_rows, load_approved_account, open_directory, parse_directory_binding, parse_json, path_parts, positive, read_file, require,
                           require_approval_file, require_private_directory, staged_local_file, store_writer,
                           sync_local, valid_id, valid_ref, verified_account, verify_path_isolation)
from store_journal import classify, private_json
from store_read import DIRECTIVE, INDEX_NAME, local_inputs, managed_credentials, selected_route
from store_receive import (GIT, SAFE_ENV, IDENTITY, absolute_directory, fetch_from_fd, git, git_result, oid, plain_git,
                           receive, receive_binding, verify_incoming)


@contextmanager
def recovery_root(account):
    with verified_account(account) as (root, _state):
        with bound_directory(root, path_parts(account.root.path, account.recovery.path), account.recovery,
                             "recovery root changed") as recovery:
            yield recovery


def boot_id():
    if sys.platform == "darwin":
        value = subprocess.check_output(["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"],
                                        env={"PATH": "/usr/bin:/bin"}, stderr=subprocess.DEVNULL, timeout=5)
    else:
        value = Path("/proc/sys/kernel/random/boot_id").read_bytes()
    identity = value.decode().strip().lower()
    require(valid_id(identity), "system boot identity unavailable")
    return identity


def record_at(stage, rev, store_id, host_id):
    files = tree(stage, rev, git)
    def contents(path):
        require(path in files, "selected host source is unavailable")
        return git(stage, "cat-file", "blob", files[path].decode())
    return stanza(parse_json(contents(IDENTITY)), contents, store_id, host_id)


def stanza(identity, contents, store_id, host_id):
    require(identity["store_id"] == store_id, "selected store changed")
    matches = [h for h in identity["hosts"] if h["host_id"] == host_id]
    require(len(matches) == 1 and len(matches[0]["bindings"]) == 1, "edit needs one existing host stanza")
    host = matches[0]
    binding = host["bindings"][0]
    selected, result = host["name"].lower(), None
    source_files = {}
    for source in identity["sources"]:
        path = source["path"]
        text = source_files[path] = contents(path)
        require(hashlib.sha256(text).hexdigest() == source["sha256"], "host source revision mismatch")
        lines = text.splitlines(keepends=True)
        alias, ordinal, start, address, last = "", 0, None, None, None
        for idx in range(len(lines) + 1):
            line = lines[idx].decode().strip() if idx < len(lines) else ""
            directive = DIRECTIVE.fullmatch(line)
            if idx == len(lines) or (directive and directive[1].lower() == "host"):
                if alias == selected:
                    require(result is None and binding == dict(path=path, host_ordinal=ordinal, token_ordinal=1)
                            and address is not None, "ambiguous selected host stanza")
                    result = (identity, path, lines, address, (host["name"], path, tuple(lines[start:last+1])))
                if idx == len(lines):
                    break
                name = directive[2].split("#", 1)[0].strip()
                require(INDEX_NAME.fullmatch(name), "address edit requires independent literal host stanzas")
                alias, ordinal, start, address, last = name.lower(), ordinal + 1, idx, None, idx
                while start > 0 and lines[start-1].lstrip().startswith(b"#"):
                    start -= 1
            elif directive:
                opt = directive[1].lower()
                require(alias and opt not in ("match", "include"), "address edit requires independent literal host stanzas")
                last = idx
                if alias == selected and opt == "hostname":
                    require(address is None, "multiple HostName values require review")
                    address = idx
            elif line and not line.startswith("#"):
                raise StoreContextError("unsupported existing host stanza")
    require(result is not None, "selected host is unavailable")
    _, credentials = managed_credentials(source_files, identity["hosts"])
    return (*result[:4], (*result[4], credentials.get(selected)))


def prepare_proposal(stage, base, tip, store_id, host_id, address, attempt, signature):
    captured = record_at(stage, base, store_id, host_id)[4]
    require(captured == signature, "selected working host is not the accepted revision")
    commits = git(stage, "rev-list", "--first-parent", "--reverse", "--max-count=257", tip, "^" + base).splitlines()
    require(len(commits) <= 256 and (not commits or oid(stage, commits[0].decode() + "^1") == base),
            "receive the current branch before editing")
    for rev in commits:
        require(record_at(stage, rev.decode(), store_id, host_id)[4] == captured, "selected host revision changed")
    identity, path, lines, pos, _ = record_at(stage, tip, store_id, host_id)
    match = re.fullmatch(rb"([ \t]*hostname(?:[ \t]+=?[ \t]*|=[ \t]*))([^ \t#\r\n]+)([ \t]*(?:#[^\r\n]*)?)(\r?\n)?",
                         lines[pos], re.I)
    require(match is not None, "unsupported HostName value")
    require(match[2] != address.encode(), "selected address is unchanged")
    lines[pos] = match[1] + address.encode() + match[3] + (match[4] or b"")
    updated = b"".join(lines)
    next(source for source in identity["sources"] if source["path"] == path)["sha256"] = hashlib.sha256(updated).hexdigest()
    identity["generation"] += 1
    git(stage, "read-tree", tip)
    for name, data in ((path, updated), (IDENTITY, json.dumps(identity, ensure_ascii=False).encode() + b"\n")):
        blob = git(stage, "hash-object", "-w", "--stdin", data=data).strip().decode()
        git(stage, "update-index", "--add", "--cacheinfo", "100644", blob, name)
    root = oid(stage, git(stage, "write-tree").strip().decode())
    proposal = git(stage, "-c", "user.name=Hostbook", "-c", "user.email=hostbook@localhost",
                   "commit-tree", root, "-p", tip, "-m", "Edit registered host " + attempt).strip().decode()
    git(stage, "update-ref", "refs/hostbook/proposal", proposal)
    return proposal


def require_plain_receiver(target_fd):
    plain_git(target_fd, bare=True)
    # Local receive-pack executes hooks as the device account. Never bypass a server's installed policy.
    try:
        with open_directory(target_fd, ("hooks",)) as hooks:
            for name in os.listdir(hooks):
                if not name.endswith(".sample"):
                    require(not os.stat(name, dir_fd=hooks, follow_symlinks=False).st_mode & 0o111,
                            "local publication requires a repository without executable server hooks")
    except FileNotFoundError:
        pass
    require(os.access("refs/heads", os.W_OK, dir_fd=target_fd)
            and os.access("objects", os.W_OK, dir_fd=target_fd), "registered remote denies write access")


def json_bytes(record):
    return json.dumps(record, sort_keys=True).encode()


def selector(record):
    return json_bytes({key:record[key] for key in ("version", "record_id", "sequence")})


def reserve(edit, recovery, record):
    with make_parents(recovery, ("control",)) as controls:
        require_private_directory(controls, "publication controls")
        os.mkdir("control", 0o700, dir_fd=edit)
        with open_directory(edit, ("control",)) as draft:
            with make_parents(draft, ("records",)) as history:
                durable_file(history, "1.json", json_bytes(record))
                sync_local(history)
            durable_file(draft, "current.json", selector(record))
            sync_local(draft)
        rename_guarded(edit, "control", controls, record["record_id"], exchange=False)
        sync_local(controls)
        sync_local(edit)


def advance(recovery, account, original, outcome):
    with open_directory(recovery, ("control", original["record_id"])) as control_fd:
        classify(control_fd, account, original["record_id"], "control")
        selected = private_json(control_fd, "current.json")
        n = selected["sequence"]
        with open_directory(control_fd, ("records",)) as history:
            require(private_json(history, "1.json") == original, "publication reservation changed")
            current = private_json(history, f"{n}.json")
            require(original["detail"]["attempt_id"] == current["detail"]["attempt_id"]
                    and current["detail"]["outcome"] in ("reserved", "uncertain"), "publication already settled")
            rec = dict(current, previous_sequence=n, sequence=n+1,
                       detail=dict(current["detail"], sender_state="quiescent", outcome=outcome))
            name = f"{n+1}.json"
            with staged_local_file(control_fd, name, json_bytes(rec)) as temp:
                sync_named(control_fd, temp)
                os.link(temp, name, src_dir_fd=control_fd, dst_dir_fd=history, follow_symlinks=False)
                sync_local(history)
            select_record(control_fd, rec)


def select_record(control_fd, rec):
    with staged_local_file(control_fd, "current.json", selector(rec)) as temp:
        sync_named(control_fd, temp)
        os.replace(temp, "current.json", src_dir_fd=control_fd, dst_dir_fd=control_fd)
        sync_local(control_fd)


def sync_named(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        sync_local(fd)
    finally:
        os.close(fd)


def send(stage, target_fd, lease_fd, ref, tip, proposed):
    require_plain_receiver(target_fd)
    client, remote = socket.socketpair()
    with client, remote:
        # Only the admitted parent opens receive-pack. Its cwd is the verified directory inode.
        server = subprocess.Popen([GIT, "receive-pack", "."], env=SAFE_ENV,
                                  stdin=remote, stdout=remote, stderr=subprocess.DEVNULL,
                                  preexec_fn=lambda: os.fchdir(target_fd), pass_fds=(target_fd, lease_fd))
        remote.close()
        relay = shlex.join([sys.executable, "-I", "-B", "-S", str(Path(__file__).with_name("store_receive.py")),
                            "relay", str(client.fileno())])
        try:
            rc, output = git_result(stage, "push", "--porcelain", "--receive-pack=" + relay,
                                    f"--force-with-lease={ref}:{tip}", "--", "file:///unused", f"{proposed}:{ref}",
                                    pass_fds=(client.fileno(), lease_fd))
            client.close()
            if server.wait(timeout=10) != 0 and rc == 0:
                return "uncertain"
        finally:
            client.close()
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
    rows = [line.split(b"\t") for line in output.splitlines()]
    for row in rows:
        if len(row) == 3 and row[1] == f"{proposed}:{ref}".encode():
            if rc == 0 and row[0] in (b" ", b"="):
                return "accepted"
            if row[0] == b"!" and row[2].startswith((b"[rejected]", b"[remote rejected]")):
                return "not-accepted"
    return "uncertain"


def publish_address(load_account, store_id, expected, host_id, stamp, text):
    require("%" not in text, "provide one unscoped IP address")
    try:
        address = str(ipaddress.ip_address(text))
    except ValueError:
        raise StoreContextError("provide one literal IPv4 or IPv6 address") from None
    selected_route(load_account, store_id, expected, host_id, stamp)
    with store_writer(load_account, store_id, expected) as (account, start, state_fd, _):
        require(start.kind == "shared" and start.effective_mode == "two-way", "shared edit requires effective two-way mode")
        with receive_binding(account, store_id, expected) as (ctx, target, _, _, binding):
            local = local_inputs(state_fd, store_id)
            with absolute_directory(ctx.checkout) as work:
                info = os.fstat(work)
                require((info.st_dev, info.st_ino) == (binding.root.device, binding.root.inode), "checkout changed")
                files = {}
                identity, _ = index_rows(work, store_id, files)
                signature = stanza(identity, files.__getitem__, store_id, host_id)[4]
            with absolute_directory(ctx.git_dir) as git_fd:
                info = os.fstat(git_fd)
                require((info.st_dev, info.st_ino) == (binding.git_dir.device, binding.git_dir.inode), "Git binding changed")
                plain_git(git_fd, bare=False)
                base = read_ref(git_fd, ctx.ref)
    attempt, binding_id = str(uuid4()), str(uuid4())
    with tempfile.TemporaryDirectory(prefix="publish-", dir=account.state.path) as temp:
        stage = Path(temp)
        git(stage, "init", "-q", "--bare", "--template=")
        with receive_binding(account, store_id, expected) as (current, remote, target_fd, _, checkout):
            require((current, remote, checkout) == (ctx, target, binding), "registered binding changed")
            require_plain_receiver(target_fd)
            tip = fetch_from_fd(stage, target_fd, ctx.ref, "refs/hostbook/incoming")
            verify_incoming(stage, tip, base, store_id, local)
            proposed = prepare_proposal(stage, base, tip, store_id, host_id, address, attempt, signature)
            shutil.rmtree(stage / "validated")
            verify_incoming(stage, proposed, tip, store_id, local)
            bundle = stage / "proposal.bundle"
            git(stage, "bundle", "create", str(bundle), "refs/hostbook/proposal", "^" + tip)
            result = publish_reserved(load_account, account, ctx, target, target_fd, binding, host_id, stamp,
                                      base, tip, proposed, attempt, binding_id, stage, bundle)
    if result["outcome"] == "accepted":
        try:
            receive(load_account, store_id, expected)
            result["local"] = "received"
        except (OSError, StoreContextError):
            result["local"] = "reconciliation-required"
    return result


def publish_reserved(load_account, account, ctx, target, target_fd, binding, host_id, stamp,
                     base, tip, proposed, attempt, binding_id, stage, bundle):
    expected, store_id = ctx.generations, ctx.store_id
    record = {"format":"hostbook-control", "version":1, "record_id":attempt, "device_id":account.device_id,
              "root_id":account.recovery_root_id, "sequence":1, "previous_sequence":0, "store_id":store_id,
              "owner":{"owner_id":str(uuid4()), "process_id":os.getpid(), "process_start_id":str(uuid4()), "boot_id":boot_id()},
              "operation":"publish", "detail":{"attempt_id":attempt, "connection_generation":expected.connection,
              "connection_binding_id":binding_id, "base_oid":tip, "proposed_oid":proposed,
              "sender_state":"active", "outcome":"reserved"}}
    with recovery_root(account) as recovery, ExitStack() as opened:
        with store_writer(load_account, store_id, expected) as (actual, locked, _, _lock):
            require((actual, locked) == (account, ctx), "registered publication binding changed")
            with receive_binding(actual, store_id, expected) as (active, live, _, _, checked):
                require((active, live, checked) == (ctx, target, binding), "registered publication binding changed")
                selected_route(load_account, store_id, expected, host_id, stamp)
                with absolute_directory(ctx.git_dir) as local_git:
                    info = os.fstat(local_git)
                    require((info.st_dev, info.st_ino) == (binding.git_dir.device, binding.git_dir.inode)
                            and read_ref(local_git, ctx.ref) == base, "registered host base changed")
                with make_parents(recovery, ("edits",)) as edits:
                    require_private_directory(edits, "publication evidence")
                    os.mkdir(attempt, 0o700, dir_fd=edits)
                    sync_local(edits)
                    edit = opened.enter_context(open_directory(edits, (attempt,)))
                fd = os.open("lease", os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=edit)
                lease = opened.enter_context(os.fdopen(fd, "r+b"))
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                lease_info = os.fstat(fd)
                out = os.open("proposal.bundle", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=edit)
                with os.fdopen(out, "wb") as dest, bundle.open("rb") as src:
                    shutil.copyfileobj(src, dest)
                    dest.flush()
                    sync_local(out)
                evidence = {"version":1, "binding_id":binding_id, "store_id":store_id, "device_id":account.device_id,
                            "target_id":ctx.target_id, "uid":os.geteuid(), "ref":ctx.ref,
                            "generations":asdict(expected), "bare_repository":dict(asdict(target),path=str(target.path)),
                            "lease_device":lease_info.st_dev, "lease_inode":lease_info.st_ino, "host_id":host_id}
                durable_file(edit, "binding.json", json_bytes(evidence))
                sync_local(fd)
                sync_local(edit)
                reserve(edit, recovery, record)
        # The admitted client and receive-pack both inherit this lease through transport completion.
        try:
            durable_file(edit, "send-started", attempt.encode())
            sync_local(edit)
            outcome = send(stage, target_fd, fd, ctx.ref, tip, proposed)
        except (OSError, StoreContextError, subprocess.TimeoutExpired):
            outcome = "uncertain"
        finally:
            lease.close()
        try:
            with open_lease(edit, evidence):
                with store_writer(load_account, store_id, expected) as (actual, _, _, _lock):
                    require(actual == account, "publication account changed")
                    advance(recovery, actual, record, outcome)
        except (OSError, StoreContextError):
            outcome = "uncertain"
    return {"store_id":store_id, "attempt_id":attempt, "outcome":outcome}


@contextmanager
def open_lease(edit, evidence):
    fd = os.open("lease", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=edit)
    try:
        info = os.fstat(fd)
        require_approval_file(fd, info)
        require((info.st_dev, info.st_ino) == (evidence["lease_device"], evidence["lease_inode"]),
                "publication lease changed")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StoreContextError("publication sender is still active") from None
        yield
    finally:
        os.close(fd)


def read_attempt(recovery, account, store_id, attempt, repair=False):
    with open_directory(recovery, ("control", attempt)) as control_fd:
        require_private_directory(control_fd, "publication control")
        classify(control_fd, account, attempt, "control")
        selected = private_json(control_fd, "current.json")["sequence"]
        with open_directory(control_fd, ("records",)) as history:
            original = private_json(history, "1.json")
            require(original["operation"] == "publish" and original["store_id"] == store_id and
                    original["detail"]["attempt_id"] == attempt, "publication reservation mismatch")
            tail = private_json(history, f"{len(os.listdir(history))}.json")
            if tail["sequence"] != selected:
                require(tail["sequence"] == selected + 1, "ambiguous publication history")
                if repair:
                    select_record(control_fd, tail)
            return original, tail


def attempt_binding(edit, account, original):
    require_private_directory(edit, "publication evidence")
    record = private_json(edit, "binding.json")
    fields(record, "version binding_id store_id device_id target_id uid ref generations bare_repository "
                   "lease_device lease_inode host_id")
    fields(record["generations"], "registration checkout connection policy")
    detail = original["detail"]
    require(type(record["version"]) is int and record["version"] == 1 and record["device_id"] == account.device_id
            and record["binding_id"] == detail["connection_binding_id"] and record["store_id"] == original["store_id"]
            and type(record["uid"]) is int and record["uid"] == os.geteuid() and valid_ref(record["ref"])
            and valid_id(record["host_id"]) and valid_id(record["target_id"])
            and all(positive(k) for k in record["generations"].values())
            and record["generations"]["connection"] == detail["connection_generation"]
            and type(record["lease_device"]) is int and record["lease_device"] >= 0 and positive(record["lease_inode"]),
            "invalid original publication binding")
    return record


def observe_attempt(account, edit, original, evidence):
    try:
        started = read_file(edit, "send-started", 64, require_approval_file)
    except FileNotFoundError:
        # The exclusive sender lease is already held. Launch always follows the durable marker.
        return "not-accepted"
    require(started == original["record_id"].encode(), "incomplete send marker")
    target = parse_directory_binding(evidence["bare_repository"])
    with verified_account(account) as (root, _state):
        groups = [(target,), (account.state,), (account.recovery,)]
        groups.extend((b.root, b.git_dir, b.git_common_dir) for b in account.checkouts.values())
        verify_path_isolation(root, account.root.path, groups)
        with bound_directory(root, path_parts(account.root.path, target.path), target,
                             "original registered transport changed") as directory:
            plain_git(directory, bare=True)
            with tempfile.TemporaryDirectory(prefix="settle-", dir=account.state.path) as folder:
                stage = Path(folder)
                git(stage, "init", "-q", "--bare", "--template=")
                tip = fetch_from_fd(stage, directory, evidence["ref"], "refs/hostbook/observed")
                proposal = original["detail"]["proposed_oid"]
                if git_result(stage, "merge-base", "--is-ancestor", proposal, tip)[0] == 0:
                    return "accepted"
    return "uncertain"


def settle(load_account, store_id, expected, attempt):
    require(valid_id(attempt), "invalid publication attempt")
    account = load_account()
    with recovery_root(account) as recovery, open_directory(recovery, ("edits", attempt)) as edit:
        original, _ = read_attempt(recovery, account, store_id, attempt)
        evidence = attempt_binding(edit, account, original)
        with open_lease(edit, evidence):
            with store_writer(load_account, store_id, expected) as (current, _, _, _lock):
                require(current == account, "publication account changed")
                _, tail = read_attempt(recovery, account, store_id, attempt, repair=True)
                outcome = tail["detail"]["outcome"]
            if outcome in ("reserved", "uncertain"):
                try:
                    result = observe_attempt(account, edit, original, evidence)
                except (OSError, StoreContextError):
                    result = "uncertain"
                with store_writer(load_account, store_id, expected) as (current, _, _, _lock):
                    require(current == account, "publication account changed")
                    if result != outcome:
                        advance(recovery, current, original, result)
                outcome = result
    return {"store_id":store_id, "attempt_id":attempt, "outcome":outcome}


def settle_command(action, args):
    if action == "_store_settle":
        require(len(args) == 1, "one publication attempt is required")
        account, store_id, expected = fixture_request()
        def load():
            return account
    else:
        require(len(args) == 6, "store ID, four generations and attempt ID are required")
        store_id, expected = args[0], Generations(*(int(x) for x in args[1:5]))
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
        def load():
            return load_approved_account(home)
    result = settle(load, store_id, expected, args[-1])
    print(json.dumps(result))
    return 0 if result["outcome"] in ("accepted", "not-accepted") else 75


def edit_command(action, args):
    if action == "_store_edit_address" and len(args) == 2:
        account, store_id, expected, address = fixture_request("address")
        def load():
            return account
    else:
        require(len(args) == 7, "store ID, four generations, host ID and selection stamp are required")
        store_id, expected = args[0], Generations(*(int(x) for x in args[1:5]))
        args = args[5:]
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
        def load():
            return load_approved_account(home)
        address = sys.stdin.buffer.readline(128).decode().strip()
    require(isinstance(address, str) and len(address) < 128, "invalid address input")
    result = publish_address(load, store_id, expected, *args, address)
    print(json.dumps(result))
    return 0 if result["outcome"] == "accepted" else 75
