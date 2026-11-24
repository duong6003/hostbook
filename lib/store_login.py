"""Open registered-store key credentials for one native SSH launch."""
from contextlib import contextmanager
from dataclasses import dataclass
import grp
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import time

from store_context import (JSON_LIMIT, StoreContextError, bound_directory, open_directory,
                           path_parts, read_file, require, require_no_acl_grants, resolved_store)
from store_vault import VaultUnlockCache, file_evidence, read_vault


AGE_PROGRAMS = ("age", "age-keygen", "age-plugin-batchpass")
AGENT_GUARD = """
/usr/bin/ssh-agent -D -a "$1" -t 2h </dev/null &
agent=$!
trap 'kill "$agent" 2>/dev/null; wait "$agent" 2>/dev/null || :' EXIT
trap 'exit 129' HUP
trap 'exit 143' TERM
/bin/cat >/dev/null
"""


def age_installations():
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir).resolve(strict=True)
    local = home / ".local" / "bin"
    return (
        (Path("/usr/bin"), None),
        (local, None),
        (Path("/opt/homebrew/bin"), Path("/opt/homebrew/Cellar/age")),
        (Path("/usr/local/bin"), Path("/usr/local/Cellar/age")),
    )


def tool_ancestry(directory, target):
    return dict.fromkeys((directory, *directory.parents, target, *target.parents))


@dataclass(frozen=True)
class ToolScope:
    """Directory bindings excluded from program lookup before any store has been enrolled."""
    excluded: frozenset


def verified_age_tools(account):
    return verified_programs(account, age_installations(), AGE_PROGRAMS,
                             "registered login requires age, age-keygen and batchpass in an approved tool directory")


def verified_programs(account, installations, programs, unavailable, *, cellar_only=False):
    if isinstance(account,ToolScope):
        protected = account.excluded
    else:
        protected = {(item.device, item.inode) for item in (account.root, account.state, account.recovery)}
        for checkout in account.checkouts.values():
            protected.update((item.device, item.inode) for item in (checkout.root, checkout.git_dir, checkout.git_common_dir))
    owners = (0, os.geteuid())
    for directory, cellar in installations:
        try:
            if directory.resolve(strict=True) != directory:
                continue
            paths = [(directory / name).resolve(strict=True) for name in programs]
            target = paths[0].parent
            if (cellar_only or target != directory) and not (cellar and target.is_relative_to(cellar)
                                              and len(target.relative_to(cellar).parts) == 2
                                              and target.name == "bin"):
                continue
            if paths != [target / name for name in programs]:
                continue
            admin_group = None
            brew = cellar and target.is_relative_to(cellar)
            if brew and sys.platform == "darwin":
                admin_group = grp.getgrnam("admin").gr_gid
            evidence = []
            for parent in tool_ancestry(directory, target):
                fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    info = os.fstat(fd)
                    admin_cellar = (admin_group is not None and info.st_gid == admin_group
                                    and parent.is_relative_to(cellar.parent.parent))
                    require((info.st_dev, info.st_ino) not in protected and info.st_uid in owners
                            and not info.st_mode & stat.S_IWOTH
                            and (not info.st_mode & stat.S_IWGRP or admin_cellar),
                            "untrusted registered vault tool directory")
                    require_no_acl_grants(fd)
                    evidence.append((info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid))
                finally:
                    os.close(fd)
            for path in paths:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    info = os.fstat(fd)
                    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid in owners
                            and not info.st_mode & 0o022 and os.access(path, os.X_OK),
                            "untrusted registered vault tool")
                    require_no_acl_grants(fd)
                    evidence.append((*file_evidence(info), info.st_mode, info.st_uid, info.st_gid))
                finally:
                    os.close(fd)
            return target, tuple(evidence)
        except (OSError, KeyError, RuntimeError, StoreContextError):
            continue
    raise StoreContextError(unavailable)


@dataclass(frozen=True, repr=False)
class KeyCredential:
    evidence: tuple
    public_key: bytes
    ciphertext: bytes


def current_credentials(load_account, store_id, expected, names):
    account = load_account()
    keys, interactive = {}, {}
    with resolved_store(account, store_id, expected) as (_, binding, root_fd, _):
        parts = path_parts(account.root.path, binding.root.path)
        with bound_directory(root_fd, parts, binding.root, "checkout binding mismatch") as checkout_fd:
            with open_directory(checkout_fd, ("secrets",)) as vault_fd:
                for name in names:
                    evidence = []

                    def verify(_fd, info):
                        require(info.st_nlink == 1, "linked store credential is not allowed")
                        evidence.append(file_evidence(info))

                    meta = read_file(vault_fd, f"{name}.meta", 4096, verify)
                    types = [line for line in meta.splitlines() if line.startswith(b"type=")]
                    if types == [b"type=password"]:
                        interactive[name] = tuple(evidence)
                        continue
                    require(types == [b"type=key"], "registered login requires a supported credential type")
                    pub = read_file(vault_fd, f"{name}.pub", 4096, verify)
                    encrypted = read_file(vault_fd, f"{name}.age", JSON_LIMIT, verify)
                    require(len(pub.splitlines()) == 1 and len(pub.split()) >= 2 and encrypted,
                            "incomplete store key credential")
                    keys[name] = KeyCredential(tuple(evidence), pub, encrypted)
        material = read_vault(root_fd, account, binding) if keys else None
    return account, material, keys, interactive


def prompt_passphrase(prompt="Password: "):
    import getpass
    import warnings

    try:
        with open("/dev/tty", "w") as terminal, warnings.catch_warnings():
            warnings.simplefilter("error",getpass.GetPassWarning)
            return getpass.getpass(prompt=prompt,stream=terminal).encode()
    except (OSError, EOFError, getpass.GetPassWarning):
        raise StoreContextError("registered vault unlock requires a terminal") from None


@contextmanager
def ssh_agent(socket):
    ssh_agent_bin = "/usr/bin/ssh-agent"
    require(os.access(ssh_agent_bin, os.X_OK), "registered login requires system ssh-agent")
    env = {"PATH": "/usr/bin:/bin", "SSH_AUTH_SOCK": str(socket), "SSH_ASKPASS_REQUIRE": "never"}
    # Only this process retains the write end. The guard observes EOF even after SIGKILL.
    child = subprocess.Popen(["/bin/sh", "-c", AGENT_GUARD, "hostbook-agent", socket], env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 2
        while not socket.is_socket() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        require(socket.is_socket() and child.poll() is None, "could not start the private SSH agent")
        yield env
    finally:
        child.stdin.close()
        child.wait(timeout=5)


def agent_keys(env, *args, data=None):
    try:
        return subprocess.run(["/usr/bin/ssh-add", *args], input=data or b"", env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        raise StoreContextError("could not load the selected store key") from None


@contextmanager
def scoped_login(load_account, store_id, expected, config, targets, read_passphrase):
    names = sorted(set(targets.values()))
    selection = current_credentials(load_account, store_id, expected, names)
    keys = selection[2]
    cache = None

    def verify():
        require(current_credentials(load_account, store_id, expected, names) == selection
                and (cache is None or cache.get(load_account, store_id, expected) is not None),
                "store credential changed during unlock")

    if not keys:
        yield verify
        return
    tool_dir, evidence = verified_age_tools(selection[0])
    passphrase = (read_passphrase or prompt_passphrase)()
    try:
        same_tools = verified_age_tools(selection[0]) == (tool_dir, evidence)
    except StoreContextError:
        same_tools = False
    require(same_tools, "registered vault tools changed during unlock")
    cache = VaultUnlockCache(tool_dir)
    cache.unlock(load_account, store_id, expected, passphrase)
    del passphrase
    verify()
    socket = config.parent / "agent"
    with ssh_agent(socket) as env:
        public_files, verified = {}, []
        identity = cache.get(load_account, store_id, expected)
        for number, (name, key) in enumerate(keys.items()):
            if number:
                agent_keys(env, "-D")
            private = cache.open_secret(identity, key.ciphertext)
            agent_keys(env, "-q", "-", data=private)
            actual = [line.split()[:2] for line in agent_keys(env, "-L").splitlines()]
            require(actual == [key.public_key.split()[:2]], "store public and private key do not match")
            verified.append(private)
            public_files[name] = config.parent / f"key-{number}.pub"
            public_files[name].write_bytes(key.public_key)
        if len(verified) > 1:
            agent_keys(env, "-D")
            for private in verified:
                agent_keys(env, "-q", "-", data=private)
        del private, verified
        entries = "".join(f"Host {alias}\n  IdentityFile {public_files[name]}\n"
                          for alias, name in targets.items() if name in public_files)
        configuration = config.read_text().replace("IdentityAgent none\n", f"IdentityAgent {socket}\n", 1)
        config.write_text(configuration + entries)
        yield verify
