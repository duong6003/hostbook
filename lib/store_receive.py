"""Receive a validated fast-forward from an approved local bare Git repository."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from uuid import uuid4
import pwd
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import unicodedata

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from store_context import (
    DIRECTORY_FLAGS, JSON_LIMIT, Generations, StoreContextError, bound_directory, fields,
    fixture_request, index_rows, load_approved_account, open_directory, parse_directory_binding,
    parse_json, path_parts, read_file, require, require_approval_file, require_private_directory,
    resolved_store, staged_local_file, store_writer, sync_local, verify_path_isolation,
    verify_state_directory,
)
from store_checkout import OID, direct_ref, check_index, check_worktree, fast_forward, prepare_receive_files, read_index, read_ref, tree
from store_journal import require_idle
from store_read import MAX_ROUTE_BYTES, effective, local_inputs, managed_credentials, ssh_view

GIT = "/usr/bin/git"
SAFE_ENV = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1", "GIT_ALLOW_PROTOCOL": "file", "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
IDENTITY = ".hostbook/identity.json"


def git_result(cwd, *args, data=b"", pass_fds=()):
    cmd = [GIT, "-c", "core.hooksPath=/dev/null", "-c", "core.attributesFile=/dev/null",
           "-c", "maintenance.auto=false", "-c", "gc.auto=0", "-c", "fetch.fsckObjects=true", *args]
    with subprocess.Popen(cmd, cwd=cwd, env=SAFE_ENV, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, pass_fds=pass_fds) as child:
        try:
            out, _ = child.communicate(data, timeout=30)
        except BaseException as error:
            # Stay in the terminal's foreground group. Git forwards termination to its transport.
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
            if isinstance(error, subprocess.TimeoutExpired):
                raise StoreContextError("registered Git operation timed out") from None
            raise
    return child.returncode, out


def git(cwd, *args, data=b"", pass_fds=()):
    rc, out = git_result(cwd, *args, data=data, pass_fds=pass_fds)
    require(rc == 0, "registered Git operation failed")
    return out


def oid(repo, ref):
    output = git(repo, "rev-parse", "--verify", ref).strip()
    require(OID.fullmatch(output), "expected a Git commit ID")
    return output.decode("ascii")


def plain_git(directory_fd, *, bare):
    # Only regular repositories are supported. Do not allow configuration redirects or filters.
    contents = read_file(directory_fd, "config", 65536)
    config = git("/", "config", "--no-includes", "--file", "-", "--null", "--list", data=contents)
    permitted = {
        b"core.repositoryformatversion": {b"0"}, b"core.filemode": {b"true", b"false"},
        b"core.bare": {b"true" if bare else b"false"}, b"core.logallrefupdates": {b"true", b"false"},
        b"core.ignorecase": {b"true", b"false"}, b"core.precomposeunicode": {b"true", b"false"},
        b"user.name": None, b"user.email": None,
    }
    bare_setting = False
    filemode, log_updates = True, not bare
    for item in config.rstrip(b"\0").split(b"\0"):
        key, _, value = item.partition(b"\n")
        if re.fullmatch(rb"(?:remote\.[a-zA-Z0-9_-]+\.(?:url|fetch)|branch\.[^\x00-\x20]+\.(?:remote|merge))", key):
            continue
        require(key in permitted and (permitted[key] is None or value in permitted[key]),
                "unsupported registered Git configuration")
        bare_setting |= key == b"core.bare"
        if key == b"core.filemode":
            filemode = value == b"true"
        if key == b"core.logallrefupdates":
            log_updates = value == b"true"
    require(bare_setting, "missing registered Git configuration")
    for name in ("commondir", "shallow", "objects/info/alternates", "objects/info/http-alternates",
                 "info/grafts", "info/attributes", "info/sparse-checkout", "MERGE_HEAD", "REVERT_HEAD",
                 "CHERRY_PICK_HEAD", "rebase-merge", "rebase-apply", "sequencer"):
        try:
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        raise StoreContextError("unsupported or unfinished Git operation")
    for _, folders, names, fd in os.fwalk(".", dir_fd=directory_fd, follow_symlinks=False, onerror=raise_walk_error):
        for name in folders + names:
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require(stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1),
                    "unsupported linked Git resource")
            require(not name.endswith(".lock"), "registered Git repository is busy")
    return filemode, log_updates


def raise_walk_error(exc):
    raise exc


def require_no_receive_pending(store_fd, own_record):
    if own_record is None:
        require("receive.pending" not in os.listdir(store_fd), "receive requires reconciliation")
    else:
        actual = read_file(store_fd, "receive.pending", 4096, require_approval_file)
        require(actual == json.dumps(own_record).encode(), "receive evidence changed")
    # Early versions recorded local receives under their original connection generation.
    if "connection" not in os.listdir(store_fd):
        return
    with open_directory(store_fd, ("connection",)) as connections:
        for name in os.listdir(connections):
            require(name.isascii() and name.isdigit(), "invalid connection history")
            with open_directory(connections, (name,)) as generation:
                require("receive.pending" not in os.listdir(generation), "older receive requires reconciliation")


def publish_receive_marker(store_fd, record):
    name = "receive.pending"
    with staged_local_file(store_fd, name, json.dumps(record).encode()) as staged:
        fd = os.open(staged, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=store_fd)
        try:
            sync_local(fd)
        finally:
            os.close(fd)
        os.link(staged, name, src_dir_fd=store_fd, dst_dir_fd=store_fd, follow_symlinks=False)
        sync_local(store_fd)


@contextmanager
def receive_binding(account, store_id, expected, own_record=None):
    with resolved_store(account, store_id, expected) as (context, checkout, root_fd, state_fd):
        require(context.kind == "shared" and context.effective_mode in ("receive-only", "two-way")
                and context.transition_id is None, "a connected unfenced shared store is required")
        require(checkout.git_dir.path == context.checkout / ".git" and checkout.git_common_dir == checkout.git_dir,
                "receive requires a regular Git checkout")
        with bound_directory(root_fd, path_parts(account.root.path, account.recovery.path), account.recovery,
                             "invalid recovery root") as recovery_fd:
            require_idle(recovery_fd, account, store_id)
        parts = path_parts(account.state.path, context.status.parent)
        with open_directory(state_fd, parts) as settings:
            require_private_directory(settings, "connection settings")
            record = parse_json(read_file(settings, "target.json", 16384, require_approval_file))
            fields(record, "version store_id target_id connection_generation ref uid bare_repository")
            require(type(record["version"]) is int and record["version"] == 1 and record["store_id"] == store_id
                    and record["target_id"] == context.target_id and record["ref"] == context.ref
                    and type(record["connection_generation"]) is int
                    and record["connection_generation"] == expected.connection and type(record["uid"]) is int
                    and record["uid"] == os.geteuid(), "registered transport binding mismatch")
            target = parse_directory_binding(record["bare_repository"])
            groups = [(target,), (account.state,), (account.recovery,)]
            groups.extend((b.root, b.git_dir, b.git_common_dir) for b in account.checkouts.values())
            verify_path_isolation(root_fd, account.root.path, groups)
            with bound_directory(root_fd, path_parts(account.root.path, target.path), target,
                                 "registered transport changed") as target_fd:
                with open_directory(state_fd, parts[:3]) as store_fd:
                    require_private_directory(store_fd, "store state")
                    require_no_receive_pending(store_fd, own_record)
                    yield context, target, target_fd, store_fd, checkout


@contextmanager
def absolute_directory(path):
    root = os.open("/", DIRECTORY_FLAGS)
    try:
        with open_directory(root, path.parts[1:]) as fd:
            yield fd
    finally:
        os.close(root)


def verify_incoming(stage, tip, base, store_id, local, requested=None, *, vault_account=None):
    git(stage, "merge-base", "--is-ancestor", base, tip)
    changed = set(git(stage, "diff", "--no-renames", "--name-only", "-z", base, tip).split(b"\0")[:-1])
    incoming_files = tree(stage, tip, git)
    view = stage / "validated"
    view.mkdir()
    collected, sources, seen = 0, set(), {}
    for path, blob in incoming_files.items():
        components = path.split("/")
        for i in range(1, len(components) + 1):
            prefix = "/".join(components[:i])
            key = unicodedata.normalize("NFC", prefix).casefold()
            item = (prefix, i == len(components))
            require(seen.setdefault(key, item) == item, "incoming file paths collide")
        if path != IDENTITY and not path.endswith(".conf"):
            continue
        if path != IDENTITY:
            require(all(not part.startswith(".") for part in components) and components[0].casefold() != "secrets",
                    "unsupported incoming source path")
            sources.add(path)
        contents = git(stage, "cat-file", "blob", blob.decode())
        collected += len(contents)
        require(collected <= MAX_ROUTE_BYTES, "incoming SSH sources exceed size limit")
        file = view / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(contents)
    vault_changes = {name.decode() for name in changed if name.startswith(b"secrets/")}
    if vault_changes:
        from store_vault import receive_password_records
        require(vault_account is not None, "encrypted changes require a registered receive")
        def contents(blob):
            return git(stage, "cat-file", "blob", blob.decode())
        receive_password_records(vault_account, vault_changes, tree(stage,base,git), incoming_files, contents)
        for path in vault_changes:
            file = view/path
            file.parent.mkdir(exist_ok=True)
            file.write_bytes(contents(incoming_files[path]))
    require(changed <= {p.encode() for p in sources | vault_changes | {IDENTITY}}, "unsupported incoming file change")
    with absolute_directory(view) as view_fd:
        files = {}
        incoming, _ = index_rows(view_fd, store_id, files, MAX_ROUTE_BYTES)
        require(sources == {source["path"] for source in incoming["sources"]}, "unbound incoming SSH source")
    prepared, _ = managed_credentials(files, incoming["hosts"])
    with ssh_view(prepared, local) as config:
        for host in incoming["hosts"]:
            route = effective(config, host["name"])
            if requested and host["name"] in requested:
                for key, value in requested[host["name"]].items():
                    require(route[key] == value, "imported host differs from the reviewed OpenSSH route")
    previous = parse_json(git(stage, "show", base + ":" + IDENTITY))
    require(incoming == previous or incoming["generation"] > previous["generation"],
            "incoming identity generation did not advance")
    return incoming_files, [name.decode() for name in changed]


def fetch_from_fd(stage, directory_fd, ref, destination):
    uploader = shlex.join([sys.executable, "-I", "-B", "-S", str(Path(__file__).resolve()),
                           "upload", str(directory_fd)])
    git(stage, "fetch", "--no-tags", "--no-recurse-submodules", "--upload-pack=" + uploader,
        "--", "file:///unused", ref + ":" + destination, pass_fds=(directory_fd,))
    return oid(stage, destination + "^{commit}")


def fetch_direct(stage, target_fd, ref, destination):
    advertised = direct_ref(target_fd, ref)
    tip = fetch_from_fd(stage, target_fd, ref, destination)
    require(tip == advertised == direct_ref(target_fd, ref), "registered branch changed during fetch")
    return tip


def prepare_adoption(stage, original_index, before, base, tip):
    check_index(stage, original_index, before, git)
    (stage / "index").unlink()
    git(stage, "read-tree", tip)
    if tip != base:
        packs = stage / "receive-pack"
        packs.mkdir()
        git(stage, "pack-objects", "--revs", str(packs / "pack"), data=f"{tip}\n^{base}\n".encode())



def install_adoption(account, context, binding, store_fd, record, stage, original_index, before, after, changed, verify_binding):
    base, tip = record["base"], record["incoming"]
    with absolute_directory(context.checkout) as checkout_fd:
        with bound_directory(checkout_fd, (".git",), binding.git_dir, "Git directory changed") as git_fd:
            info = os.fstat(checkout_fd)
            require((info.st_dev, info.st_ino) == (binding.root.device, binding.root.inode),
                    "registered checkout changed")
            filemode, log_updates = plain_git(git_fd, bare=False)
            require(read_ref(git_fd, record["ref"]) == base and read_index(git_fd) == original_index,
                    "Git branch or index changed during receive")
            check_worktree(checkout_fd, before, filemode)
            verify_binding()
            if base != tip:
                with absolute_directory(account.recovery.path) as recovery:
                    info = os.fstat(recovery)
                    require((info.st_dev, info.st_ino) == (account.recovery.device, account.recovery.inode),
                            "receive recovery directory changed")
                    with prepare_receive_files(recovery, checkout_fd, stage, changed, before, record) as capture:
                        verify_binding()
                        publish_receive_marker(store_fd, record)
                        verify_binding(record)
                        fast_forward(checkout_fd, git_fd, record["ref"], base, tip, original_index,
                                     stage, before, after, lambda: verify_binding(record),
                                     filemode, log_updates, *capture)
                        os.unlink("receive.pending", dir_fd=store_fd)
                        sync_local(store_fd)


def receive(load_account, store_id, expected):
    with store_writer(load_account, store_id, expected) as (account, start, state, _lock):
        with receive_binding(account, store_id, expected) as (context, target, _, _store, binding):
            require(start == context, "store binding changed")
            local = local_inputs(state, store_id)
            with absolute_directory(context.git_dir) as git_fd:
                require(os.fstat(git_fd).st_ino == binding.git_dir.inode
                        and os.fstat(git_fd).st_dev == binding.git_dir.device, "Git directory changed")
                plain_git(git_fd, bare=False)
                base, original_index = read_ref(git_fd, context.ref), read_index(git_fd)
    with receive_binding(account, store_id, expected) as (start_again, observed, target_fd, _store, _binding):
        require((start_again, observed) == (context, target), "registered transport changed")
        plain_git(target_fd, bare=True)
        with tempfile.TemporaryDirectory(prefix="receive-", dir=account.state.path) as temp:
            stage = Path(temp)
            git(stage, "init", "--quiet", "--bare", "--template=")
            with absolute_directory(context.git_dir) as local_git:
                info = os.fstat(local_git)
                require((info.st_dev, info.st_ino) == (binding.git_dir.device, binding.git_dir.inode),
                        "Git directory changed")
                plain_git(local_git, bare=False)
                accepted = fetch_from_fd(stage, local_git, context.ref, "refs/hostbook/accepted")
                require(accepted == base, "local branch changed during receive")
            tip = fetch_direct(stage, target_fd, context.ref, "refs/hostbook/incoming")
            after, changed = verify_incoming(stage, tip, base, store_id, local, vault_account=account)
            before = tree(stage, base, git)
            prepare_adoption(stage, original_index, before, base, tip)
            with store_writer(load_account, store_id, expected) as (current, gated, state_fd, lock_fd):
                require(local_inputs(state_fd, store_id) == local, "registered SSH inputs changed")
                require(current == account and gated == context, "registered checkout changed")
                with receive_binding(current, store_id, expected) as (latest, bound, _, store_fd, active):
                    require((latest, bound, active) == (context, target, binding), "registered transport changed")
                    record = {"store_id": store_id, "target_id": context.target_id, "ref": context.ref,
                              "base": base, "incoming": tip, "connection_generation": expected.connection,
                              "checkout_generation": expected.checkout, "recovery_id": str(uuid4())}

                    def verify_binding(own_record=None):
                        require(load_account() == account and local_inputs(state_fd, store_id) == local,
                                "registered approval or SSH inputs changed")
                        with receive_binding(account, store_id, expected, own_record) as (actual, remote, _, namespace, registered):
                            require((actual, remote, registered) == (context, target, binding)
                                    and os.path.samestat(os.fstat(namespace), os.fstat(store_fd)),
                                    "registered binding changed during receive")
                        verify_state_directory(state_fd, path_parts(account.state.path, context.writer_lock),
                                               lock_fd, "store writer directory changed")

                    install_adoption(account, context, binding, store_fd, record, stage, original_index, before, after,
                                     changed, verify_binding)
    return {"store_id": store_id, "received": tip}


def receive_command(action, args):
    if action == "_store_receive" and not args:
        account, store_id, expected = fixture_request()
        def load():
            return account
    else:
        require(len(args) == 5, "store ID and four generations are required")
        store_id, expected = args[0], Generations(*(int(v) for v in args[1:]))
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
        def load():
            return load_approved_account(home)
    print(json.dumps(receive(load, store_id, expected)))
    return 0


if __name__ == "__main__":
    # Git appends its URL path. The approved repository is the inherited descriptor.
    if len(sys.argv) == 6 and sys.argv[1] == "verify-transaction":
        ref, old, new, phase = sys.argv[2:]
        if phase != "prepared":
            sys.exit(0)
        # receive-pack runs the prepared hook with the transaction's native ref locks held.
        try:
            rows = sys.stdin.buffer.read(16385).splitlines()
            expected = f"{old} {new} {ref}".encode()
            head = f"{old} {new} HEAD".encode()
            require(rows.count(expected) == 1 and all(row in (expected, head) for row in rows),
                    "unexpected native ref update")
            fd = os.open(".", DIRECTORY_FLAGS)
            try:
                require(direct_ref(fd, ref) == old, "registered remote ref is not direct")
                if head in rows:
                    require(read_file(fd, "HEAD", 4096) == b"ref: " + ref.encode() + b"\n",
                            "unexpected native HEAD update")
            finally:
                os.close(fd)
        except (OSError, StoreContextError):
            sys.exit(1)
        sys.exit(0)
    if len(sys.argv) == 4 and sys.argv[1] == "upload" and sys.argv[3] == "/unused":
        os.fchdir(int(sys.argv[2]))
        os.execve(GIT, [GIT, "upload-pack", "--strict", "."], SAFE_ENV)
    if len(sys.argv) == 4 and sys.argv[1] == "relay" and sys.argv[3] == "/unused":
        # Relay an already connected socket. This helper never opens a repository or transport.
        import socket
        from threading import Thread
        with socket.socket(fileno=int(sys.argv[2])) as peer:
            def forward():
                try:
                    while data := os.read(0, 65536):
                        peer.sendall(data)
                    peer.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
            Thread(target=forward, daemon=True).start()
            while data := peer.recv(65536):
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
        sys.exit(0)
    sys.exit(65)
