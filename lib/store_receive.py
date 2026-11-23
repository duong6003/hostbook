"""Receive a validated fast-forward from an approved local bare Git repository."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
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
    resolved_store, store_writer, sync_local, verify_path_isolation,
    verify_state_directory,
)
from store_read import MAX_ROUTE_BYTES, effective, local_inputs, managed_credentials, ssh_view

GIT = "/usr/bin/git"
SAFE_ENV = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1", "GIT_ALLOW_PROTOCOL": "file", "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
OID = re.compile(rb"(?:[a-f0-9]{40}|[a-f0-9]{64})")
IDENTITY = ".hostbook/identity.json"


def git(cwd, *args, data=b"", pass_fds=()):
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
    require(child.returncode == 0, "registered Git operation failed")
    return out


def oid(repo, ref):
    output = git(repo, "rev-parse", "--verify", ref).strip()
    require(OID.fullmatch(output), "expected a Git commit ID")
    return output.decode("ascii")


def plain_git(directory_fd, directory, *, bare):
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
    for item in config.rstrip(b"\0").split(b"\0"):
        key, _, value = item.partition(b"\n")
        if re.fullmatch(rb"(?:remote\.[a-zA-Z0-9_-]+\.(?:url|fetch)|branch\.[^\x00-\x20]+\.(?:remote|merge))", key):
            continue
        require(key in permitted and (permitted[key] is None or value in permitted[key]),
                "unsupported registered Git configuration")
        bare_setting |= key == b"core.bare"
    require(bare_setting, "missing registered Git configuration")
    for name in ("commondir", "shallow", "objects/info/alternates", "objects/info/http-alternates",
                 "info/grafts", "info/attributes", "info/sparse-checkout", "MERGE_HEAD", "REVERT_HEAD",
                 "CHERRY_PICK_HEAD", "rebase-merge", "rebase-apply", "sequencer"):
        require(not os.path.lexists(directory / name), "unsupported or unfinished Git operation")
    for base, folders, names in os.walk(directory, followlinks=False, onerror=raise_walk_error):
        for name in folders + names:
            info = os.lstat(Path(base) / name)
            require(stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1),
                    "unsupported linked Git resource")
            require(not name.endswith(".lock"), "registered Git repository is busy")


def raise_walk_error(exc):
    raise exc


@contextmanager
def receive_binding(account, store_id, expected):
    with resolved_store(account, store_id, expected) as (context, checkout, root_fd, state_fd):
        require(context.kind == "shared" and context.effective_mode in ("receive-only", "two-way")
                and context.transition_id is None, "a connected unfenced shared store is required")
        require(checkout.git_dir.path == context.checkout / ".git" and checkout.git_common_dir == checkout.git_dir,
                "receive requires a regular Git checkout")
        with bound_directory(root_fd, path_parts(account.root.path, account.recovery.path), account.recovery,
                             "invalid recovery root") as recovery_fd:
            for name in ("transactions", "control"):
                try:
                    with open_directory(recovery_fd, (name,)) as records:
                        require_private_directory(records, "recovery records")
                        require(not os.listdir(records), "existing recovery records require reconciliation")
                except FileNotFoundError:
                    pass
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
                yield context, target, target_fd, settings


@contextmanager
def absolute_directory(path):
    root = os.open("/", DIRECTORY_FLAGS)
    try:
        with open_directory(root, path.parts[1:]) as fd:
            yield fd
    finally:
        os.close(root)


def check_clean(context):
    repo = context.checkout
    with absolute_directory(repo) as checkout_fd:
        with open_directory(checkout_fd, (".git",)) as git_fd:
            plain_git(git_fd, context.git_dir, bare=False)
        index_rows(checkout_fd, context.store_id)
    require(git(repo, "symbolic-ref", "--quiet", "HEAD").rstrip(b"\n") == context.ref.encode(),
            "registered branch is not checked out")
    require(not git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored", "--ignore-submodules=all"),
            "registered checkout has local changes")
    entries = git(repo, "ls-files", "--stage", "-v", "-z").split(b"\0")[:-1]
    require(len(entries) <= 4096, "registered store has too many files")
    total = 0
    with absolute_directory(repo) as checkout_fd:
        for entry in entries:
            meta, path = entry.split(b"\t", 1)
            flag, mode, blob, stage = meta.split()
            require(flag == b"H" and mode == b"100644" and OID.fullmatch(blob) and stage == b"0",
                    "unsupported registered index entry")
            parts = path.decode().split("/")
            require(all(p and p not in (".", "..") and p.casefold() != ".git" for p in parts),
                    "invalid tracked file path")
            with open_directory(checkout_fd, parts[:-1]) as parent:
                def unlinked(_fd, info):
                    require(info.st_nlink == 1, "linked tracked file requires reconciliation")
                contents = read_file(parent, parts[-1], JSON_LIMIT, unlinked)
            total += len(contents)
            require(total <= 4 * MAX_ROUTE_BYTES, "registered store exceeds receive size limit")
            algo = "sha1" if len(blob) == 40 else "sha256"
            digest = hashlib.new(algo, b"blob " + str(len(contents)).encode() + b"\0" + contents)
            require(digest.hexdigest().encode() == blob, "registered checkout differs from its index")
    return oid(repo, "HEAD^{commit}")


def verify_incoming(stage, tip, base, store_id, local):
    git(stage, "merge-base", "--is-ancestor", base, tip)
    changed = set(git(stage, "diff", "--no-renames", "--name-only", "-z", base, tip).split(b"\0")[:-1])
    tree = git(stage, "ls-tree", "-rlz", "--full-tree", tip).split(b"\0")[:-1]
    require(len(tree) <= 4096, "incoming store has too many files")
    view = stage / "validated"
    view.mkdir()
    collected, sources, seen = 0, set(), {}
    for line in tree:
        meta, name = line.split(b"\t", 1)
        mode, kind, blob, size = meta.split()
        path = name.decode()
        components = path.split("/")
        require(mode == b"100644" and kind == b"blob" and OID.fullmatch(blob)
                and not re.search(r"[\x00-\x1f\x7f\\]", path)
                and all(part and part not in (".", "..") and part.casefold() not in
                        (".git", ".gitmodules", ".gitattributes") for part in components),
                "unsupported incoming Git tree")
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
        require(int(size) <= JSON_LIMIT, "incoming store file is too large")
        collected += int(size)
        require(collected <= MAX_ROUTE_BYTES, "incoming SSH sources exceed size limit")
        file = view / path
        file.parent.mkdir(parents=True, exist_ok=True)
        contents = git(stage, "cat-file", "blob", blob.decode())
        file.write_bytes(contents)
    require(changed <= {p.encode() for p in sources | {IDENTITY}}, "receive currently supports host-source changes only")
    with absolute_directory(view) as view_fd:
        files = {}
        incoming, _ = index_rows(view_fd, store_id, files, MAX_ROUTE_BYTES)
        require(sources == {source["path"] for source in incoming["sources"]}, "unbound incoming SSH source")
    prepared, _ = managed_credentials(files, incoming["hosts"])
    with ssh_view(prepared, local) as config:
        for host in incoming["hosts"]:
            effective(config, host["name"])
    previous = parse_json(git(stage, "show", base + ":" + IDENTITY))
    require(incoming == previous or incoming["generation"] > previous["generation"],
            "incoming identity generation did not advance")


def receive(load_account, store_id, expected):
    account = load_account()
    with store_writer(load_account, store_id, expected) as (locked, start, state, _lock):
        require(locked == account, "store approval changed")
        with receive_binding(account, store_id, expected) as (context, target, _remote, settings):
            require(start == context and "receive.pending" not in os.listdir(settings), "receive requires reconciliation")
            base = check_clean(context)
            local = local_inputs(state, store_id)
    with receive_binding(account, store_id, expected) as (start_again, observed, target_fd, _settings):
        require((start_again, observed) == (context, target), "registered transport changed")
        plain_git(target_fd, target.path, bare=True)
        with tempfile.TemporaryDirectory(prefix="receive-", dir=account.state.path) as temp:
            stage = Path(temp)
            git(stage, "init", "--quiet", "--bare", "--template=")
            # The file transport runs upload-pack from the verified open repository descriptor.
            uploader = shlex.join([sys.executable, "-I", "-B", "-S", str(Path(__file__).resolve()),
                                   "upload", str(target_fd)])
            git(stage, "fetch", "--no-tags", "--no-recurse-submodules", "--upload-pack=" + uploader,
                "--", "file:///unused", context.ref + ":refs/hostbook/incoming", pass_fds=(target_fd,))
            tip = oid(stage, "refs/hostbook/incoming^{commit}")
            verify_incoming(stage, tip, base, store_id, local)
            with store_writer(load_account, store_id, expected) as (current, gated, state_fd, lock_fd):
                require(local_inputs(state_fd, store_id) == local, "registered SSH inputs changed")
                require(current == account and gated == context, "registered checkout changed")
                with receive_binding(current, store_id, expected) as (latest, bound, _, settings):
                    require((latest, bound) == (context, target) and check_clean(latest) == base,
                            "store changed during receive")
                    verify_state_directory(state_fd, path_parts(account.state.path, context.writer_lock),
                                           lock_fd, "store writer directory changed")
                    require("receive.pending" not in os.listdir(settings), "receive requires reconciliation")
                    if base != tip:
                        git(context.checkout, "fetch", "--no-tags", "--no-recurse-submodules", "--no-write-fetch-head",
                            "--", str(stage), tip)
                        require(check_clean(context) == base, "checkout changed before local fast-forward")
                        fd = os.open("receive.pending", os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY,
                                     0o600, dir_fd=settings)
                        with os.fdopen(fd, "w") as marker:
                            json.dump({"store_id": store_id, "target_id": context.target_id, "ref": context.ref,
                                       "connection_generation": expected.connection, "base": base, "incoming": tip}, marker)
                            marker.flush()
                            sync_local(marker.fileno())
                        sync_local(settings)
                        git(context.checkout, "merge", "--ff-only", "--no-autostash", "--no-overwrite-ignore", tip)
                        require(check_clean(context) == tip, "received checkout requires reconciliation")
                        os.unlink("receive.pending", dir_fd=settings)
                        sync_local(settings)
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
    if len(sys.argv) == 4 and sys.argv[1] == "upload" and sys.argv[3] == "/unused":
        os.fchdir(int(sys.argv[2]))
        os.execve(GIT, [GIT, "upload-pack", "--strict", "."], SAFE_ENV)
    sys.exit(65)
