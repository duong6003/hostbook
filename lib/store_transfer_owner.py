"""Bind an opaque transfer owner to its native process incarnation and filesystem lease."""
from contextlib import contextmanager
import ctypes
import errno
import fcntl
import os
from pathlib import Path
import stat
import struct
import sys
from uuid import uuid4

from store_context import StoreContextError, require, require_approval_file, require_private_directory
from store_publish import boot_id


def native_start(pid):
    require(type(pid) is int and pid > 0, "invalid transfer owner PID")
    if sys.platform.startswith("linux"):
        try:
            data = Path(f"/proc/{pid}/stat").read_bytes()
        except FileNotFoundError:
            return None
        # Field 22 follows the parenthesized command, which may itself contain spaces or ')'.
        head, end, fields = data.rpartition(b") ")
        values = fields.split()
        require(end and head.partition(b" (")[0] == str(pid).encode() and len(values) >= 20
                and values[19].isdigit(), "cannot verify transfer owner start")
        return "ticks-" + values[19].decode()
    require(sys.platform == "darwin", "native transfer ownership is unavailable")
    # Apple's struct proc_bsdinfo has 12 uint32s, 48 command-name bytes, six
    # uint32s, then the two uint64 pbi_start_tvsec/pbi_start_tvusec fields.
    layout = struct.Struct("=12I48s6IQQ")
    data = ctypes.create_string_buffer(layout.size)
    lib = ctypes.CDLL("/usr/lib/libproc.dylib",use_errno=True)
    lib.proc_pidinfo.argtypes = [ctypes.c_int,ctypes.c_int,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_int]
    size = lib.proc_pidinfo(pid,3,0,data,layout.size)  # PROC_PIDTBSDINFO
    if size == 0 and ctypes.get_errno() in (errno.ESRCH,errno.ENOENT):
        return None
    require(size == layout.size, "cannot verify transfer owner start")
    row = layout.unpack(data.raw)
    require(row[3] == pid and row[-2] > 0 and row[-1] < 1000000, "invalid native transfer owner")
    return f"start-{row[-2]}-{row[-1]}"


_process_ids = {}


def new_owner():
    pid, boot = os.getpid(), boot_id()
    start = native_start(pid)
    require(start is not None, "cannot establish transfer owner start")
    label = _process_ids.setdefault((boot,pid,start),str(uuid4()))
    return {"owner_id":str(uuid4()),"process_id":pid,"process_start_id":label,"boot_id":boot}, start


def owner_alive(owner, start):
    return owner["boot_id"] == boot_id() and native_start(owner["process_id"]) == start


@contextmanager
def owner_lease(transaction, owner, evidence, *, recovery=False):
    require_private_directory(transaction,"transfer transaction")
    fd = os.open("owner.lease",os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW,dir_fd=transaction)
    try:
        info = os.fstat(fd)
        require_approval_file(fd,info)
        require(stat.S_ISREG(info.st_mode) and evidence["lease"] == {"device":info.st_dev,"inode":info.st_ino},
                "transfer owner lease binding changed")
        try:
            fcntl.flock(fd,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StoreContextError("transfer owner lease is busy") from None
        alive = owner_alive(owner,evidence["native_start"])
        require((alive and owner["process_id"] == os.getpid()) or (recovery and not alive),
                "transfer process ownership is unresolved")
        require(os.path.samestat(info,os.stat("owner.lease",dir_fd=transaction,follow_symlinks=False)),
                "transfer owner lease binding changed")
        yield fd
    finally:
        os.close(fd)
