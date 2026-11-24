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


def operator_environment(scratch,home):
    tools = scratch/"bin"
    tools.mkdir(mode=0o700)
    env = {"HOME":str(scratch),"PATH":str(tools)+":/usr/bin:/bin","SHELL":"/bin/bash",
           "TERM":os.environ.get("TERM","xterm-256color"),"LANG":os.environ.get("LANG","en_US.UTF-8"),
           "HOSTBOOK_LANG":operator_language(os.environ,home),"HOSTBOOK_DIR":str(scratch/"view"),
           "HOSTBOOK_STATE":str(scratch/"state"),"HOSTBOOK_SSH_CONFIG":str(scratch/"ssh.config"),
           "XDG_CONFIG_HOME":str(scratch/"settings")}
    return tools,env


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


def setting_arguments(changes):
    from store_read import EFFECTIVE_OPTIONS
    allowed = EFFECTIVE_OPTIONS | {"hostname","user","port","proxyjump"}
    args = []
    for key,values in changes.items():
        require(key in allowed,"unsupported transfer review setting")
        # Escape any terminal controls before values enter an ANSI-capable picker.
        args.extend([key,*[json.dumps(str(values[k]),ensure_ascii=True)[1:-1] if values[k] is not None else "—"
                           for k in ("existing","proposed")]])
    return args


def review_arguments(details):
    src,dst = details["source"],details["destination"]
    match = next((row for row in details["matches"] if row["host_id"] == details["chosen_destination_id"]),{})
    args = ["_store_transfer_review",src["name"],src["store_id"],dst["proposed_alias"],dst["store_id"],
            details["publications"][0]["ref"],details["destination_credential"],str(len(details["retained_credential_users"])),
            details["credential_action"],",".join(details["required_aliases"]),src["kind"],details["decision"],
            match.get("name",""),match.get("host_id",""),",".join(details["retained_destination_groups"])]
    if details["decision"] == "update":
        args.extend([src["host_id"],src["ref"],src["revision"],dst["ref"],dst["revision"],match["credential"] or "—"])
        args.extend(setting_arguments(details["connection_changes"]))
    return args


def run_recovery(load, action, ident, secret, confirm, emit):
    from store_transfer_resume import cancel_prepared, resume_confirmed, settle_transfer, transfer_status
    require(action in ("status","resume","cancel","settle","rollback"),"unknown transfer recovery action")
    status = transfer_status(load,ident)
    emit(status)
    if action == "status":
        return 0
    require(status["fenced"],"transfer has already finished")
    password,code = secret("backup"),secret("recovery")
    if action == "settle":
        result = settle_transfer(load,ident,password,code)
        emit(result)
        return 0 if result["outcome"] != "uncertain" else 75
    if action == "rollback":
        from store_transfer_rollback import rollback_private
        result = rollback_private(load,ident,password,code,secret,confirm)
    elif action == "cancel":
        result = cancel_prepared(load,ident,password,code,confirm)
    else:
        result = resume_confirmed(load,ident,password,code,lambda _:secret("destination"),confirm)
    emit(result)
    return 0 if result["checkpoint"] in ("completed","cancelled","rolled-back") else 75


def transfer_command(args, *, recovery=False):
    if recovery:
        require(len(args) == 2 and args[1] in ("status","cancel","resume","settle","rollback"),"provide the original transaction ID and recovery action")
    else:
        require(len(args) in (14,15,18),"provide both registered selections, destination alias, credential name and create or reuse")
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    def load():
        return load_approved_account(home)
    def emit(data):
        print(json.dumps(data),flush=True)
    if recovery and args[1] == "status":
        return run_recovery(load,"status",args[0],None,None,emit)
    account = load()
    locations = [(directory,cellar.with_name("fzf") if cellar else None) for directory,cellar in age_installations()]
    hostbook = Path(__file__).resolve().parents[1]/"hostbook"
    roles = ("source","destination","backup","backup-repeat","recovery","recovery-repeat")
    with interruptible_preview(), private_workspace() as (scratch,_):
        tools,env = operator_environment(scratch,home)
        command = ["/bin/bash",str(hostbook)]
        texts = subprocess.run([*command,"_store_transfer_labels","recover" if recovery else "create"],env=env,stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE,timeout=10)
        require(texts.returncode == 0,"registered transfer prompt translations are unavailable")
        texts = texts.stdout.decode().splitlines()
        require(len(texts) == 1+len(roles) and all(texts),"registered transfer prompt translations are unavailable")
        def picker():
            return compatible_picker(account,locations,texts[0])
        labels = dict(zip(roles,texts[1:]))
        def secret(role):
            return prompt_passphrase(labels[role]+" ")
        if recovery and args[1] == "settle":
            return run_recovery(load,"settle",args[0],secret,None,emit)
        binary = picker()
        (tools/"fzf").symlink_to(binary[0]/"fzf")
        def confirm(details):
            require(picker() == binary and load() == account,"transfer confirmation context changed")
            if details.get("action") == "rollback":
                src,dst = details["source"],details["destination"]
                review = ["_store_transfer_rollback",details["transaction_id"],src["name"],src["store_id"],dst["alias"],
                          dst["store_id"],dst["host_id"],dst["ref"],dst["revision"],dst["inverse"],",".join(details["required_aliases"]),
                          details["credential"],"keep" if details["keep_credential"] else "remove",details["decision"],
                          details["restore_credential"] or "—",*setting_arguments(details["connection_changes"])]
            elif details.get("action") == "cancel":
                review = ["_store_transfer_cancel",details["transaction_id"],details["source"],details["destination"]]
            else:
                review = review_arguments(details)
            terminal_env = dict(env,HOSTBOOK_REVIEW_TRANSACTION=details.get("transaction_id",""),
                                HOSTBOOK_REVIEW_CHECKPOINT=details.get("checkpoint",""))
            proc = subprocess.run([*command,*review],env=terminal_env,stdout=subprocess.PIPE)
            if proc.returncode:
                return False
            if details.get("requires_typed_alias") or details.get("required_aliases"):
                return proc.stdout.decode().splitlines()
            return not proc.stdout
        if recovery:
            return run_recovery(load,args[1],args[0],secret,confirm,emit)
        return run_transfer(load,args,secret,confirm,emit)
