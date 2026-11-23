"""Open registered-store key credentials for one native SSH launch."""
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import time

from store_context import (JSON_LIMIT, StoreContextError, bound_directory, open_directory,
                           path_parts, read_file, require, resolved_store)
from store_vault import VaultUnlockCache, file_evidence, read_vault


@dataclass(frozen=True, repr=False)
class KeyCredential:
    evidence: tuple
    public_key: bytes
    ciphertext: bytes


def current_keys(load_account, store_id, expected, names):
    account = load_account()
    keys = {}
    with resolved_store(account, store_id, expected) as (_, binding, root_fd, _):
        material = read_vault(root_fd, account, binding)
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
                    require(types == [b"type=key"], "registered login requires a supported store key credential")
                    pub = read_file(vault_fd, f"{name}.pub", 4096, verify)
                    encrypted = read_file(vault_fd, f"{name}.age", JSON_LIMIT, verify)
                    require(len(pub.splitlines()) == 1 and len(pub.split()) >= 2 and encrypted,
                            "incomplete store key credential")
                    keys[name] = KeyCredential(tuple(evidence), pub, encrypted)
    return account, material, keys


def prompt_passphrase():
    import getpass

    try:
        with open("/dev/tty", "w") as terminal:
            return getpass.getpass(stream=terminal).encode()
    except (OSError, EOFError):
        raise StoreContextError("registered vault unlock requires a terminal") from None


@contextmanager
def ssh_agent(socket):
    ssh_agent_bin = "/usr/bin/ssh-agent"
    require(os.access(ssh_agent_bin, os.X_OK), "registered login requires system ssh-agent")
    env = {"PATH": "/usr/bin:/bin", "SSH_AUTH_SOCK": str(socket), "SSH_ASKPASS_REQUIRE": "never"}
    child = subprocess.Popen([ssh_agent_bin, "-D", "-a", socket, "-t", "2h"], env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 2
        while not socket.is_socket() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        require(socket.is_socket() and child.poll() is None, "could not start the private SSH agent")
        yield env
    finally:
        child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def agent_keys(env, data=None):
    args = ["-L"] if data is None else ["-q", "-"]
    try:
        return subprocess.run(["/usr/bin/ssh-add", *args], input=data or b"", env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        raise StoreContextError("could not load the selected store key") from None


@contextmanager
def scoped_login(load_account, store_id, expected, config, targets, read_passphrase):
    names = sorted(set(targets.values()))
    selection = current_keys(load_account, store_id, expected, names)
    path = shutil.which("age")
    require(path and Path(path).is_absolute(), "registered login requires age on the trusted tool path")
    cache = VaultUnlockCache(Path(path).parent)
    cache.unlock(load_account, store_id, expected, (read_passphrase or prompt_passphrase)())

    def verify():
        require(current_keys(load_account, store_id, expected, names) == selection
                and cache.get(load_account, store_id, expected) is not None,
                "store credential changed during unlock")

    verify()
    material = selection[2]
    socket = config.parent / "agent"
    with ssh_agent(socket) as env:
        public_files, offers = {}, set()
        identity = cache.get(load_account, store_id, expected)
        for number, name in enumerate(names):
            key = material[name]
            offers.add(tuple(key.public_key.split()[:2]))
            agent_keys(env, cache.open_secret(identity, key.ciphertext))
            public_files[name] = config.parent / f"key-{number}.pub"
            public_files[name].write_bytes(key.public_key)
        actual = {tuple(line.split()[:2]) for line in agent_keys(env).splitlines()}
        require(actual == offers, "store public and private key do not match")
        entries = "".join(f"Host {alias}\n  IdentityFile {public_files[name]}\n" for alias, name in targets.items())
        configuration = config.read_text().replace("IdentityAgent none\n", f"IdentityAgent {socket}\n", 1)
        config.write_text(configuration + entries)
        yield verify
