"""Read-only v1 store resolution for a caller with independent account evidence.

The caller supplies approved bindings from outside the registry. The resolver
checks those bindings before reading registration data. It does not enroll an
account, authorize writes, or open a vault or transport.
"""
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
from typing import Optional
import re
import stat
import sys
from uuid import uuid4


class StoreContextError(ValueError):
    pass


@dataclass(frozen=True)
class DirectoryBinding:
    path: Path
    device: int
    inode: int


@dataclass(frozen=True)
class CheckoutBinding:
    store_id: str
    checkout_id: str
    root: DirectoryBinding
    git_dir: DirectoryBinding
    git_common_dir: DirectoryBinding


@dataclass(frozen=True)
class Account:
    root: DirectoryBinding
    state: DirectoryBinding
    recovery: DirectoryBinding
    device_id: str
    recovery_root_id: str
    checkouts: dict[str, CheckoutBinding]


@dataclass(frozen=True)
class Generations:
    registration: int
    checkout: int
    connection: int
    policy: int


@dataclass(frozen=True)
class StoreContext:
    store_id: str
    kind: str
    generations: Generations
    checkout: Path
    git_dir: Path
    git_common_dir: Path
    effective_mode: Optional[str]
    transition_id: Optional[str]
    target_id: Optional[str]
    ref: Optional[str]
    vault: Path
    vault_cache_namespace: Path
    index: Path
    writer_lock: Path
    status: Path


STORE_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
JSON_LIMIT = 4 * 1024 * 1024
APPROVAL_FILE = "store-account-v1.json"


def require(condition, reason):
    if not condition:
        raise StoreContextError(reason)


def valid_id(value):
    return isinstance(value, str) and STORE_ID.fullmatch(value) is not None


def positive(value):
    return type(value) is int and value > 0


def fields(value, names):
    require(isinstance(value, dict) and value.keys() == set(names.split()), "invalid record fields")


def unique_object(pairs):
    result = dict(pairs)
    require(len(result) == len(pairs), "duplicate JSON key")
    return result


def nonfinite(_value):
    raise StoreContextError("invalid JSON number")


def path_parts(root, path):
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise StoreContextError("path outside verified account") from exc
    require(relative.parts and ".." not in relative.parts, "invalid account path")
    return relative.parts


@contextmanager
def open_directory(parent, parts, ancestors=None):
    fd = os.dup(parent)
    try:
        for part in parts:
            if ancestors is not None:
                info = os.fstat(fd)
                ancestors.add((info.st_dev, info.st_ino))
            next_fd = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


@contextmanager
def bound_directory(parent, parts, binding, reason, ancestors=None):
    try:
        with open_directory(parent, parts, ancestors) as fd:
            info = os.fstat(fd)
            require((info.st_dev, info.st_ino) == (binding.device, binding.inode), reason)
            yield fd
    except OSError as exc:
        raise StoreContextError(reason) from exc


def read_file(parent, name, limit, verify=None):
    fd = os.open(name, FILE_FLAGS, dir_fd=parent)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode), "expected a regular file")
        if verify is not None:
            verify(stream.fileno(), info)
        contents = stream.read(limit + 1)
    require(len(contents) <= limit, "record exceeds size limit")
    return contents


def read_json(parent, name):
    return parse_json(read_file(parent, name, JSON_LIMIT))


def parse_json(contents):
    return json.loads(contents.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=nonfinite)


def valid_ref(ref):
    if not isinstance(ref, str) or not ref.startswith("refs/heads/"):
        return False
    return (not ref.endswith(".") and ".." not in ref and "@{" not in ref and
            re.search(r"[\x00-\x20\x7f~^:?*\[\\]", ref) is None and all(
                part and not part.startswith(".") and not part.endswith(".lock") for part in ref.split("/")))


def entry_generations(entry):
    return Generations(*(entry[key + "_generation"] for key in
                         ("registration", "checkout", "connection", "policy")))


def validate_entry(entry):
    fields(entry, "store_id kind registration_generation checkout_generation connection_generation "
                  "policy_generation checkout connection effective_mode transition_id")
    require(valid_id(entry["store_id"]) and entry["kind"] in ("private", "shared"), "invalid store entry")
    generation = entry_generations(entry)
    require(all(positive(value) for value in (generation.registration, generation.checkout,
                                             generation.connection, generation.policy)), "invalid generations")
    require(entry["transition_id"] is None or valid_id(entry["transition_id"]), "invalid transition")
    checkout, connection = entry["checkout"], entry["connection"]
    if checkout is None:
        require(entry["kind"] == "shared" and connection is None, "missing checkout")
    else:
        fields(checkout, "checkout_id root binding_id")
        require(valid_id(checkout["checkout_id"]) and valid_id(checkout["binding_id"]) and
                isinstance(checkout["root"], str), "invalid checkout")
    if connection is None:
        require(entry["effective_mode"] is None, "invalid disconnected mode")
    else:
        fields(connection, "target_id ref")
        require(entry["kind"] == "shared" and checkout is not None and
                entry["effective_mode"] in ("receive-only", "two-way") and
                valid_id(connection["target_id"]) and valid_ref(connection["ref"]), "invalid connection")


def verify_path_isolation(root_fd, root, groups):
    claimed_ids, claimed_ancestors = set(), set()
    for group in groups:
        identities, ancestors = set(), set()
        for binding in set(group):
            with bound_directory(root_fd, path_parts(root, binding.path), binding,
                                 "directory binding mismatch", ancestors):
                identities.add((binding.device, binding.inode))
        require(not (identities & (claimed_ids | claimed_ancestors) or ancestors & claimed_ids),
                "overlapping store directories")
        claimed_ids.update(identities)
        claimed_ancestors.update(ancestors)


def read_registry(state_fd):
    with open_directory(state_fd, ("stores", "v1")) as registry_fd:
        record = read_json(registry_fd, "registry.json")
    fields(record, "format version device_id recovery_root_id revision stores")
    require(record["format"] == "hostbook-registrations" and type(record["version"]) is int
            and record["version"] == 1 and positive(record["revision"])
            and isinstance(record["stores"], list), "invalid registry header")
    require(valid_id(record["device_id"]) and valid_id(record["recovery_root_id"]), "invalid registry account IDs")
    entries = {}
    private_count = 0
    for entry in record["stores"]:
        validate_entry(entry)
        key = entry["store_id"]
        require(key not in entries, "duplicate store ID")
        private_count += entry["kind"] == "private"
        require(private_count <= 1, "duplicate private store")
        entries[key] = entry
    return record, entries


def registry_entries(state_fd, account):
    record, entries = read_registry(state_fd)
    require(record["device_id"] == account.device_id and record["recovery_root_id"] == account.recovery_root_id,
            "registry account binding mismatch")
    checkouts = {}
    for key, entry in entries.items():
        settings = entry["checkout"]
        if settings is None:
            continue
        binding = account.checkouts.get(settings["binding_id"])
        require(binding is not None and binding.store_id == key and
                binding.checkout_id == settings["checkout_id"] and
                str(binding.root.path) == settings["root"], "checkout binding mismatch")
        checkouts[key] = binding
    return entries, checkouts


def git_path(directory, text):
    require(text and not any(ord(char) < 32 or ord(char) == 127 for char in text), "invalid Git binding path")
    path = Path(text)
    if path.is_absolute():
        require(".." not in path.parts, "invalid Git binding path")
        return path
    parts = list(path.parts)
    while parts and parts[0] == "..":
        directory = directory.parent
        parts.pop(0)
    # The traversed suffix must remain subject to the no-symlink directory check.
    require(".." not in parts, "invalid Git binding path")
    return directory.joinpath(*parts)


def directory_binding(path, fd):
    info = os.fstat(fd)
    return DirectoryBinding(path, info.st_dev, info.st_ino)


def git_bindings(root_fd, root, checkout_fd, checkout, approved=None):
    git_dir = checkout / ".git"
    try:
        info = os.stat(".git", dir_fd=checkout_fd, follow_symlinks=False)
        if stat.S_ISREG(info.st_mode):
            line = read_file(checkout_fd, ".git", 4096).decode("utf-8").rstrip("\r\n")
            require(line.startswith("gitdir: "), "invalid Git binding")
            git_dir = git_path(checkout, line[len("gitdir: "):])
        if approved is not None:
            require(git_dir == approved.git_dir.path, "Git binding mismatch")
        with open_directory(root_fd, path_parts(root, git_dir)) as git_fd:
            git = directory_binding(git_dir, git_fd)
            if approved is not None:
                require(git == approved.git_dir, "Git binding mismatch")
            try:
                common = read_file(git_fd, "commondir", 4096).decode("utf-8").rstrip("\r\n")
            except FileNotFoundError:
                return git, git
            common_dir = git_path(git_dir, common)
            if approved is not None:
                require(common_dir == approved.git_common_dir.path, "Git common directory binding mismatch")
            with open_directory(root_fd, path_parts(root, common_dir)) as common_fd:
                return git, directory_binding(common_dir, common_fd)
    except OSError as exc:
        raise StoreContextError("Git binding unavailable") from exc


def verify_checkout(root_fd, root, binding, selected):
    with bound_directory(root_fd, path_parts(root, binding.root.path), binding.root,
                         "checkout binding mismatch") as checkout_fd:
        _, common = git_bindings(root_fd, root, checkout_fd, binding.root.path, binding)
        require(common == binding.git_common_dir, "Git common directory binding mismatch")
        if selected:
            verify_sidecar(checkout_fd, binding.store_id)
            verify_resource(checkout_fd, ("secrets",), stat.S_ISDIR)


def verify_sidecar(checkout_fd, expected_store_id):
    with open_directory(checkout_fd, (".hostbook",)) as identity_fd:
        identity = read_json(identity_fd, "identity.json")
    fields(identity, "format version generation store_id sources hosts")
    require(identity["format"] == "hostbook-store" and type(identity["version"]) is int
            and identity["version"] == 1 and positive(identity["generation"]) and
            isinstance(identity["sources"], list) and isinstance(identity["hosts"], list), "invalid sidecar header")
    require(identity["store_id"] == expected_store_id, "sidecar store ID mismatch")
    return identity


def verify_resource(root_fd, parts, expected_type):
    try:
        with open_directory(root_fd, parts[:-1]) as parent:
            entry = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    require(expected_type(entry.st_mode), "invalid store resource path")


def require_no_acl_grants(fd):
    if sys.platform != "darwin":
        return
    # Darwin extended ACL grants can add access outside the POSIX mode bits.
    import ctypes

    lib = ctypes.CDLL(None, use_errno=True)
    lib.acl_get_fd.argtypes = [ctypes.c_int]
    lib.acl_get_fd.restype = ctypes.c_void_p
    lib.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    lib.acl_get_tag_type.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    lib.acl_free.argtypes = [ctypes.c_void_p]
    acl = lib.acl_get_fd(fd)
    if not acl:
        require(ctypes.get_errno() == errno.ENOENT, "cannot verify approval path ACL")
        return
    try:
        entry, tag = ctypes.c_void_p(), ctypes.c_int()
        entry_id = 0  # ACL_FIRST_ENTRY
        while lib.acl_get_entry(acl, entry_id, ctypes.byref(entry)) == 0:
            require(lib.acl_get_tag_type(entry, ctypes.byref(tag)) == 0, "cannot verify approval path ACL")
            require(tag.value == 2, "ACL grants on local store approval path")  # ACL_EXTENDED_DENY
            entry_id = -1  # ACL_NEXT_ENTRY
        require(ctypes.get_errno() == errno.EINVAL, "cannot verify approval path ACL")
    finally:
        lib.acl_free(acl)


def require_private_directory(fd, label):
    info = os.fstat(fd)
    require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
            f"{label} directory must be private and owned by this account")
    require_no_acl_grants(fd)


def verify_recovery_marker(recovery_fd, device_id, recovery_root_id):
    try:
        marker = read_json(recovery_fd, "root.json")
        fields(marker, "version device_id root_id")
    except (OSError, ValueError, RecursionError) as exc:
        raise StoreContextError("recovery root record unavailable or malformed") from exc
    require(type(marker["version"]) is int and marker["version"] == 1 and
            marker["device_id"] == device_id and marker["root_id"] == recovery_root_id,
            "recovery root record does not match approved account")


@contextmanager
def verified_account(account):
    """Recheck approved directory evidence and the journal root before registry access."""
    root = account.root.path
    require(root.is_absolute() and root.anchor == "/" and root.parts[1:] and
            ".." not in root.parts, "invalid verified account root")
    require(valid_id(account.device_id) and valid_id(account.recovery_root_id), "invalid approved account IDs")
    fs_root = os.open("/", DIRECTORY_FLAGS)
    try:
        with bound_directory(fs_root, root.parts[1:], account.root, "account binding mismatch") as root_fd:
            require_private_directory(root_fd, "account")
            with bound_directory(root_fd, path_parts(root, account.recovery.path), account.recovery,
                                 "recovery binding mismatch") as recovery_fd:
                require_private_directory(recovery_fd, "recovery")
                verify_recovery_marker(recovery_fd, account.device_id, account.recovery_root_id)
            with bound_directory(root_fd, path_parts(root, account.state.path), account.state,
                                 "state binding mismatch") as state_fd:
                require_private_directory(state_fd, "state")
                verify_path_isolation(root_fd, root, [(account.state,), (account.recovery,)])
                yield root_fd, state_fd
    finally:
        os.close(fs_root)


@contextmanager
def resolved_store(account, store_id, expected):
    """Hold the verified directory descriptors while a scoped operation uses them."""""
    require(valid_id(store_id), "invalid store ID")
    require(isinstance(expected, Generations) and all(positive(value) for value in vars(expected).values()),
            "invalid expected generations")
    try:
        with verified_account(account) as (root_fd, state_fd):
            entries, bindings = registry_entries(state_fd, account)
            require(store_id in entries, "unknown store")
            entry = entries[store_id]
            generations = entry_generations(entry)
            require(generations == expected, "stale store generations")
            require(store_id in bindings, "store has no checkout")
            root = account.root.path
            for key, binding in bindings.items():
                verify_checkout(root_fd, root, binding, key == store_id)
            groups = [(account.state,), (account.recovery,)]
            groups.extend((binding.root, binding.git_dir, binding.git_common_dir)
                          for binding in bindings.values())
            verify_path_isolation(root_fd, root, groups)
            binding = bindings[store_id]
            prefix = ("stores", "v1", store_id)
            resources = (
                (("vault-cache", account.device_id), stat.S_ISDIR),
                (("index", str(generations.checkout), "index.tsv"), stat.S_ISREG),
                (("sync.lock",), stat.S_ISDIR),
                (("connection", str(generations.connection), "status"), stat.S_ISREG),
            )
            for parts, expected_type in resources:
                verify_resource(state_fd, prefix + parts, expected_type)
            namespace = account.state.path / "stores" / "v1" / store_id
            connection = entry["connection"] or {}
            context = StoreContext(
                store_id, entry["kind"], generations, binding.root.path, binding.git_dir.path, binding.git_common_dir.path,
                entry["effective_mode"], entry["transition_id"], connection.get("target_id"), connection.get("ref"),
                binding.root.path / "secrets", namespace / "vault-cache" / account.device_id,
                namespace / "index" / str(generations.checkout) / "index.tsv",
                namespace / "sync.lock", namespace / "connection" / str(generations.connection) / "status",
            )

            yield context, binding, root_fd, state_fd
    except StoreContextError:
        raise
    except (OSError, ValueError, RecursionError) as exc:
        raise StoreContextError("store metadata unavailable or malformed") from exc


def resolve_store(account, store_id, expected):
    """Return metadata and scoped resource names, without writing to any paths."""
    with resolved_store(account, store_id, expected) as (context, _, _, _):
        return context


def require_unlinked_index_source(_fd, info):
    require(info.st_nlink == 1, "linked index source is not allowed")


INDEX_NAME = re.compile(r"[A-Za-z0-9._-]+")
INDEX_HOST_LINE = re.compile(r"^[ \t]*host(?:[ \t]+=?[ \t]*|=[ \t]*)(.*)$", re.IGNORECASE | re.ASCII)


def index_host_tokens(contents, path):
    ordinal = 0
    # shortcut: reject quoted or escaped Host lists until the scoped SSH resolver supports them.
    for line in contents.decode("utf-8").split("\n"):
        match = INDEX_HOST_LINE.match(line.rstrip("\r"))
        if not match:
            continue
        ordinal += 1
        for index, token in enumerate(re.split(r"[ \t]+", match[1].strip(" \t")), 1):
            if token.startswith("#"):
                break
            require(not any(char in token for char in ("'", '"', "\\")), "unsupported index Host token")
            if INDEX_NAME.fullmatch(token):
                yield (path, ordinal, index), token.lower()


def index_rows(checkout_fd, store_id, source_bytes=None, max_source_bytes=None):
    identity = verify_sidecar(checkout_fd, store_id)
    paths, occurrences = set(), {}
    total_bytes = 0
    for source in identity["sources"]:
        fields(source, "path sha256")
        source_path, digest = source["path"], source["sha256"]
        require(isinstance(source_path, str) and isinstance(digest, str)
                and re.fullmatch(r"[0-9a-f]{64}", digest), "invalid index source")
        parts = tuple(source_path.split("/"))
        require(all(part and not part.startswith(".") for part in parts)
                and parts[0].casefold() != "secrets" and parts[-1].endswith(".conf")
                and not re.search(r"[\x00-\x1f\x7f\\]", source_path)
                and source_path.casefold() not in paths, "invalid or duplicate index source path")
        paths.add(source_path.casefold())
        with open_directory(checkout_fd, parts[:-1]) as source_fd:
            contents = read_file(source_fd, parts[-1], JSON_LIMIT, require_unlinked_index_source)
        total_bytes += len(contents)
        require(max_source_bytes is None or total_bytes <= max_source_bytes, "SSH sources exceed size limit")
        require(hashlib.sha256(contents).hexdigest() == digest, "index source bytes changed since identity review")
        occurrences.update(index_host_tokens(contents, source_path))
        if source_bytes is not None:
            source_bytes[source_path] = contents

    ids, names = set(), set()
    rows = []
    for host in identity["hosts"]:
        fields(host, "host_id name bindings")
        key, name, bindings = host["host_id"], host["name"], host["bindings"]
        require(valid_id(key) and key not in ids and isinstance(name, str)
                and INDEX_NAME.fullmatch(name) and name.lower() not in names,
                "invalid or duplicate index host")
        require(isinstance(bindings, list) and bindings, "missing index host bindings")
        for binding in bindings:
            fields(binding, "path host_ordinal token_ordinal")
            path, header, token = (binding[field] for field in ("path", "host_ordinal", "token_ordinal"))
            require(isinstance(path, str) and positive(header) and positive(token)
                    and occurrences.pop((path, header, token), None) == name.lower(),
                    "invalid or duplicate index host binding")
        ids.add(key)
        names.add(name.lower())
        rows.append(f"{store_id}\t{key}\t{name}\n")
    require(not occurrences, "index sources contain unbound host names")
    return identity, "".join(rows)


@contextmanager
def store_state_directory(state_fd, parts):
    # stores/v1 already belongs to the verified registry. Create only scoped state.
    with ExitStack() as directories:
        fd = state_fd
        for part in parts[:2]:
            fd = directories.enter_context(open_directory(fd, (part,)))
            require_owned_config_directory(fd)
        for part in parts[2:]:
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            fd = directories.enter_context(open_directory(fd, (part,)))
            require_private_directory(fd, "store state")
        yield fd


def acquire_local_lock(fd, reason):
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise StoreContextError(reason) from exc


@contextmanager
def staged_local_file(directory_fd, name, contents):
    pending = f".{name}.{uuid4()}.tmp"
    fd = os.open(pending, os.O_WRONLY | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(contents)
        yield pending
    finally:
        try:
            os.unlink(pending, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def verify_state_directory(state_fd, parts, expected_fd, reason):
    with open_directory(state_fd, parts) as current_fd:
        require_private_directory(current_fd, "store state")
        current, expected = os.fstat(current_fd), os.fstat(expected_fd)
        require((current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino), reason)


def refresh_store_index(load_account, store_id, expected):
    """Rebuild this store's alias inventory on explicit request."""
    account = load_account()
    with resolved_store(account, store_id, expected) as (context, binding, root_fd, state_fd):
        parts = path_parts(account.state.path, context.index.parent)
        checkout_parts = path_parts(account.root.path, binding.root.path)
        with store_state_directory(state_fd, parts) as index_fd:
            acquire_local_lock(index_fd, "store index is already being refreshed")
            with bound_directory(root_fd, checkout_parts, binding.root, "checkout binding mismatch") as checkout_fd:
                snapshot, rows = index_rows(checkout_fd, store_id)
            with staged_local_file(index_fd, context.index.name, rows.encode("utf-8")) as pending:
                require(load_account() == account, "store approval changed during index refresh")
                with resolved_store(account, store_id, expected) as (current, actual, current_root, current_state):
                    require(current == context and actual == binding, "store binding changed during index refresh")
                    with bound_directory(current_root, checkout_parts, binding.root,
                                         "checkout binding mismatch") as checkout_fd:
                        require(index_rows(checkout_fd, store_id)[0] == snapshot, "index inputs changed during refresh")
                    verify_state_directory(current_state, parts, index_fd, "index directory changed")
                    os.replace(pending, context.index.name, src_dir_fd=index_fd, dst_dir_fd=index_fd)
    return rows


@contextmanager
def store_writer(load_account, store_id, expected):
    """Serialize local effects for one store. The lock supplies no write authority."""
    account = load_account()
    with resolved_store(account, store_id, expected) as (context, _, _, state_fd):
        lock_parts = path_parts(account.state.path, context.writer_lock)
        with store_state_directory(state_fd, lock_parts) as lock_fd:
            # Keep the directory inode in place. Closing the descriptor releases the lease.
            acquire_local_lock(lock_fd, "store writer is busy")
            yield account, context, state_fd, lock_fd


def refresh_store_status(load_account, store_id, expected):
    """Record the current local binding without claiming a synchronization result."""
    with store_writer(load_account, store_id, expected) as (account, context, state_fd, lock_fd):
        status = {
            "store_id": store_id, "connection_generation": expected.connection,
            "effective_mode": context.effective_mode, "state": "unchecked",
        }
        status_parts = path_parts(account.state.path, context.status.parent)
        lock_parts = path_parts(account.state.path, context.writer_lock)
        with store_state_directory(state_fd, status_parts) as status_fd:
            contents = (json.dumps(status) + "\n").encode("utf-8")
            with staged_local_file(status_fd, context.status.name, contents) as pending:
                require(load_account() == account, "store approval changed during status refresh")
                with resolved_store(account, store_id, expected) as (current, _, _, current_state):
                    require(current == context, "store binding changed during status refresh")
                    verify_state_directory(current_state, lock_parts, lock_fd, "store writer directory changed")
                    verify_state_directory(current_state, status_parts, status_fd, "store status directory changed")
                    os.replace(pending, context.status.name, src_dir_fd=status_fd, dst_dir_fd=status_fd)
    return status


def parse_directory_binding(value):
    fields(value, "path device inode")
    require(isinstance(value["path"], str) and type(value["device"]) is int
            and value["device"] >= 0 and positive(value["inode"]), "invalid directory binding")
    return DirectoryBinding(Path(value["path"]), value["device"], value["inode"])


def parse_account(value):
    fields(value, "root state recovery device_id recovery_root_id checkouts")
    root = parse_directory_binding(value["root"])
    checkouts = value["checkouts"]
    require(isinstance(checkouts, dict), "invalid approved checkout bindings")
    bindings = {}
    for binding_id, binding in checkouts.items():
        fields(binding, "store_id checkout_id root git_dir git_common_dir")
        require(valid_id(binding_id) and valid_id(binding["store_id"]) and valid_id(binding["checkout_id"]),
                "invalid approved checkout IDs")
        bindings[binding_id] = CheckoutBinding(
            binding["store_id"], binding["checkout_id"], parse_directory_binding(binding["root"]),
            parse_directory_binding(binding["git_dir"]), parse_directory_binding(binding["git_common_dir"]),
        )
    return Account(root, parse_directory_binding(value["state"]), parse_directory_binding(value["recovery"]),
                   value["device_id"], value["recovery_root_id"], bindings)


def require_owned_config_directory(fd):
    info = os.fstat(fd)
    require(info.st_uid == os.geteuid() and info.st_mode & 0o022 == 0,
            "approval directory must be owned by this account and protected from other writers")
    require_no_acl_grants(fd)
    return info


def require_approval_file(fd, info):
    require(info.st_uid == os.geteuid() and info.st_nlink == 1 and info.st_mode & 0o077 == 0,
            "approval file must be private and owned by this account")
    require_no_acl_grants(fd)


@contextmanager
def approval_directory(home):
    require(home.is_absolute() and home.anchor == "/" and home.parts[1:] and ".." not in home.parts,
            "invalid approval home path")
    fs_root = os.open("/", DIRECTORY_FLAGS)
    try:
        with open_directory(fs_root, home.parts[1:]) as home_fd:
            require_owned_config_directory(home_fd)
            with open_directory(home_fd, (".config",)) as config_fd:
                require_owned_config_directory(config_fd)
                with open_directory(config_fd, ("hostbook",)) as approval_fd:
                    require_owned_config_directory(approval_fd)
                    yield (
                        fs_root, directory_binding(home, home_fd), approval_fd,
                        directory_binding(home / ".config/hostbook", approval_fd),
                    )
    finally:
        os.close(fs_root)


def verify_approval_placement(fs_root, approval, account):
    paths = [account.state, account.recovery]
    for binding in account.checkouts.values():
        paths.extend((binding.root, binding.git_dir, binding.git_common_dir))
    for path in paths:
        path_parts(account.root.path, path.path)
    verify_path_isolation(fs_root, Path("/"), [(approval,), paths])


def load_approved_account(home):
    """Read independent approval from the effective account's verified home."""
    try:
        with approval_directory(home) as (fs_root, home_binding, approval_fd, approval):
            approved = parse_json(read_file(approval_fd, APPROVAL_FILE, JSON_LIMIT, require_approval_file))
            fields(approved, "format version home account")
            require(approved["format"] == "hostbook-store-account"
                    and type(approved["version"]) is int and approved["version"] == 1,
                    "invalid local store approval version")
            require(parse_directory_binding(approved["home"]) == home_binding, "approval home binding mismatch")
            account = parse_account(approved["account"])
            verify_approval_placement(fs_root, approval, account)
            return account
    except StoreContextError:
        raise
    except (OSError, ValueError, RecursionError) as exc:
        raise StoreContextError("local store approval unavailable or malformed") from exc


def capture_prepared_account(fs_root, root):
    require(root.is_absolute() and root.anchor == "/" and root.parts[1:] and ".." not in root.parts,
            "invalid prepared account root")
    with open_directory(fs_root, root.parts[1:]) as root_fd:
        require_private_directory(root_fd, "account")
        with open_directory(root_fd, ("state",)) as state_fd:
            require_private_directory(state_fd, "state")
            record, entries = read_registry(state_fd)
            state = directory_binding(root / "state", state_fd)
        with open_directory(root_fd, ("recovery",)) as recovery_fd:
            require_private_directory(recovery_fd, "recovery")
            verify_recovery_marker(recovery_fd, record["device_id"], record["recovery_root_id"])
            recovery = directory_binding(root / "recovery", recovery_fd)

        bindings, stores = {}, []
        groups = [(state,), (recovery,)]
        for store_id, entry in entries.items():
            require(entry["effective_mode"] in (None, "receive-only") and entry["transition_id"] is None,
                    "first approval requires receive-only shared stores and no registration transition")
            settings = entry["checkout"]
            if settings is None:
                continue
            require(settings["binding_id"] not in bindings, "duplicate checkout binding")
            checkout = Path(settings["root"])
            require(str(checkout) == settings["root"], "invalid checkout path")
            with open_directory(root_fd, path_parts(root, checkout)) as checkout_fd:
                checked = directory_binding(checkout, checkout_fd)
                git, common = git_bindings(root_fd, root, checkout_fd, checkout)
                verify_sidecar(checkout_fd, store_id)
                verify_resource(checkout_fd, ("secrets",), stat.S_ISDIR)
            bindings[settings["binding_id"]] = CheckoutBinding(store_id, settings["checkout_id"],
                                                              checked, git, common)
            groups.append((checked, git, common))
            stores.append({"store_id": store_id, "checkout": str(checkout), "git_common_dir": str(common.path),
                           "effective_mode": entry["effective_mode"], "generations": asdict(entry_generations(entry))})
        require(stores, "prepared account has no stores with checkouts")
        verify_path_isolation(root_fd, root, groups)
        account = Account(directory_binding(root, root_fd), state, recovery, record["device_id"],
                          record["recovery_root_id"], bindings)
    return account, record, stores


def approval_plan(fs_root, home_binding, approval, root):
    account, record, stores = capture_prepared_account(fs_root, root)
    verify_approval_placement(fs_root, approval, account)
    document = {"format": "hostbook-store-account", "version": 1, "home": asdict(home_binding),
                "account": asdict(account)}
    evidence = json.dumps([document, asdict(approval), record], sort_keys=True, default=os.fspath)
    confirmation = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
    contents = json.dumps(document, sort_keys=True, default=os.fspath).encode("utf-8")
    return {"root": str(root), "stores": stores, "confirmation": confirmation}, contents


def plan_account_approval(home, root):
    with approval_directory(home) as (fs_root, home_binding, _, approval):
        plan, _ = approval_plan(fs_root, home_binding, approval, root)
    return plan


def sync_local(fd):
    if sys.platform == "darwin":
        operation = getattr(fcntl, "F_FULLFSYNC", None)
        if operation is None:
            raise OSError(errno.ENOTSUP, "F_FULLFSYNC is unavailable")
        fcntl.fcntl(fd, operation)
    else:
        os.fsync(fd)


def publish_approval(approval_fd, contents):
    require(len(contents) <= JSON_LIMIT, "approval exceeds size limit")
    sync_local(approval_fd)
    pending = f".{APPROVAL_FILE}.{uuid4()}.pending"
    fd = os.open(pending, os.O_WRONLY | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=approval_fd)
    published = False
    try:
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(contents)
                stream.flush()
                require_approval_file(stream.fileno(), os.fstat(stream.fileno()))
                sync_local(stream.fileno())
            try:
                os.link(pending, APPROVAL_FILE, src_dir_fd=approval_fd, dst_dir_fd=approval_fd, follow_symlinks=False)
            except FileExistsError as exc:
                raise StoreContextError("local store approval already exists") from exc
            published = True
        finally:
            os.unlink(pending, dir_fd=approval_fd)
        sync_local(approval_fd)
    except OSError as exc:
        if published:
            raise StoreContextError("local approval outcome uncertain after publication") from exc
        raise


def approve_account(home, root, confirmation):
    with approval_directory(home) as (fs_root, home_binding, approval_fd, approval):
        plan, contents = approval_plan(fs_root, home_binding, approval, root)
        require(plan["confirmation"] == confirmation, "approval confirmation no longer matches the prepared account")
        publish_approval(approval_fd, contents)
    try:
        account = load_approved_account(home)
        require(account == parse_account(parse_json(contents)["account"]), "published approval changed")
        for store in plan["stores"]:
            resolve_store(account, store["store_id"], Generations(**store["generations"]))
    except (StoreContextError, OSError) as exc:
        raise StoreContextError("local approval published but verification must be reconciled") from exc
    return {"stores": plan["stores"]}


def fixture_request(extra=None):
    require(stat.S_ISFIFO(os.fstat(sys.stdin.fileno()).st_mode), "a fixture request pipe is required")
    contents = sys.stdin.buffer.read(JSON_LIMIT + 1)
    require(len(contents) <= JSON_LIMIT, "fixture request exceeds size limit")
    request = parse_json(contents)
    fields(request, "account store_id generations" + (" " + extra if extra else ""))
    fields(request["generations"], "registration checkout connection policy")
    account = parse_account(request["account"])
    root = account.root.path
    require(root.parent == Path("/tmp").resolve() and root.name.startswith("hostbook-context-"),
            "fixture account must be in a Hostbook scratch directory")
    bound = (account, request["store_id"], Generations(**request["generations"]))
    return (*bound, request[extra]) if extra else bound


def check_legacy_destinations(account, paths):
    """Deny a legacy path or filesystem identity claimed by an independently approved registration."""
    claimed = [account.state, account.recovery]
    for binding in account.checkouts.values():
        claimed.extend((binding.root, binding.git_dir, binding.git_common_dir))
    names = {item.path for item in claimed}
    identities = {(item.device, item.inode) for item in claimed}
    for argument in paths:
        if not argument:
            continue
        path = Path(argument).absolute()
        actual = path.resolve(strict=False)
        for location in (path, actual):
            require(not any(location == name or name in location.parents or location in name.parents for name in names),
                    "legacy destination overlaps registered store data")
        for parent in (actual,*actual.parents):
            try:
                info = parent.stat()
            except FileNotFoundError:
                continue
            require((info.st_dev, info.st_ino) not in identities and
                    (parent != actual or not stat.S_ISREG(info.st_mode) or info.st_nlink == 1),
                    "legacy destination aliases registered resources")
    checkout = Path(paths[0])
    if (checkout / ".git").exists():
        from store_receive import git_result
        rc, dirs = git_result(checkout, "rev-parse", "--absolute-git-dir", "--git-common-dir")
        require(rc == 0, "legacy Git binding is unavailable")
        for item in dirs.splitlines():
            git_dir = (checkout / os.fsdecode(item)).resolve(strict=True)
            require(all(git_dir != name and name not in git_dir.parents for name in names),
                    "legacy Git directory belongs to a registered store")
            info = git_dir.stat()
            require((info.st_dev,info.st_ino) not in identities, "legacy Git directory was registered")


def legacy_write_host(account, directory, project, block):
    """Write an unrelated legacy config through a verified parent directory descriptor."""
    from store_checkout import rename_guarded
    from store_receive import absolute_directory
    require(project and "/" not in project and not any(ord(c)<32 for c in project), "invalid legacy project")
    root = Path(directory).resolve(strict=True)
    name = project + ".conf"
    target = root / name
    check_legacy_destinations(account, (directory, str(target)))
    input_fd = os.open(block, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(input_fd,"rb") as source:
        require(stat.S_ISREG(os.fstat(input_fd).st_mode), "expected a legacy Host block file")
        data = source.read(JSON_LIMIT + 1)
    require(len(data) <= JSON_LIMIT, "legacy input exceeds size limit")
    with absolute_directory(root) as parent:
        def verify_parent():
            require(os.path.samestat(root.stat(),os.fstat(parent)), "legacy destination moved")
            check_legacy_destinations(account, (directory, str(target)))
        verify_parent()
        try:
            prior = read_file(parent, name, JSON_LIMIT, require_unlinked_index_source)
            mode = os.stat(name,dir_fd=parent,follow_symlinks=False).st_mode & 0o777
        except FileNotFoundError:
            prior, mode = None, 0o600
        lines = (prior or b"").split(b"\n")
        wildcard = next((index for index,line in enumerate(lines)
                         if re.match(rb"^[ \t]*host[ \t].*[*?]",line,re.I)), None)
        if wildcard is None:
            updated = (prior + b"\n" if prior else b"") + data
        else:
            if lines[-1] == b"":
                lines.pop()
            inserted = data if not data or data.endswith(b"\n") else data+b"\n"
            updated = b"".join(line+b"\n" for line in lines[:wildcard]) + inserted+b"\n"
            updated += b"".join(line+b"\n" for line in lines[wildcard:])
        require(len(updated) <= JSON_LIMIT, "legacy project exceeds size limit")
        staged = ".hostbook-write-" + str(uuid4())
        fd = os.open(staged,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
        keep = False
        try:
            with os.fdopen(fd,"wb") as stream:
                stream.write(updated)
                stream.flush()
                os.fchmod(stream.fileno(),mode)
                sync_local(stream.fileno())
            verify_parent()
            rename_guarded(parent,staged,parent,name,exchange=prior is not None)
            if prior is not None:
                keep = True
                try:
                    displaced = read_file(parent,staged,JSON_LIMIT,require_unlinked_index_source)
                    require(displaced == prior,"legacy file changed during save")
                except (OSError,StoreContextError):
                    rename_guarded(parent,staged,parent,name,exchange=True)
                    raise
            sync_local(parent)
            verify_parent()
            keep = False
        finally:
            if not keep:
                try:
                    os.unlink(staged,dir_fd=parent)
                except FileNotFoundError:
                    pass


def main(args):
    try:
        if args and args[0] == "_registered_selection":
            require(len(args) == 2, "one checkout directory is required")
            account = load_approved_account(Path(pwd.getpwuid(os.geteuid()).pw_dir))
            selection = Path(args[1]).resolve(strict=True)
            info = selection.stat()
            require(any(binding.root.path == selection and
                        (binding.root.device,binding.root.inode) == (info.st_dev,info.st_ino)
                        for binding in account.checkouts.values()), "selection is not the approved checkout")
            return 0
        if args and args[0] == "_legacy_merge_install":
            require(len(args)==3 and re.fullmatch(r"\.hostbook-merge\.[a-zA-Z0-9]+",args[1])
                    and args[2] not in ("",".","..") and "/" not in args[2], "invalid merge output")
            home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
            account = load_approved_account(home)
            parent = os.open(".",DIRECTORY_FLAGS)
            try:
                cwd = Path.cwd()
                check_legacy_destinations(account, (str(cwd),str(cwd/args[1]),str(cwd/args[2])))
                source = os.stat(args[1],dir_fd=parent,follow_symlinks=False)
                target = os.stat(args[2],dir_fd=parent,follow_symlinks=False)
                require(all(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 for info in (source,target))
                        and source.st_uid == os.geteuid() and source.st_mode & 0o077 == 0,"merge output changed")
                os.replace(args[1],args[2],src_dir_fd=parent,dst_dir_fd=parent)
                sync_local(parent)
            finally:
                os.close(parent)
            return 0
        if args and args[0] == "_legacy_write_host":
            require(len(args) == 4, "legacy directory, project and input are required")
            home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
            legacy_write_host(load_approved_account(home), *args[1:])
            return 0
        if args and args[0] == "_legacy_destinations":
            require(len(args) >= 2, "actual legacy destination required")
            home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
            check_legacy_destinations(load_approved_account(home), args[1:])
            return 0
        if args and args[0] == "_approve_store_account":
            require(len(args) in (2, 3), "prepared account root and optional confirmation are required")
            home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
            root = Path(args[1])
            result = (plan_account_approval(home, root) if len(args) == 2
                      else approve_account(home, root, args[2]))
            print(json.dumps(result))
            return 0
        action = args[0] if args else None
        if action in ("_store_add", "store-add", "_store_import", "store-import"):
            from store_private import private_command
            return private_command(action, args[1:])
        if action == "store-conflict":
            from store_conflict import conflict_command
            return conflict_command(args[1:])
        if action == "store-migration-writers":
            from store_migration_writers import writer_command
            return writer_command(args[1:])
        if action in ("store-migration-backup","store-migration-restore"):
            from store_migration_backup import backup_command
            return backup_command(args[1:],reopen=action == "store-migration-restore")
        if action == "store-migration-inspect":
            from store_migration import inspection_command
            return inspection_command(args[1:])
        if action in ("store-transfer","store-transfer-recover"):
            from store_transfer_command import transfer_command
            return transfer_command(args[1:],recovery=action == "store-transfer-recover")
        if action == "store-transfer-preview":
            from store_transfer import preview_command
            return preview_command(args[1:])
        if action in ("_store_mode", "store-mode"):
            from store_mode import mode_command
            return mode_command(action, args[1:])
        if action in ("_store_settle", "store-settle"):
            from store_publish import settle_command
            return settle_command(action, args[1:])
        if action in ("_store_edit_address", "store-edit-address"):
            from store_publish import edit_command
            return edit_command(action, args[1:])
        if action in ("_store_receive", "store-receive"):
            from store_receive import receive_command
            return receive_command(action, args[1:])
        if action == "_store_overview":
            from store_overview import overview_command
            return overview_command(args[1:])
        if action == "_store_home_inventory":
            from store_overview import home_inventory
            require(len(args) == 1, "expected no inventory arguments")
            return home_inventory()
        if action == "_store_home_host":
            from store_overview import overview_command
            require(len(args) == 3, "expected a host selection")
            return overview_command([args[1]], host_token=args[2])
        if action in ("_store_hosts", "_store_preview", "_store_connect", "store-hosts", "store-preview", "store-connect"):
            from store_read import route_command
            return route_command(action, args[1:])
        refresh = action in ("_refresh_store_index", "_refresh_store_status")
        if refresh or action == "_store_context":
            args = args[1:]
        if refresh or args:
            require(len(args) == 5, "store ID and four generations are required")
            expected = Generations(*(int(value) for value in args[1:]))
            home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
            if action == "_refresh_store_index":
                sys.stdout.write(refresh_store_index(lambda: load_approved_account(home), args[0], expected))
                return 0
            if action == "_refresh_store_status":
                print(json.dumps(refresh_store_status(lambda: load_approved_account(home), args[0], expected)))
                return 0
            account, store_id = load_approved_account(home), args[0]
        else:
            account, store_id, expected = fixture_request()
        context = resolve_store(account, store_id, expected)
        print(json.dumps({"store_id": context.store_id, "checkout": str(context.checkout),
                          "generations": asdict(context.generations)}))
        return 0
    except StoreContextError as error:
        print(error, file=sys.stderr)
    except (OSError, ValueError, KeyError, RecursionError):
        print("invalid store context request", file=sys.stderr)
    return 65


if __name__ == "__main__":
    # Isolated Python mode excludes the script directory from module lookup.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.modules["store_context"] = sys.modules[__name__]
    sys.exit(main(sys.argv[1:]))
