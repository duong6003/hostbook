"""Run a reviewed host transfer between one private and one shared store."""
import json
import os
from pathlib import Path
import pwd
import re
import subprocess

from store_context import Generations, StoreContextError, load_approved_account, read_file, require
from store_receive import absolute_directory
from store_read import SECRET_NAME
from store_login import age_installations, prompt_passphrase, verified_programs
from store_transfer import interruptible_preview, prepare_candidate, preview
from store_transfer_apply import save_destination
from store_transfer_credentials import CredentialRef, private_workspace, stage_credential
from store_transfer_intent import prepare_intent
from store_transfer_publish import confirm_private_destination, publish_destination, publish_source
from store_transfer_remove import finish_shared_source, remove_source


def run_transfer(load, args, secret, confirm, emit):
    require(len(args) in (14,15,18) and SECRET_NAME.fullmatch(args[12]) and args[13] in ("create","reuse"), "provide both registered selections, destination alias, credential name and create or reuse")
    plan = preview(load,args[0],Generations(*(int(v) for v in args[1:5])),args[5],
                   args[6],Generations(*(int(v) for v in args[7:11])),args[11])
    if len(args) == 18:
        plan = plan.choose(*args[14:])
    elif len(args) == 15:
        require(args[14] == "cancel","unknown transfer decision")
    comparison = plan.comparison()
    emit(dict(comparison,checkpoint="comparison",destination_credential=args[12],credential_action=args[13]))
    if len(args) == 15:
        emit({"checkpoint":"cancelled"})
        return 0
    plan.verify_decision()
    require(comparison["shared_mode"] == "two-way" and comparison["shared_checkout_at_remote_tip"],
            "this transfer needs one current two-way shared checkout")
    credential = comparison["source"]["credential"]
    require(credential, "this transfer requires a managed source credential")
    src = CredentialRef(plan.source.context.store_id,plan.source.context.generations,credential)
    dst = CredentialRef(plan.destination.context.store_id,plan.destination.context.generations,args[12])
    store_phrases = {}
    def unlock(store_id):
        require(store_id in (src.store_id,dst.store_id), "unexpected transfer vault")
        if store_id not in store_phrases:
            store_phrases[store_id] = secret("source" if store_id == src.store_id else "destination")
        return store_phrases[store_id]
    mapped = stage_credential(load,src,dst,unlock,reuse_existing=args[13] == "reuse")
    with prepare_candidate(load,plan,mapped) as candidate:
        password = secret("backup")
        require(password and secret("backup-repeat") == password,"transaction backup passwords differ")
        code = secret("recovery")
        require(code and secret("recovery-repeat") == code and password != code,"independent transaction recovery codes differ")
        def reviewed(details):
            if plan.decision == "update":
                emit(dict(details,checkpoint="review"))
            return confirm(details)
        ident = prepare_intent(load,plan,candidate,mapped,unlock,password,code,reviewed)
        emit({"transaction_id":ident,"checkpoint":"prepared"})
        save_destination(load,ident,plan,candidate,mapped,unlock,password,code)
        arguments = load,ident,plan,candidate,mapped,unlock,password,code
        if plan.destination.context.kind == "shared":
            result = publish_destination(*arguments)
            if result["checkpoint"] != "destination-confirmed":
                emit(result)
                return 75
        else:
            confirm_private_destination(*arguments)
        # Reopen current destination vault before removing either source.
        del store_phrases[dst.store_id]
        result = remove_source(*arguments)
        if plan.source.context.kind == "shared":
            result = publish_source(*arguments)
            if result["checkpoint"] != "source-confirmed":
                emit(result)
                return 75
            result = finish_shared_source(*arguments)
        emit(result)
    return 0


def operator_language(env, home):
    language = env.get("HOSTBOOK_LANG","")
    if not language:
        configured = Path(env.get("XDG_CONFIG_HOME") or Path(env.get("HOME") or home)/".config")/"hostbook"
        if configured.is_absolute():
            try:
                with absolute_directory(configured) as directory:
                    data = read_file(directory,"config",65536)
                for line in data.split(b"\n"):
                    key,sep,value = line.partition(b"=")
                    if sep and key.strip() == b"lang":
                        language = re.split(rb"[ \t]# ",value,maxsplit=1)[0].strip().decode("ascii",errors="replace")
            except (OSError,StoreContextError):
                pass
    if language in ("vi","en"):
        return language
    locale = env.get("LC_ALL") or env.get("LC_MESSAGES") or env.get("LANG","")
    return "vi" if locale.startswith("vi") else "en"


def compatible_picker(account, locations, minimum):
    require(re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}",minimum),"invalid installed fzf requirement")
    required = tuple(int(v) for v in minimum.split("."))
    reason = "registered transfer confirmation requires an approved fzf "+minimum+" or later"
    for location in locations:
        try:
            binary = verified_programs(account,(location,),("fzf",),reason)
            result = subprocess.run([binary[0]/"fzf","--version"],env={"PATH":"/usr/bin:/bin"},stdin=subprocess.DEVNULL,
                                    capture_output=True,timeout=5)
            version = re.match(rb"([0-9]+(?:\.[0-9]+){1,2})(?:[ \r\n]|$)",result.stdout)
            if result.returncode == 0 and version and tuple(map(int,version[1].split(b"."))) >= required:
                return binary
        except (OSError,subprocess.SubprocessError,StoreContextError):
            continue
    raise StoreContextError(reason)


def transfer_command(args):
    require(len(args) in (14,15,18),"provide both registered selections, destination alias, credential name and create or reuse")
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    def load():
        return load_approved_account(home)
    account = load()
    locations = [(directory,cellar.with_name("fzf") if cellar else None) for directory,cellar in age_installations()]
    hostbook = Path(__file__).resolve().parents[1]/"hostbook"
    roles = ("source","destination","backup","backup-repeat","recovery","recovery-repeat")
    with interruptible_preview(), private_workspace() as (scratch,_):
        tools = scratch/"bin"
        tools.mkdir(mode=0o700)
        env = {"HOME":str(scratch),"PATH":str(tools)+":/usr/bin:/bin","SHELL":"/bin/bash",
               "TERM":os.environ.get("TERM","xterm-256color"),"LANG":os.environ.get("LANG","en_US.UTF-8"),
               "HOSTBOOK_LANG":operator_language(os.environ,home),"HOSTBOOK_DIR":str(scratch/"view"),
               "HOSTBOOK_STATE":str(scratch/"state"),"HOSTBOOK_SSH_CONFIG":str(scratch/"ssh.config"),
               "XDG_CONFIG_HOME":str(scratch/"settings")}
        command = ["/bin/bash",str(hostbook)]
        texts = subprocess.run([*command,"_store_transfer_labels"],env=env,stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE,timeout=10)
        require(texts.returncode == 0,"registered transfer prompt translations are unavailable")
        texts = texts.stdout.decode().splitlines()
        require(len(texts) == 1+len(roles) and all(texts),"registered transfer prompt translations are unavailable")
        def picker():
            return compatible_picker(account,locations,texts[0])
        binary = picker()
        (tools/"fzf").symlink_to(binary[0]/"fzf")
        labels = dict(zip(roles,texts[1:]))
        def secret(role):
            return prompt_passphrase(labels[role]+" ")
        def confirm(details):
            require(picker() == binary and load() == account,"transfer confirmation context changed")
            src,dst = details["source"],details["destination"]
            match = next((r for r in details["matches"] if r["host_id"] == details["chosen_destination_id"]),{})
            proc = subprocess.run([*command,"_store_transfer_review",src["name"],src["store_id"],
                dst["proposed_alias"],dst["store_id"],details["publications"][0]["ref"],details["destination_credential"],
                str(len(details["retained_credential_users"])),details["credential_action"],
                ",".join(details["required_aliases"]),src["kind"],details["decision"],match.get("name",""),match.get("host_id",""),
                ",".join(details["retained_destination_groups"]),",".join(details["connection_changes"])],env=env,stdout=subprocess.PIPE)
            if proc.returncode:
                return False
            if details["requires_typed_alias"]:
                return proc.stdout.decode().splitlines()
            return not proc.stdout
        return run_transfer(load,args,secret,confirm,lambda data:print(json.dumps(data),flush=True))
