"""Read and fast-forward a regular checkout through its approved directory descriptors."""
from contextlib import ExitStack, contextmanager
import hashlib
import os
import re
import shutil
import stat
from uuid import uuid4

from store_context import JSON_LIMIT, open_directory, read_file, require, staged_local_file, sync_local

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


def check_contents(parent, name, blob):
    data = read_file(parent, name, JSON_LIMIT, unlinked)
    algorithm = "sha1" if len(blob) == 40 else "sha256"
    digest = hashlib.new(algorithm, b"blob " + str(len(data)).encode() + b"\0" + data)
    require(digest.hexdigest().encode() == blob, "registered checkout has local changes")


def check_worktree(checkout_fd, files):
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
            check_contents(fd, name, files[full])
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
    packs = stage / "objects/pack"
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


def install_sources(checkout_fd, stage, changed, before):
    for path in sorted(changed):
        parts = path.split("/")
        with make_parents(checkout_fd, parts[:-1]) as parent:
            name = parts[-1]
            data = (stage / "validated" / path).read_bytes()
            with staged_local_file(parent, name, data) as staged:
                fd = os.open(staged, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    if path in before:
                        check_contents(parent, name, before[path])
                        mode = os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode & 0o777
                        os.fchmod(fd, mode)
                    sync_local(fd)
                finally:
                    os.close(fd)
                if path in before:
                    os.replace(staged, name, src_dir_fd=parent, dst_dir_fd=parent)
                else:
                    os.link(staged, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                sync_local(parent)


def fast_forward(checkout_fd, git_fd, ref, base, tip, original_index, stage, changed,
                 before, after, verify_binding):
    candidate = (stage / "index").read_bytes()
    parts = ref.split("/")
    with make_parents(git_fd, parts[:-1]) as refs:
        with locked_git_file(git_fd, "index", candidate) as set_index:
            with locked_git_file(refs, parts[-1], (tip + "\n").encode()) as set_ref:
                verify_binding()
                require(read_ref(git_fd, ref) == base and read_index(git_fd) == original_index,
                        "Git branch or index changed during receive")
                check_worktree(checkout_fd, before)
                install_git_pack(git_fd, stage)
                verify_binding()
                install_sources(checkout_fd, stage, changed, before)
                verify_binding()
                set_index()
                set_ref()
    require(read_ref(git_fd, ref) == tip and read_index(git_fd) == candidate, "received Git state differs")
    check_worktree(checkout_fd, after)
    verify_binding()
