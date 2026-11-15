# Security policy

## Reporting a vulnerability

Do not disclose vulnerabilities, exploit details or credentials in public issues, pull requests or logs.

Use the repository's [Security advisories](https://github.com/duong6003/hostbook/security/advisories)
page and select **Report a vulnerability** when that option is available. This sends the report privately
to repository maintainers.

Private vulnerability reporting must be enabled separately in GitHub settings; this file does not enable it.
If the private reporting option is unavailable, contact the repository owner directly (the repository is
private, so even issues are visible to every collaborator). Ask only for a private security channel,
without naming the affected component, describing the vulnerability or attaching sensitive data.
Wait for that channel before sending the report.

Include in a private report:

- The affected commit SHA or version, operating system and relevant dependency versions.
- The triggering conditions, expected and actual behavior, and realistic impact.
- Minimal reproduction steps using synthetic hosts and disposable credentials.
- Relevant code locations, sanitized output and a possible fix if known.

Never send real private keys, passwords, recovery codes, vault identities or unredacted host inventories.
If a secret was exposed, revoke or rotate it; deleting the public text alone is not sufficient.

## Scope and maintenance

Security reports are welcome for Hostbook's SSH flows, encrypted vault, credential handling, Git sync,
import/export, installers, update process and platform helpers. Reports affecting older versions are
welcome too; identify the exact version and whether the issue also affects current main when safe to test.
There is no published long-term-support or backport matrix. Acknowledgment and remediation timing are
not guaranteed by this policy.

For issues in upstream dependencies, follow the upstream project's reporting policy. Privately notify
Hostbook maintainers as well when Hostbook's integration or distributed dependency is affected.

## Security boundaries

- Host inventories and standard SSH configuration are readable data. Vault encryption does not encrypt
  hostnames, addresses, usernames, tags or all repository metadata.
- An unlocked vault, SSH agent or compromised operating-system account can expose credentials.
  Protected caches and encryption at rest do not protect against a compromised unlocked session.
- Share packages may include credentials. Review the selected hosts and secrets, and send the package
  password through a separate trusted channel.
- SSH host verification remains important. Investigate changed host keys rather than disabling checks.
- Imported configuration and shared repositories require trust. SSH directives can invoke local
  commands; syntactic validity is not proof that configuration is safe.
- Install and update code only from sources you trust. Keep OpenSSH, Git and other dependencies updated.

See [Secrets and security](docs/wiki/Secrets-and-security.md) for the current platform-specific behavior.
These boundaries describe the threat model, not a reason to dismiss defects that violate documented protections.

## Safe investigation and remediation

Test only systems you own or are authorized to assess. Use disposable host lists, vaults, SSH agents,
configuration and servers. Follow the isolation rules in [AGENTS.md](AGENTS.md) and the
[review guidelines](docs/REVIEW_GUIDELINES.md).

Maintain confidentiality while validating a report and preparing a fix. Coordinate public disclosure
with maintainers so users can receive a fix and mitigation guidance. Avoid public reproductions or
patch descriptions that expose sensitive details before that coordination.
