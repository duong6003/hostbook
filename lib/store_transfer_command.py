"""Run one explicitly reviewed transfer of a private host to a registered shared store."""
import json
import os
from pathlib import Path
import pwd
import subprocess

from store_context import Generations, load_approved_account, require
from store_read import SECRET_NAME
from store_login import age_installations, prompt_passphrase, verified_programs
from store_transfer import interruptible_preview, prepare_candidate, preview
from store_transfer_apply import save_destination
from store_transfer_credentials import CredentialRef, private_workspace, stage_credential
from store_transfer_intent import prepare_intent
from store_transfer_publish import publish_destination
from store_transfer_remove import finish_private_source


def run_transfer(load, args, secret, confirm, emit):
    require(len(args) == 14 and SECRET_NAME.fullmatch(args[12]) and args[13] in ("create","reuse"), "provide both registered selections, destination alias, credential name and create or reuse")
    plan = preview(load,args[0],Generations(*(int(v) for v in args[1:5])),args[5],
                   args[6],Generations(*(int(v) for v in args[7:11])),args[11])
    comparison = plan.comparison()
    emit(dict(comparison,checkpoint="comparison",destination_credential=args[12],credential_action=args[13]))
    require(plan.source.context.kind == "private" and plan.destination.context.kind == "shared"
            and comparison["shared_mode"] == "two-way" and comparison["shared_checkout_at_remote_tip"]
            and not comparison["matches"], "this transfer needs a current two-way shared destination and a free host")
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
        ident = prepare_intent(load,plan,candidate,mapped,unlock,password,code,confirm)
        emit({"transaction_id":ident,"checkpoint":"prepared"})
        save_destination(load,ident,plan,candidate,mapped,unlock,password,code)
        result = publish_destination(load,ident,plan,candidate,mapped,unlock,password,code)
        if result["checkpoint"] != "destination-confirmed":
            emit(result)
            return 75
        # Reopen current destination vault before removing the private source.
        del store_phrases[dst.store_id]
        emit(finish_private_source(load,ident,plan,candidate,mapped,unlock,password,code))
    return 0


def transfer_command(args):
    require(len(args) == 14,"provide both registered selections, destination alias, credential name and create or reuse")
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    def load():
        return load_approved_account(home)
    account = load()
    locations = [(directory,cellar.with_name("fzf") if cellar else None) for directory,cellar in age_installations()]
    def picker():
        return verified_programs(account,locations,("fzf",),"registered transfer confirmation requires an approved fzf")
    binary = picker()
    hostbook = Path(__file__).resolve().parents[1]/"hostbook"
    roles = ("source","destination","backup","backup-repeat","recovery","recovery-repeat")
    with interruptible_preview(), private_workspace() as (scratch,_):
        tools = scratch/"bin"
        tools.mkdir(mode=0o700)
        (tools/"fzf").symlink_to(binary[0]/"fzf")
        env = {"HOME":str(scratch),"PATH":str(tools)+":/usr/bin:/bin","SHELL":"/bin/bash",
               "TERM":os.environ.get("TERM","xterm-256color"),"LANG":os.environ.get("LANG","en_US.UTF-8"),
               "HOSTBOOK_LANG":os.environ.get("HOSTBOOK_LANG",""),"HOSTBOOK_DIR":str(scratch/"view"),
               "HOSTBOOK_STATE":str(scratch/"state"),"HOSTBOOK_SSH_CONFIG":str(scratch/"ssh.config"),
               "XDG_CONFIG_HOME":str(scratch/"settings")}
        command = ["/bin/bash",str(hostbook)]
        texts = subprocess.run([*command,"_store_transfer_labels"],env=env,stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE,timeout=10)
        require(texts.returncode == 0,"registered transfer prompt translations are unavailable")
        texts = texts.stdout.decode().splitlines()
        require(len(texts) == len(roles) and all(texts),"registered transfer prompt translations are unavailable")
        labels = dict(zip(roles,texts))
        def secret(role):
            return prompt_passphrase(labels[role]+" ")
        def confirm(details):
            require(picker() == binary and load() == account,"transfer confirmation context changed")
            src,dst = details["source"],details["destination"]
            proc = subprocess.run([*command,"_store_transfer_review",src["name"],src["store_id"],
                dst["proposed_alias"],dst["store_id"],details["publications"][0]["ref"],details["destination_credential"],
                str(len(details["retained_credential_users"])),details["credential_action"],
                "prod" if details["requires_typed_alias"] else "ordinary"],env=env,stdout=subprocess.PIPE)
            if proc.returncode:
                return False
            if details["requires_typed_alias"]:
                return proc.stdout.decode().rstrip("\n")
            return not proc.stdout
        return run_transfer(load,args,secret,confirm,lambda data:print(json.dumps(data),flush=True))
