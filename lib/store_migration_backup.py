"""Seal a bounded legacy recovery set and reopen it into a new private directory."""
from contextlib import contextmanager
import base64
import ctypes
import errno
import os
from pathlib import Path
import stat
import sys
from uuid import uuid4

from store_checkout import MAX_STORE_FILES, durable_file, rename_guarded
from store_context import (FILE_FLAGS, JSON_LIMIT, fields, open_directory, parse_json, require,
                           read_file, require_private_directory, sync_local, valid_id)
from store_login import ToolScope, verified_age_tools
from store_migration import inspect_legacy, vault_condition
from store_receive import absolute_directory, git, plain_git
from store_transfer_credentials import CredentialRecord, key_binding, private_workspace, verify_private_key
from store_transfer_intent import wire
from store_transfer_recovery import capture, read_payload, seal_payload, verify_backup
from store_vault import VaultUnlockCache, file_evidence

ROLES = ("checkout","state","settings","ssh")
LEGACY_STATE = frozenset(("askpass","check","current","deps","flash","history","idx","idx2","index.tsv",
                          "last-check.tsv","last-import","lastmode","skipped","status","sync.lock.gate","tmp"))


def encoded(value):
    return base64.b64encode(value).decode("ascii")


def native_metadata(fd):
    info = os.fstat(fd)
    require(not getattr(info,"st_flags",0) and not info.st_mode & 0o7000,"special file flags require review")
    result = dict(uid=info.st_uid,gid=info.st_gid,mode=stat.S_IMODE(info.st_mode))
    if sys.platform == "linux":
        names = os.listxattr(fd)
        require(all(name.startswith("user.") or name in ("system.posix_acl_access","system.posix_acl_default")
                    for name in names),"unsupported extended file metadata")
        result["attributes"] = {name:encoded(os.getxattr(fd,name)) for name in names}
    elif sys.platform == "darwin":
        lib = ctypes.CDLL(None,use_errno=True)
        length = lib.flistxattr(fd,None,0,0)
        require(0 <= length <= 65536,"unreadable extended attributes")
        names = ctypes.create_string_buffer(length)
        require(lib.flistxattr(fd,names,length,0) == length,"extended attributes changed")
        result["attributes"] = {}
        for name in names.raw.split(b"\0")[:-1]:
            size = lib.fgetxattr(fd,name,None,0,0,0)
            require(0 <= size <= 65536,"unsupported extended attribute")
            data = ctypes.create_string_buffer(size)
            require(lib.fgetxattr(fd,name,data,size,0,0) == size,"extended attribute changed")
            result["attributes"][os.fsdecode(name)] = encoded(data.raw)
        lib.acl_get_fd.restype = ctypes.c_void_p
        acl = lib.acl_get_fd(fd)
        if acl:
            try:
                lib.acl_to_text.argtypes = [ctypes.c_void_p,ctypes.POINTER(ctypes.c_ssize_t)]
                lib.acl_to_text.restype = ctypes.c_void_p
                size = ctypes.c_ssize_t()
                text = lib.acl_to_text(acl,ctypes.byref(size))
                require(text and 0 <= size.value <= 65536,"unreadable filesystem ACL")
                try:
                    result["acl"] = encoded(ctypes.string_at(text,size.value))
                finally:
                    lib.acl_free(ctypes.c_void_p(text))
            finally:
                lib.acl_free(ctypes.c_void_p(acl))
        else:
            require(ctypes.get_errno() == errno.ENOENT,"cannot inventory filesystem ACL")
            result["acl"] = None
    else:
        require(False,"unsupported filesystem metadata platform")
    return result


def apply_metadata(fd, wanted):
    current = os.fstat(fd)
    if (current.st_uid,current.st_gid) != (wanted["uid"],wanted["gid"]):
        os.fchown(fd,wanted["uid"],wanted["gid"])
    os.fchmod(fd,wanted["mode"])
    if sys.platform == "linux":
        for key,value in wanted["attributes"].items():
            os.setxattr(fd,key,base64.b64decode(value,validate=True))
    elif sys.platform == "darwin":
        lib = ctypes.CDLL(None,use_errno=True)
        existing = native_metadata(fd)["attributes"]
        for key,value in wanted["attributes"].items():
            if existing.get(key) != value:
                data = base64.b64decode(value,validate=True)
                require(lib.fsetxattr(fd,os.fsencode(key),data,len(data),0,0) == 0,"cannot restore extended attribute")
    if sys.platform == "darwin" and wanted["acl"] is not None:
        lib = ctypes.CDLL(None,use_errno=True)
        lib.acl_from_text.restype = ctypes.c_void_p
        acl = lib.acl_from_text(base64.b64decode(wanted["acl"],validate=True))
        require(acl,"unreadable recovery ACL")
        try:
            require(lib.acl_set_fd(fd,ctypes.c_void_p(acl)) == 0,"unable to restore ACL")
        finally:
            lib.acl_free(ctypes.c_void_p(acl))
    require(native_metadata(fd) == wanted,"restored permissions or ownership differ")


def checked_path(path):
    require(isinstance(path,Path) and path.anchor == "/" and len(path.parts)>1 and ".." not in path.parts,
            "select an absolute migration path")
    return path


def safe_name(name):
    return name and name not in (".","..") and "/" not in name and all(ord(ch) >= 32 and ord(ch) != 127 for ch in name)


def snapshot(paths, root_fd=None):
    require(set(paths) == set(ROLES),"select the legacy checkout, state, settings and SSH configuration")
    locations = [checked_path(paths[name]) for name in ROLES]
    require(all(i == j or not a.is_relative_to(b) for i,a in enumerate(locations) for j,b in enumerate(locations)),
            "legacy inputs overlap")
    records,stamps,seen = {},{},set()
    total = 0
    def visit(parent,name,parts):
        nonlocal total
        require(len(parts) <= 48 and len(records) < MAX_STORE_FILES and safe_name(name),"unsupported backup entry")
        named = os.stat(name,dir_fd=parent,follow_symlinks=False)
        require(stat.S_ISDIR(named.st_mode) or stat.S_ISREG(named.st_mode),"legacy link or special input requires review")
        fd = os.open(name,FILE_FLAGS,dir_fd=parent)
        try:
            info = os.fstat(fd)
            require(os.path.samestat(info,named),"legacy input changed")
            folder = stat.S_ISDIR(info.st_mode)
            require(folder or stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                    "non-regular or multiply linked legacy data requires review")
            if len(parts) == 1:
                require(folder == (parts[0] != "ssh"),"select an SSH config file and separate checkout, state and settings directories")
            ident = info.st_dev,info.st_ino
            require(ident not in seen,"legacy paths alias one another")
            seen.add(ident)
            key = "/".join(parts)
            stamps[key] = file_evidence(info)
            data = records[key] = dict(directory=folder,metadata=native_metadata(fd))
            total += len(wire(data))+len(key)
            require(total <= JSON_LIMIT,"legacy metadata exceeds supported backup size")
            if folder:
                names = sorted(os.listdir(fd))
                if parts[0] == "state":
                    if len(parts) == 1:
                        require(set(names) <= LEGACY_STATE,"review unknown, unlocked or active legacy state before backup")
                    if len(parts) == 2 and parts[1] == "tmp":
                        require(not names,"quiesce and clear active legacy scratch before backup")
                if parts == ("settings",):
                    require(set(names) <= {"config","themes"},"review unknown legacy settings before backup")
                for child in names:
                    visit(fd,child,(*parts,child))
            else:
                total += info.st_size
                require(total <= JSON_LIMIT and info.st_size <= JSON_LIMIT,"legacy recovery set exceeds supported size")
                with os.fdopen(os.dup(fd),"rb") as stream:
                    value = stream.read(info.st_size+1)
                require(len(value) == info.st_size,"legacy file changed while copying")
                data["data"] = encoded(value)
            require(file_evidence(os.fstat(fd)) == stamps[key] and
                    os.path.samestat(info,os.stat(name,dir_fd=parent,follow_symlinks=False)),"legacy entry changed")
        finally:
            os.close(fd)
    for role,path in zip(ROLES,locations):
        if root_fd is None:
            with absolute_directory(path.parent) as parent:
                visit(parent,path.name,(role,))
        else:
            visit(root_fd,role,(role,))
    return records,stamps


@contextmanager
def recovery_dir(path):
    with absolute_directory(checked_path(path)) as root:
        require_private_directory(root,"migration recovery")
        def current():
            require_private_directory(root,"migration recovery")
            with absolute_directory(path) as named:
                require(os.path.samestat(os.fstat(named),os.fstat(root)),"migration recovery path changed")
        yield root,current


def scope(*stamps):
    return ToolScope(frozenset(row[:2] for row in stamps))


def write_stage_bytes(fd,data):
    with os.fdopen(os.dup(fd),"wb") as stream:
        stream.write(data)
        stream.flush()


def materialize(stage,records,created=None):
    if created is None:
        created = {}
    require(isinstance(records,dict) and len(records) <= MAX_STORE_FILES and all(role in records for role in ROLES),
            "incomplete legacy recovery manifest")
    ordered = []
    for key,row in sorted(records.items(),key=lambda pair: (pair[0].count("/"),pair[0])):
        parts = key.split("/")
        require(len(parts) <= 48 and parts[0] in ROLES and all(safe_name(p) for p in parts)
                and (len(parts)==1 or records.get("/".join(parts[:-1]),{}).get("directory") is True),
                "invalid recovery manifest path")
        folder = row.get("directory")
        require(type(folder) is bool,"invalid recovery entry")
        fields(row,"directory metadata" if folder else "directory metadata data")
        with open_directory(stage,parts[:-1]) as parent:
            if folder:
                os.mkdir(parts[-1],0o700,dir_fd=parent)
                info = os.stat(parts[-1],dir_fd=parent,follow_symlinks=False)
                created[key] = (info.st_dev,info.st_ino,True)
            else:
                fd = os.open(parts[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
                try:
                    info = os.fstat(fd)
                    created[key] = (info.st_dev,info.st_ino,False)
                    write_stage_bytes(fd,base64.b64decode(row["data"],validate=True))
                    created[key] = (info.st_dev,info.st_ino,True)
                    sync_local(fd)
                finally:
                    os.close(fd)
        ordered.append((parts,row))
    for parts,row in reversed(ordered):
        with open_directory(stage,parts[:-1]) as parent:
            fd = os.open(parts[-1],FILE_FLAGS,dir_fd=parent)
            try:
                apply_metadata(fd,row["metadata"])
                sync_local(fd)
            finally:
                os.close(fd)
    sync_local(stage)


def restored_snapshot(root_fd):
    return snapshot({role:Path("/")/role for role in ROLES},root_fd)[0]


def verify_history(checkout, *, restored=False):
    if ".git" not in os.listdir(checkout):
        return
    with open_directory(checkout,(".git",)) as repo:
        plain_git(repo,bare=False)
        if restored:
            git(repo,"--git-dir=.","fsck","--full","--strict","--cache","--no-dangling")
            index = git(repo,"--git-dir=.","ls-files","--stage","-z")
            require(not any(row.startswith(b"160000 ") for row in index.split(b"\0")),
                    "external Git submodule requires separate recovery")


def prove_vault(records,authority,access):
    prefix = "checkout/secrets/"
    files = {path[len(prefix):]:base64.b64decode(value["data"],validate=True)
             for path,value in records.items() if path.startswith(prefix) and not value["directory"]}
    if not files:
        require(access is None,"vault selection changed")
        return
    require(vault_condition(files) == "locked-unverified" and access and len(access) == 2 and all(access),
            "both original vault credentials are required")
    tools = verified_age_tools(authority)
    vault = VaultUnlockCache(tools[0])
    recipient = files["vault.pub"].rstrip(b"\n")+b"\n"
    for envelope,phrase in zip(("vault.key.age","vault.recovery.age"),access):
        identity = vault.open_identity(files[envelope],phrase,recipient)
    for name,metadata in files.items():
        if name.endswith(".meta"):
            base = name[:-5]
            record = CredentialRecord(metadata,files[base+".age"],files.get(base+".pub"))
            key = key_binding(record)
            plaintext = vault.open_secret(identity,record.ciphertext)
            require(plaintext,"restored legacy credential is empty")
            if key is not None:
                verify_private_key(plaintext,key)
    require(verified_age_tools(authority) == tools,"legacy vault tools changed")


def backup(paths,output,password,code,*,vault_access=None):
    report = inspect_legacy(paths["checkout"])
    require(report["role"] == "private-default" and report["vault"] in ("absent","locked-unverified"),
            "select a private legacy source with a complete vault or proven absence")
    require(all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in paths.values()),
            "select an independent recovery directory")
    with absolute_directory(paths["checkout"]) as checkout:
        verify_history(checkout)
    records,stamps = snapshot(paths)
    ident = str(uuid4())
    manifest = wire(dict(version=1,kind="legacy-backup",id=ident,platform=sys.platform,
                         locations=paths,original=stamps,entries=records))
    with recovery_dir(output) as (root,current):
        tools = scope(file_evidence(os.fstat(root)),*stamps.values())
        prove_vault(records,tools,vault_access)
        sealed = seal_payload(tools,manifest,password,code)
        temp = ".backup-"+ident
        current()
        os.mkdir(temp,0o700,dir_fd=root)
        with open_directory(root,(temp,)) as pending:
            for name,data in (("vault.pub",sealed.recipient),("vault.key.age",sealed.password_envelope),
                              ("vault.recovery.age",sealed.recovery_envelope),(ident+".age",sealed.ciphertext)):
                durable_file(pending,name,data)
            sync_local(pending)
            names = ("vault.key.age","vault.recovery.age")
            original = capture(pending,pending,ident,names)
            verify_backup(tools,pending,pending,ident,password,code,manifest)
            with private_workspace() as (_,stage):
                materialize(stage,records)
                restored = restored_snapshot(stage)
                require(restored == records,"staged legacy restore differs")
                prove_vault(restored,tools,vault_access)
                with open_directory(stage,("checkout",)) as checkout:
                    verify_history(checkout,restored=True)
            require(snapshot(paths) == (records,stamps) and inspect_legacy(paths["checkout"]) == report,
                    "legacy source changed during backup")
            current()
            require(capture(pending,pending,ident,names) == original,"verified backup material changed")
            require(os.path.samestat(os.fstat(pending),os.stat(temp,dir_fd=root,follow_symlinks=False)),
                    "pending backup directory changed")
            rename_guarded(root,temp,root,ident,exchange=False)
            sync_local(root)
            current()
    return ident


@contextmanager
def restore_staging(target,temp,records,created):
    os.mkdir(temp,0o700,dir_fd=target)
    with open_directory(target,(temp,)) as stage:
        try:
            yield stage
        except BaseException:
            # Only a still-named private stage owned by this restore is eligible for cleanup.
            # Move it to a fresh private name before removing the displaced tree.
            try:
                named = os.stat(temp,dir_fd=target,follow_symlinks=False)
            except FileNotFoundError:
                raise
            if os.path.samestat(named,os.fstat(stage)):
                retired = ".cleanup-"+str(uuid4())
                rename_guarded(target,temp,target,retired,exchange=False)
                if os.path.samestat(os.stat(retired,dir_fd=target,follow_symlinks=False),os.fstat(stage)):
                    for key in sorted(created,key=lambda p:p.count("/")):
                        if records[key]["directory"]:
                            with open_directory(stage,key.split("/")) as directory:
                                info = os.fstat(directory)
                                require((info.st_dev,info.st_ino) == created[key][:2],"changed restore directory retained")
                                os.fchmod(directory,stat.S_IMODE(info.st_mode)|0o700)
                    for key in sorted(created,key=lambda p:p.count("/"),reverse=True):
                        parts = key.split("/")
                        try:
                            with open_directory(stage,parts[:-1]) as parent:
                                info = os.stat(parts[-1],dir_fd=parent,follow_symlinks=False)
                                device,inode,complete = created[key]
                                require((info.st_dev,info.st_ino) == (device,inode),"changed restore entry retained")
                                row = records[key]
                                if row["directory"]:
                                    os.rmdir(parts[-1],dir_fd=parent)
                                else:
                                    displaced = ".file-"+str(uuid4())
                                    rename_guarded(parent,parts[-1],stage,displaced,exchange=False)
                                    def same_file(_fd,seen):
                                        require((seen.st_dev,seen.st_ino) == (device,inode),"changed restore file retained")
                                    try:
                                        value = read_file(stage,displaced,JSON_LIMIT,same_file)
                                        expected = base64.b64decode(row["data"],validate=True)
                                        require(value == expected or not complete and expected.startswith(value),
                                                "edited restore file retained")
                                    except BaseException:
                                        rename_guarded(stage,displaced,parent,parts[-1],exchange=False)
                                        raise
                                    os.unlink(displaced,dir_fd=stage)
                        except FileNotFoundError:
                            continue
                    os.rmdir(retired,dir_fd=target)
                    sync_local(target)
            raise


def restore(output,ident,destination,phrase,*,recovery=False):
    require(valid_id(ident) and not output.is_relative_to(destination) and not destination.is_relative_to(output),
            "select an independent private restoration directory")
    with recovery_dir(output) as (root,current),open_directory(root,(ident,)) as saved:
        require_private_directory(saved,"retained backup")
        with recovery_dir(destination) as (target,check_target):
            tools = scope(file_evidence(os.fstat(root)),file_evidence(os.fstat(target)))
            contents = read_payload(tools,saved,saved,ident,phrase,recovery=recovery)
            manifest = parse_json(contents)
            fields(manifest,"version kind id platform locations original entries")
            require(manifest["version"] == 1 and manifest["kind"] == "legacy-backup" and manifest["id"] == ident
                    and manifest["platform"] == sys.platform,"unsupported migration recovery set")
            originals = [checked_path(Path(name)) for name in manifest["locations"].values()]
            require(all(not destination.is_relative_to(p) and not p.is_relative_to(destination) for p in originals),
                    "restore into a separate private directory")
            require(ident not in os.listdir(target),"restored directory already exists")
            temp = ".restore-"+str(uuid4())
            created = {}
            with restore_staging(target,temp,manifest["entries"],created) as stage:
                materialize(stage,manifest["entries"],created)
                with open_directory(stage,("checkout",)) as checkout:
                    verify_history(checkout,restored=True)
                require(restored_snapshot(stage) == manifest["entries"],"restored recovery set differs")
                current()
                require_private_directory(saved,"retained backup")
                require(os.path.samestat(os.fstat(saved),os.stat(ident,dir_fd=root,follow_symlinks=False)),
                        "retained backup directory changed")
                check_target()
                require(os.path.samestat(os.fstat(stage),os.stat(temp,dir_fd=target,follow_symlinks=False)),
                        "new restore directory changed")
                rename_guarded(target,temp,target,ident,exchange=False)
                sync_local(target)
                check_target()
    return destination/ident


def backup_command(args,*,reopen=False):
    import json
    import pwd
    import subprocess
    from store_login import prompt_passphrase
    from store_transfer import interruptible_preview
    from store_transfer_command import operator_language

    if reopen:
        require(len(args) == 4 and valid_id(args[1]) and args[3] in ("password","code"),
                "provide backup directory, backup ID, private restore directory and password or code")
    else:
        require(len(args) == 5,"provide original checkout, state, settings, SSH config and private backup directory")
    lang = operator_language(os.environ,Path(pwd.getpwuid(os.geteuid()).pw_dir))
    app = Path(__file__).resolve().parents[1]/"hostbook"
    labels = subprocess.run(["/bin/bash",str(app),"_store_migration_labels"],env={"PATH":"/usr/bin:/bin",
                            "HOME":"/","HOSTBOOK_LANG":lang},stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,timeout=8)
    text = labels.stdout.decode().splitlines()
    require(labels.returncode == 0 and len(text) == 8 and all(s and "‹" not in s for s in text),
            "migration prompt translations are unavailable")
    def secret(index):
        return prompt_passphrase(text[index]+" ")
    with interruptible_preview():
        if reopen:
            code = args[3] == "code"
            restore(Path(args[0]),args[1],Path(args[2]),secret(5 if code else 4),recovery=code)
            print(json.dumps(dict(checkpoint="restored-private-copy",backup_id=args[1])))
        else:
            paths = dict(zip(ROLES,map(Path,args[:4])))
            report = inspect_legacy(paths["checkout"])
            require(report["role"] == "private-default" and report["vault"] in ("absent","locked-unverified"),
                    "resolve the legacy classification or damaged vault before backup")
            access = (secret(6),secret(7)) if report["vault"] != "absent" else None
            password = secret(0)
            require(password and secret(1) == password,"migration backup passwords differ")
            code = secret(2)
            require(code and secret(3) == code and code != password,"migration backup recovery codes differ")
            ident = backup(paths,Path(args[4]),password,code,vault_access=access)
            print(json.dumps(dict(checkpoint="backup-verified",backup_id=ident)))
    return 0
