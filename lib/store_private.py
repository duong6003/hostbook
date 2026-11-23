"""Admit new literal hosts in the registered private store."""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import sys
import tempfile
from uuid import uuid4

from store_checkout import read_index, read_ref, tree
from store_context import (INDEX_NAME, JSON_LIMIT, StoreContextError, bound_directory, entry_generations,
                           fixture_request, load_approved_account, open_directory, path_parts, read_file,
                           registry_entries, require, require_private_directory, resolved_store, store_writer,
                           valid_ref, verified_account, verify_state_directory)
from store_journal import require_idle
from store_read import DIRECTIVE, local_inputs
from store_receive import (IDENTITY, absolute_directory, fetch_from_fd, git, install_adoption, oid, plain_git,
                           prepare_adoption, require_no_receive_pending, verify_incoming)


def private_destination(account):
    with verified_account(account) as (_, state):
        entries, _ = registry_entries(state, account)
        private = [entry for entry in entries.values() if entry["kind"] == "private"]
        require(len(private) == 1, "a registered private store is required")
        return private[0]["store_id"], entry_generations(private[0])


def admitted_hosts(document):
    require(0 < len(document) <= JSON_LIMIT, "private host input exceeds size limit")
    records, current = [], None
    physical = document.decode().replace("\r\n","\n")
    require(not re.search(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]",physical),
            "plain host imports require LF or CRLF lines")
    for line in physical.split("\n"):
        text = line.strip(" \t")
        if not text:
            continue
        if text.startswith("#"):
            require(not re.match(r"#\s*secret\s*:",text,re.I), "credential import requires a separate review")
            continue
        item = DIRECTIVE.fullmatch(text)
        require(item is not None, "invalid private host directive")
        key, value = item[1].lower(), item[2].strip()
        if key == "host":
            require(INDEX_NAME.fullmatch(value) and not value.startswith("-"), "import requires concrete host names")
            current = {"name":value}
            records.append(current)
        else:
            require(current and key in ("hostname","user","port") and key not in current,
                    "plain private import supports one HostName, User and Port per concrete Host")
            if key == "hostname":
                require("%" not in value, "provide an unscoped address")
                try:
                    ipaddress.ip_address(value)
                except ValueError:
                    raise StoreContextError("provide a literal IPv4 or IPv6 address") from None
            if key == "port":
                require(value.isascii() and value.isdigit() and 1 <= int(value) <= 65535, "invalid SSH port")
            if key == "user":
                require(re.fullmatch(r"[a-zA-Z0-9._@-]+",value), "unsupported SSH user")
            current[key] = value
    require(records and len(records) <= 256 and all("hostname" in row for row in records),
            "each new host requires one literal address")
    require(len({row["name"].lower() for row in records}) == len(records), "duplicate private host name")
    return records


@contextmanager
def private_binding(account, store_id, expected, marker=None):
    with resolved_store(account, store_id, expected) as (context, binding, root, state):
        require(context.kind == "private" and context.transition_id is None and context.target_id is None
                and binding.git_dir.path == context.checkout/".git" and binding.git_common_dir == binding.git_dir,
                "private writes require an unfenced regular private checkout")
        with bound_directory(root, path_parts(account.root.path,account.recovery.path),account.recovery,
                             "private recovery root changed") as recovery:
            require_idle(recovery,account,store_id)
        with open_directory(state, ("stores","v1",store_id)) as namespace:
            require_private_directory(namespace, "private store")
            require_no_receive_pending(namespace,marker)
            yield context, binding, namespace


def private_commit(stage, base, store, records, document):
    identity = json.loads(git(stage,"show",base+":"+IDENTITY))
    require(identity["store_id"] == store, "private identity changed")
    current = {row["name"].lower() for row in identity["hosts"]}
    require(not any(row["name"].lower() in current for row in records), "private host name already exists")
    project = "hosts-" + str(uuid4()) + ".conf"
    require(project not in tree(stage,base,git), "new private project already exists")
    identity["sources"].insert(0, {"path":project,"sha256":hashlib.sha256(document).hexdigest()})
    host_ids = []
    for ordinal, row in enumerate(records,1):
        host = str(uuid4())
        host_ids.append(host)
        identity["hosts"].append({"host_id":host,"name":row["name"],"bindings":[
            {"path":project,"host_ordinal":ordinal,"token_ordinal":1}]})
    identity["generation"] += 1
    git(stage,"read-tree",base)
    for path, data in ((project,document),(IDENTITY,json.dumps(identity,ensure_ascii=False).encode()+b"\n")):
        blob = git(stage,"hash-object","-w","--stdin",data=data).strip().decode()
        git(stage,"update-index","--add","--cacheinfo","100644",blob,path)
    root = oid(stage,git(stage,"write-tree").strip().decode())
    commit = git(stage,"-c","user.name=Hostbook","-c","user.email=hostbook@localhost",
                 "commit-tree",root,"-p",base,"-m","Add private hosts").strip().decode()
    return commit,host_ids


def add_private(load, document):
    records = admitted_hosts(document)
    account = load()
    store, expected = private_destination(account)
    with store_writer(load,store,expected) as (actual,start,state,_):
        require(actual == account,"private account changed")
        local = local_inputs(state,store)
        with private_binding(account,store,expected) as (ctx,binding,_):
            require(start == ctx,"private registration changed")
            with absolute_directory(ctx.git_dir) as repo:
                info = os.fstat(repo)
                require((info.st_dev,info.st_ino) == (binding.git_dir.device,binding.git_dir.inode),"private Git changed")
                plain_git(repo,bare=False)
                head = read_file(repo,"HEAD",4096).decode().strip()
                require(head.startswith("ref: ") and valid_ref(head[5:]),"private checkout needs an attached branch")
                ref = head[5:]
                base, original_index = read_ref(repo,ref), read_index(repo)
    with tempfile.TemporaryDirectory(prefix="private-add-",dir=account.state.path) as folder:
        stage = Path(folder)
        git(stage,"init","-q","--bare","--template=")
        with absolute_directory(ctx.git_dir) as repo:
            info = os.fstat(repo)
            require((info.st_dev,info.st_ino) == (binding.git_dir.device,binding.git_dir.inode),"private Git changed")
            require(fetch_from_fd(stage,repo,ref,"refs/hostbook/base")==base,"private branch changed")
        before = tree(stage,base,git)
        tip,host_ids = private_commit(stage,base,store,records,document)
        requested = {row["name"]:{key:value for key,value in row.items() if key != "name"} for row in records}
        after,changes = verify_incoming(stage,tip,base,store,local,requested)
        prepare_adoption(stage,original_index,before,base,tip)
        with store_writer(load,store,expected) as (actual,current,state,writer):
            require(actual==account and current==ctx,"private registration changed")
            with private_binding(account,store,expected) as (_,active,namespace):
                require(active==binding,"private checkout changed")
                def verify(marker=None):
                    require(load()==account and local_inputs(state,store)==local,"private approval or SSH inputs changed")
                    with private_binding(account,store,expected,marker) as (now,checked,source):
                        require((now,checked)==(ctx,binding) and os.path.samestat(os.fstat(namespace),os.fstat(source)),
                                "private checkout changed")
                    verify_state_directory(state,path_parts(account.state.path,ctx.writer_lock),writer,"private writer changed")
                record = {"operation":"private-add","store_id":store,"target_id":None,"ref":ref,"base":base,"incoming":tip,
                          "connection_generation":expected.connection,"checkout_generation":expected.checkout,
                          "recovery_id":str(uuid4())}
                install_adoption(account,ctx,binding,namespace,record,stage,original_index,before,after,changes,verify)
    return {"store_id":store,"host_ids":host_ids,"commit":tip,"generations":asdict(expected)}


def private_command(action,args):
    importing = action.endswith("import")
    require(not args if importing else len(args)==2, "provide a host name and literal address, or import on stdin")
    if action.startswith("_"):
        packet = fixture_request("document") if importing else fixture_request()
        def load():
            return packet[0]
        if importing:
            require(isinstance(packet[3],str),"expected a plain host document")
            document = packet[3].encode()
    else:
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
        def load():
            return load_approved_account(home)
        if importing:
            document = sys.stdin.buffer.read(JSON_LIMIT+1)
    if not importing:
        name,address = args
        require(INDEX_NAME.fullmatch(name) and not any(ch.isspace() for ch in address),"invalid host name or address")
        document = f"Host {name}\n  HostName {address}\n".encode()
    print(json.dumps(add_private(load,document)))
    return 0
