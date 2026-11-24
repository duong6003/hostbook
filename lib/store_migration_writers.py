"""Inspect direct Linux filesystem permissions for a proposed legacy handoff."""
import errno
import json
import os
from pathlib import Path
import pwd
import stat
import struct
import sys

from store_checkout import MAX_STORE_FILES
from store_context import DIRECTORY_FLAGS, FILE_FLAGS, StoreContextError, open_directory, require
from store_migration_backup import checked_path
from store_receive import plain_git


def linux_acl(fd, name):
    try:
        raw = os.getxattr(fd,name)
    except OSError as exc:
        if exc.errno == errno.ENODATA:
            return ()
        raise
    require(4 <= len(raw) <= 65536 and raw[:4] == struct.pack("<I",2) and len(raw)%8 == 4,
            "unsupported native ACL")
    return tuple(struct.iter_unpack("<HHI",raw[4:]))


def access_bits(info,acl,uid,groups):
    if not acl:
        return (info.st_mode >> (6 if uid == info.st_uid else 3 if info.st_gid in groups else 0)) & 7
    owner,group,other,mask = {},{},{},None
    named_users,named_groups = {},{}
    for tag,rights,ident in acl:
        require(rights <= 7 and tag in (1,2,4,8,16,32),"unsupported native ACL entry")
        if tag == 16:
            require(mask is None,"ambiguous native ACL")
            mask = rights
        else:
            dest = {1:owner,2:named_users,4:group,8:named_groups,32:other}[tag]
            require(ident not in dest,"ambiguous native ACL")
            dest[ident] = rights
    unbound = 0xffffffff
    require(all(set(row) == {unbound} for row in (owner,group,other)) and
            (not (named_users or named_groups) or mask is not None),"incomplete native ACL")
    if uid == info.st_uid:
        return owner[unbound]
    if uid in named_users:
        return named_users[uid] & mask
    matching = [group[unbound]] if info.st_gid in groups else []
    matching.extend(rights for gid,rights in named_groups.items() if gid in groups)
    if not matching:
        return other[unbound]
    total = 0
    for rights in matching:
        total |= rights
    return total & (7 if mask is None else mask)


def mount_id(fd):
    for row in Path("/proc/self/fdinfo/"+str(fd)).read_bytes().splitlines():
        if row.startswith(b"mnt_id:"):
            ident = row.split(b":",1)[1].strip()
            require(ident.isdigit(),"unknown mount binding")
            return int(ident)
    raise StoreContextError("unknown mount binding")


def inspect_paths(checkout,state,uids):
    checked_path(checkout); checked_path(state)
    require(not checkout.is_relative_to(state) and not state.is_relative_to(checkout) and
            uids and len(set(uids)) == len(uids) and len(uids) <= 32 and
            all(type(uid) is int and 0 <= uid < 0xffffffff for uid in uids),"select independent resources and legacy UIDs")
    blockers = ["verify-delegated-service-authority","verify-legacy-writer-quiescence","verify-migration-intent-and-recovery"]
    def result(status,resource):
        return dict(filesystem_permissions=status,resource=resource,legacy_uids=uids,cutover_ready=False,blockers=blockers)
    if 0 in uids or os.geteuid() in uids:
        return result("access-remains","writer-identity")
    if sys.platform != "linux":
        return result("unknown","native-identity-and-acl-provider")
    def memberships():
        result = {}
        for uid in uids:
            entry = pwd.getpwuid(uid)
            result[uid] = frozenset(os.getgrouplist(entry.pw_name,entry.pw_gid))
        return result
    def observation():
        identities = memberships()
        evidence,seen = [],set()
        grant = None
        def check(fd,role,child=None):
            nonlocal grant
            current = os.fstat(fd)
            acl = linux_acl(fd,"system.posix_acl_access")
            inherited = linux_acl(fd,"system.posix_acl_default") if stat.S_ISDIR(current.st_mode) else ()
            mid = mount_id(fd)
            evidence.append((role,mid,current.st_dev,current.st_ino,current.st_mode,current.st_uid,current.st_gid,
                             current.st_ctime_ns,acl,inherited))
            for uid,groups in identities.items():
                bits = access_bits(current,acl,uid,groups)
                if child is not None:
                    writable = bits & 3 == 3 and (not current.st_mode & stat.S_ISVTX or uid == child.st_uid)
                else:
                    writable = bool(bits & 2) or inherited and access_bits(current,inherited,uid,groups) & 2
                if uid == current.st_uid or writable:
                    grant = grant or role
            return mid
        def walk(fd,role,mount):
            info = os.fstat(fd)
            key = (info.st_dev,info.st_ino)
            require(key not in seen and len(seen) < MAX_STORE_FILES,"overlapping or oversized migration resources")
            seen.add(key)
            require(check(fd,role) == mount,"nested mount requires separate evidence")
            if stat.S_ISDIR(info.st_mode):
                for name in sorted(os.listdir(fd)):
                    child = os.stat(name,dir_fd=fd,follow_symlinks=False)
                    require(stat.S_ISDIR(child.st_mode) or stat.S_ISREG(child.st_mode) and child.st_nlink == 1,
                            "linked or special candidate resource")
                    opened = os.open(name,FILE_FLAGS,dir_fd=fd)
                    try:
                        require(os.path.samestat(child,os.fstat(opened)),"candidate entry changed")
                        walk(opened,"git" if role == "checkout" and name == ".git" else role,mount)
                    finally:
                        os.close(opened)
        for role,path in (("checkout",checkout),("state",state)):
            fd = os.open("/",DIRECTORY_FLAGS)
            try:
                for component in path.parts[1:]:
                    sub = os.open(component,DIRECTORY_FLAGS,dir_fd=fd)
                    try:
                        check(fd,role+"-ancestor",os.fstat(sub))
                    except BaseException:
                        os.close(sub)
                        raise
                    os.close(fd)
                    fd = sub
                if role == "checkout":
                    with open_directory(fd,(".git",)) as repo:
                        plain_git(repo,bare=False)
                walk(fd,role,mount_id(fd))
            finally:
                os.close(fd)
        return grant,evidence,identities
    try:
        first = observation()
        require(observation() == first,"writer access evidence changed")
    except (KeyError,OSError,StoreContextError,RecursionError):
        return result("unknown","resource-or-principal-binding")
    return result("access-remains",first[0]) if first[0] else result("checked","checkout-git-state-and-ancestors")


def writer_command(args):
    require(len(args) >= 3,"provide proposed checkout, state and distinct legacy UIDs")
    print(json.dumps(inspect_paths(Path(args[0]),Path(args[1]),[int(value) for value in args[2:]])))
    return 0
