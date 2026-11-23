"""Read-only v1 store resolution for a caller with independent account evidence.

The caller supplies approved bindings from outside the registry. The resolver
checks those bindings before reading registration data. It does not enroll an
account, authorize writes, or open a vault or transport.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Optional
import re
import stat


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


def read_file(parent, name, limit):
    fd = os.open(name, FILE_FLAGS, dir_fd=parent)
    with os.fdopen(fd, "rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "expected a regular file")
        contents = stream.read(limit + 1)
    require(len(contents) <= limit, "record exceeds size limit")
    return contents


def read_json(parent, name):
    return json.loads(read_file(parent, name, JSON_LIMIT).decode("utf-8"),
                      object_pairs_hook=unique_object, parse_constant=nonfinite)


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


def registry_entries(state_fd, account):
    with open_directory(state_fd, ("stores", "v1")) as registry_fd:
        record = read_json(registry_fd, "registry.json")
    fields(record, "format version device_id recovery_root_id revision stores")
    require(record["format"] == "hostbook-registrations" and type(record["version"]) is int
            and record["version"] == 1 and positive(record["revision"])
            and isinstance(record["stores"], list), "invalid registry header")
    require(record["device_id"] == account.device_id and record["recovery_root_id"] == account.recovery_root_id,
            "registry account binding mismatch")
    entries, checkouts = {}, {}
    private_count = 0
    for entry in record["stores"]:
        validate_entry(entry)
        key = entry["store_id"]
        require(key not in entries, "duplicate store ID")
        private_count += entry["kind"] == "private"
        require(private_count <= 1, "duplicate private store")
        entries[key] = entry
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


def verify_checkout(root_fd, root, binding, selected):
    with bound_directory(root_fd, path_parts(root, binding.root.path), binding.root,
                         "checkout binding mismatch") as checkout_fd:
        try:
            git_dir = binding.root.path / ".git"
            info = os.stat(".git", dir_fd=checkout_fd, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode):
                line = read_file(checkout_fd, ".git", 4096).decode("utf-8").rstrip("\r\n")
                require(line.startswith("gitdir: "), "invalid Git binding")
                git_dir = git_path(binding.root.path, line[len("gitdir: "):])
            require(git_dir == binding.git_dir.path, "Git binding mismatch")
            with bound_directory(root_fd, path_parts(root, git_dir), binding.git_dir,
                                 "Git binding mismatch") as git_fd:
                try:
                    common = read_file(git_fd, "commondir", 4096).decode("utf-8").rstrip("\r\n")
                except FileNotFoundError:
                    common_dir = git_dir
                else:
                    common_dir = git_path(git_dir, common)
                require(common_dir == binding.git_common_dir.path, "Git common directory binding mismatch")
                with bound_directory(root_fd, path_parts(root, common_dir), binding.git_common_dir,
                                     "Git common directory binding mismatch"):
                    pass
        except OSError as exc:
            raise StoreContextError("Git binding unavailable") from exc
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


def verify_resource(root_fd, parts, expected_type):
    try:
        with open_directory(root_fd, parts[:-1]) as parent:
            entry = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    require(expected_type(entry.st_mode), "invalid store resource path")


def require_private_directory(fd, label):
    info = os.fstat(fd)
    require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
            f"{label} directory must be private and owned by this account")


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
                try:
                    marker = read_json(recovery_fd, "root.json")
                    fields(marker, "version device_id root_id")
                except (OSError, ValueError, RecursionError) as exc:
                    raise StoreContextError("recovery root record unavailable or malformed") from exc
                require(type(marker["version"]) is int and marker["version"] == 1 and
                        marker["device_id"] == account.device_id and marker["root_id"] == account.recovery_root_id,
                        "recovery root record does not match approved account")
            with bound_directory(root_fd, path_parts(root, account.state.path), account.state,
                                 "state binding mismatch") as state_fd:
                require_private_directory(state_fd, "state")
                verify_path_isolation(root_fd, root, [(account.state,), (account.recovery,)])
                yield root_fd, state_fd
    finally:
        os.close(fs_root)


def resolve_store(account, store_id, expected):
    """Return metadata and scoped resource names, without writing to any paths."""
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
    except StoreContextError:
        raise
    except (OSError, ValueError, RecursionError) as exc:
        raise StoreContextError("store metadata unavailable or malformed") from exc
    namespace = account.state.path / "stores" / "v1" / store_id
    connection = entry["connection"] or {}
    return StoreContext(
        store_id, entry["kind"], generations, binding.root.path, binding.git_dir.path, binding.git_common_dir.path,
        entry["effective_mode"], entry["transition_id"], connection.get("target_id"), connection.get("ref"),
        binding.root.path / "secrets", namespace / "vault-cache" / account.device_id,
        namespace / "index" / str(generations.checkout) / "index.tsv",
        namespace / "sync.lock", namespace / "connection" / str(generations.connection) / "status",
    )
