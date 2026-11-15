# Secrets and security

## The vault and reusable credentials

The secret vault holds named SSH keys and passwords, encrypted with
[age](https://github.com/FiloSottile/age). It is created on first run (see
[Getting started](Getting-started)): you set a **vault protection password** (the master password,
at least ten characters) and save a **recovery code** that is shown once. This password is not a
server login password. A credential can be reused across hosts; key fingerprints prevent duplicate
imports.

Use `Ctrl-O` → **Secrets** (`Secret`), or:

```bash
hostbook secrets
```

The screen opens on saved entries and an **Add to vault…** action, which offers a key from a
file, a pasted key, a new generated key, or a server password. Original key files are never
changed or relied on: the vault keeps an encrypted copy, and canceling a source returns to the
vault list. For each secret you can show the servers that use it, copy the public key, replace the
key (from the clipboard, a file, or a new one), edit contents, change a password, rename it, or
delete it. After replacing a key hostbook can install the new key on the servers that use it and,
separately, remove the old one; removal cannot be undone automatically. **Secret vault** in the same
menu holds the master password, recovery code, and lock actions, and **Move this machine's keys
into the vault** moves keys that servers still read from files.

Secret names start with a letter or digit, use lowercase letters, digits and `. _ -`, are at most
40 characters, and must not be `vault` or start with `vault.` (those names are reserved).

Deleting a secret shows which servers lose their saved login and that the servers themselves stay.
If a server tagged `prod` uses it, you must type the exact secret name before it is deleted.

When adding or editing a host, the login step lists vault credentials first, then **Add to vault…**.
**No saved login** lets SSH use external agents and hardware-backed keys without storing a secret.

During first-run SSH-config migration, an external private key is saved to the vault only after you
confirm it; the original file stays. A key saved that way is committed independently and remains if you
later cancel the host import. Imported sources with unsupported inherited credentials, executable
Match rules, or Include directives are rejected before SSH examines them.

## Share packages

Only encrypted Hostbook packages (`.hbshare`) can be imported. A package is encrypted with its own
password and is validated before extraction: unexpected content and archive links are rejected, and
nothing reaches your hosts or vault until you finish the review. Updating or replacing a `prod`
server from a package requires typing its name. See [Sync and sharing](Sync-and-sharing).

## Unlocking and external SSH clients

The vault identity is protected by the master password. The recovery code is shown when
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
hostbook vault passwd         # change the master password
hostbook vault recover        # forgot it: use the recovery code, then set a new password
hostbook vault recovery-code  # create a new recovery code (the old one stops working)
```

The same actions are in **Secrets → Secret vault**. Use a strong master password (at least ten
characters). The recovery code looks like `HB-XXXX-XXXX-XXXX-XXXX-XXXX`; recovering issues a new code and the
old one stops working, so save the new one. Keep it in a password manager or on paper, apart from the vault. Neither an encrypted
Git backup nor a share package should be published together with its unlocking material.

## Trust boundaries

- SSH host verification still matters. A changed host key must be investigated.
- Connection checks may accept a previously unseen host key, but reject changed keys.
- `~/ssh-hosts` contains readable hostnames, addresses, users, and tags. Encryption protects vault secrets, not the entire host list.
- A share package can include credentials (you choose with or without secrets); share only with intended recipients and send its password through a different channel.
- Imported host names and repository content are validated. Keep SSH and Git up to date and review access to your remote.
