"""Read and fast-forward a regular checkout through its approved directory descriptors."""
from contextlib import ExitStack, contextmanager
import ctypes
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
from uuid import uuid4

from store_context import (JSON_LIMIT, StoreContextError, open_directory, read_file, require,
                           require_private_directory, sync_local)

OID = re.compile(rb"(?:[a-f0-9]{40}|[a-f0-9]{64})")
MAX_STORE_BYTES = 64 * 1024 * 1024
MAX_STORE_FILES = 4096


def count_file(total, size):
    require(0 <= size <= JSON_LIMIT and total + size <= MAX_STORE_BYTES, "registered store exceeds receive size limit")
    return total + size


def unlinked(_fd, info):
    require(info.st_nlink == 1, "linked tracked file requires reconciliation")


def read_ref(git_fd, full_ref):
    require(read_file(git_fd, "HEAD", 4096, unlinked) == b"ref: " + full_ref.encode() + b"\n",
            "registered branch is not checked out")
    return direct_ref(git_fd, full_ref)


def direct_ref(git_fd, full_ref):
    parts = full_ref.split("/")
    try:
        with open_directory(git_fd, parts[:-1]) as parent:
            value = read_file(parent, parts[-1], 128, unlinked).rstrip(b"\n")
    except FileNotFoundError:
        packed = read_file(git_fd, "packed-refs", JSON_LIMIT, unlinked)
        matches = [line.split(b" ", 1)[0] for line in packed.splitlines() if line.endswith(b" " + full_ref.encode())]
        require(len(matches) == 1, "registered branch is unavailable")
        value = matches[0]
    require(OID.fullmatch(value), "invalid registered branch revision")
    return value.decode("ascii")


def read_index(git_fd):
    return read_file(git_fd, "index", JSON_LIMIT, unlinked)


def tree(stage, ref, git):
    entries = git(stage, "ls-tree", "-rlz", "--full-tree", ref).split(b"\0")[:-1]
    require(len(entries) <= MAX_STORE_FILES, "registered store has too many files")
    files = {}
    total = 0
    for entry in entries:
        meta, raw_path = entry.split(b"\t", 1)
        mode, kind, blob, length = meta.split()
        path = raw_path.decode()
        require(mode == b"100644" and kind == b"blob" and OID.fullmatch(blob)
                and not re.search(r"[\x00-\x1f\x7f\\]", path)
                and all(p and p not in (".", "..") and p.casefold() not in (".git", ".gitattributes", ".gitmodules")
                        for p in path.split("/")), "unsupported Git tree")
        total = count_file(total, int(length))
        files[path] = blob
    return files


def check_index(stage, original, files, git):
    (stage / "index").write_bytes(original)
    listed = {}
    for entry in git(stage, "ls-files", "--stage", "-v", "-z").split(b"\0")[:-1]:
        meta, path = entry.split(b"\t", 1)
        flag, mode, blob, level = meta.split()
        require(flag == b"H" and mode == b"100644" and level == b"0", "unsupported registered index entry")
        listed[path.decode()] = blob
    require(listed == files, "registered checkout has staged changes")


def check_contents(parent, name, blob, filemode=True):
    def verify(fd, info):
        unlinked(fd, info)
        require(not filemode or not info.st_mode & stat.S_IXUSR, "registered checkout has a file mode change")
    data = read_file(parent, name, JSON_LIMIT, verify)
    algorithm = "sha1" if len(blob) == 40 else "sha256"
    digest = hashlib.new(algorithm, b"blob " + str(len(data)).encode() + b"\0" + data)
    require(digest.hexdigest().encode() == blob, "registered checkout has local changes")


def check_worktree(checkout_fd, files, filemode):
    seen = set()
    def fail(exc):
        raise exc
    for path, folders, names, fd in os.fwalk(".", dir_fd=checkout_fd, follow_symlinks=False, onerror=fail):
        if path == ".":
            folders.remove(".git")
        for folder in folders:
            require(stat.S_ISDIR(os.stat(folder, dir_fd=fd, follow_symlinks=False).st_mode),
                    "unsupported linked worktree directory")
        prefix = "" if path == "." else path[2:] + "/"
        for name in names:
            full = prefix + name
            require(full in files, "registered checkout has untracked files")
            check_contents(fd, name, files[full], filemode)
            seen.add(full)
    require(seen == set(files), "registered checkout has missing files")


@contextmanager
def make_parents(root_fd, parts):
    with ExitStack() as stack:
        parent = root_fd
        for name in parts:
            try:
                os.mkdir(name, 0o700, dir_fd=parent)
                sync_local(parent)
            except FileExistsError:
                pass
            parent = stack.enter_context(open_directory(parent, (name,)))
        yield parent


def install_git_pack(git_fd, stage):
    # Publish each complete immutable pack before its index, including across devices.
    packs = stage / "receive-pack"
    with make_parents(git_fd, ("objects", "pack")) as dest:
        for pack in sorted(packs.glob("pack-*.pack")):
            require(re.fullmatch(r"pack-[a-f0-9]{40,64}\.pack", pack.name), "invalid Git pack")
            for source in (pack, pack.with_suffix(".idx")):
                temp = "." + str(uuid4()) + ".tmp"
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dest)
                try:
                    with os.fdopen(fd, "wb") as out, source.open("rb") as data:
                        shutil.copyfileobj(data, out)
                        out.flush()
                        sync_local(out.fileno())
                    try:
                        os.link(temp, source.name, src_dir_fd=dest, dst_dir_fd=dest, follow_symlinks=False)
                    except FileExistsError:
                        existing = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dest)
                        with os.fdopen(existing, "rb") as installed, source.open("rb") as expected:
                            require(stat.S_ISREG(os.fstat(existing).st_mode), "invalid existing Git pack")
                            while True:
                                chunk = expected.read(1024 * 1024)
                                require(installed.read(len(chunk) or 1) == chunk, "existing Git pack differs")
                                if not chunk:
                                    break
                finally:
                    os.unlink(temp, dir_fd=dest)
                sync_local(dest)


@contextmanager
def locked_git_file(parent, name, contents):
    lock = name + ".lock"
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600, dir_fd=parent)
    with os.fdopen(fd, "wb") as stream:
        def owned():
            try:
                return os.path.samestat(os.fstat(fd), os.stat(lock, dir_fd=parent, follow_symlinks=False))
            except FileNotFoundError:
                return False

        def publish():
            require(owned(), "Git lock ownership changed")
            os.replace(lock, name, src_dir_fd=parent, dst_dir_fd=parent)
            sync_local(parent)

        try:
            stream.write(contents)
            stream.flush()
            sync_local(fd)
            yield publish
        finally:
            if owned():
                os.unlink(lock, dir_fd=parent)


def rename_guarded(source_fd, source, dest_fd, dest, *, exchange):
    # Both kernels implement an atomic swap. A new destination instead requires exclusive creation.
    libc = ctypes.CDLL(None, use_errno=True)
    args = (source_fd, os.fsencode(source), dest_fd, os.fsencode(dest))
    if sys.platform == "darwin":
        result = libc.renameatx_np(*args, 2 if exchange else 4)
    elif sys.platform.startswith("linux"):
        flags = 2 if exchange else 1
        if hasattr(libc, "renameat2"):
            result = libc.renameat2(*args, flags)
        else:
            number = {"x86_64": 316, "aarch64": 276}.get(os.uname().machine)
            require(number is not None, "atomic receive is unavailable on this platform")
            result = libc.syscall(number, *args, flags)
    else:
        raise StoreContextError("atomic receive is unavailable on this platform")
    if result != 0:
        err = ctypes.get_errno()
        raise OSError(err, "atomic receive rename failed")


def check_destinations(checkout_fd, changes, before, recovery_fd):
    device = os.fstat(recovery_fd).st_dev
    require(os.fstat(checkout_fd).st_dev == device, "receive recovery must share the checkout filesystem")
    for path in changes:
        parts = path.split("/")
        with ExitStack() as stack:
            parent = checkout_fd
            for part in parts[:-1]:
                try:
                    parent = stack.enter_context(open_directory(parent, (part,)))
                except FileNotFoundError:
                    break
                require(os.fstat(parent).st_dev == device, "receive recovery must share the source filesystem")
            else:
                if path not in before:
                    try:
                        os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    raise StoreContextError("incoming file collides with existing local data")


def durable_file(parent, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        sync_local(fd)


@contextmanager
def prepare_receive_files(recovery_fd, checkout_fd, stage, changes, before, record):
    check_destinations(checkout_fd, changes, before, recovery_fd)
    with make_parents(recovery_fd, ("receives",)) as receipts:
        require_private_directory(receipts, "receive recovery")
        ident = record["recovery_id"]
        os.mkdir(ident, 0o700, dir_fd=receipts)
        sync_local(receipts)
        with open_directory(receipts, (ident,)) as capture:
            paths = sorted(changes, key=lambda path: (path == ".hostbook/identity.json", path))
            durable_file(capture, "manifest.json", json.dumps(dict(record, paths=paths)).encode())
            for i, path in enumerate(paths):
                durable_file(capture, str(i), (stage / "validated" / path).read_bytes())
            for probe in (".a", ".b"):
                durable_file(capture, probe, b"")
            rename_guarded(capture, ".a", capture, ".b", exchange=True)
            for probe in (".a", ".b"):
                os.unlink(probe, dir_fd=capture)
            sync_local(capture)
            yield capture, paths


def install_sources(checkout_fd, capture, paths, before, filemode):
    for i, path in enumerate(paths):
        parts = path.split("/")
        with make_parents(checkout_fd, parts[:-1]) as parent:
            name, slot = parts[-1], str(i)
            if path in before:
                check_contents(parent, name, before[path], filemode)
                mode = os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode & 0o777
                fd = os.open(slot, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=capture)
                try:
                    os.fchmod(fd, mode)
                    sync_local(fd)
                finally:
                    os.close(fd)
                rename_guarded(capture, slot, parent, name, exchange=True)
                sync_local(capture)
                sync_local(parent)
                try:
                    check_contents(capture, slot, before[path], filemode)
                except (StoreContextError, OSError):
                    # Restore the actual displaced editor save; the private slot retains the other inode.
                    rename_guarded(capture, slot, parent, name, exchange=True)
                    sync_local(capture)
                    sync_local(parent)
                    raise
            else:
                rename_guarded(capture, slot, parent, name, exchange=False)
                sync_local(capture)
                sync_local(parent)


def append_reflog(git_fd, ref, base, tip, enabled):
    parts = ("logs", *ref.split("/"))
    with ExitStack() as stack:
        if enabled:
            parent = stack.enter_context(make_parents(git_fd, parts[:-1]))
        else:
            try:
                parent = stack.enter_context(open_directory(git_fd, parts[:-1]))
            except FileNotFoundError:
                return
        flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            fd = os.open(parts[-1], flags | (os.O_CREAT if enabled else 0), 0o600, dir_fd=parent)
        except FileNotFoundError:
            if enabled:
                raise
            return
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "invalid Git reflog")
            entry = f"{base} {tip} Hostbook <hostbook@localhost> {int(time.time())} +0000\treceive: fast-forward\n".encode()
            require(os.write(fd, entry) == len(entry), "Git reflog write was incomplete")
            sync_local(fd)
        finally:
            os.close(fd)
        sync_local(parent)


def fast_forward(checkout_fd, git_fd, ref, base, tip, original_index, stage,
                 before, after, verify_binding, filemode, log_updates, capture, paths):
    candidate = (stage / "index").read_bytes()
    parts = ref.split("/")
    with make_parents(git_fd, parts[:-1]) as refs:
        with locked_git_file(git_fd, "index", candidate) as set_index:
            with locked_git_file(refs, parts[-1], (tip + "\n").encode()) as set_ref:
                verify_binding()
                require(read_ref(git_fd, ref) == base and read_index(git_fd) == original_index,
                        "Git branch or index changed during receive")
                check_worktree(checkout_fd, before, filemode)
                install_git_pack(git_fd, stage)
                verify_binding()
                install_sources(checkout_fd, capture, paths, before, filemode)
                verify_binding()
                set_index()
                append_reflog(git_fd, ref, base, tip, log_updates)
                append_reflog(git_fd, "HEAD", base, tip, log_updates)
                set_ref()
    require(read_ref(git_fd, ref) == tip and read_index(git_fd) == candidate, "received Git state differs")
    check_worktree(checkout_fd, after, filemode)
    verify_binding()
