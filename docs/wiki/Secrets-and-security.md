# Secrets and security

## Reusable credentials

The secret vault holds named SSH keys and passwords, encrypted with
[age](https://github.com/FiloSottile/age). A credential can be reused across hosts;
key fingerprints help avoid duplicate imports.

Use `Ctrl-O` -> `Secret`, or:

```bash
hostbook secrets
```

You can import or generate keys, replace credentials, rename entries, copy public
keys, inspect usage, and remove credentials after reviewing affected hosts.
When adding a host, `Đăng nhập bằng` selects an existing credential or creates one.
Existing external agents and hardware-backed keys can use the option that lets SSH
manage authentication without storing a new secret.

During SSH-config import, an external private key is saved to the vault only after you confirm it. The original key file is left in place. A separately confirmed key save is committed independently and remains saved if you later cancel the host import. Imported sources with unsupported inherited credentials, executable Match rules, or Include directives are rejected before SSH examines them. Share bundles are validated before extraction and reject archive links.

## Unlocking and external SSH clients

The vault identity is protected by a master password. A recovery code is shown when
the vault is created; save it securely. Encrypted credentials sync with the host list.

```bash
hostbook unlock
hostbook lock
hostbook lock --forget
```

Unlocking loads keys into ssh-agent. External `ssh`, `scp`, and VS Code Remote-SSH
can use them when they share that agent. Start hostbook from a shell with a working
SSH agent if you need this behavior. When no usable agent is inherited, hostbook can
start a private fallback agent; that agent does not update the invoking shell's
`SSH_AUTH_SOCK`, so other clients will not automatically see its keys.

The default key lifetime is eight hours. `lock` unloads managed keys from the agent
available to that invocation; `lock --forget` also clears the remembered vault
identity for this machine.

On Windows the master password is requested for each new hostbook invocation that
needs the vault. Askpass children share only a path to a temporary **DPAPI-encrypted**
identity cache; the vault identity is never stored as plaintext or in environment
variables. Normal exit removes that invocation's cache. Crash remnants remain
DPAPI-encrypted and can be removed from `~/.local/state/hostbook/tmp` when the app
is closed. State and temporary directories use owner-only NTFS ACLs, checked through
Windows APIs; Unix `chmod` emulation is not treated as protection. Decrypted keys
used during import/edit/sharing still briefly exist in protected temporary files.

Windows uses the bundled Git OpenSSH agent, including its private fallback when
necessary. Native Windows OpenSSH and VS Code do not automatically share its Unix
socket. Use your normal external agent/key setup for those clients; `hostbook unlock`
does not configure the Windows ssh-agent service.

macOS uses Keychain. Linux prefers secret-service; if unavailable, the implementation
can use a permission-restricted runtime file. An unlocked session can therefore use
the credentials: treat access to your OS login and ssh-agent as access to your vault.
This is not a guarantee that unlocked secrets never exist in memory or runtime files.

## Master password and recovery

```bash
hostbook vault passwd
hostbook vault recover
```

Use a strong master password (at least ten characters). The recovery code can restore
access if you forget it. Neither an encrypted Git backup nor a share bundle should be
published together with its unlocking material.

## Trust boundaries

- SSH host verification still matters. A changed host key must be investigated.
- Connection checks may accept a previously unseen host key, but reject changed keys.
- `~/ssh-hosts` contains readable hostnames, addresses, users, and tags. Encryption protects vault secrets, not the entire host list.
- Share bundles include credentials by default; share only with intended recipients.
- Imported host names and repository content are validated. Keep SSH and Git up to date and review access to your remote.
