# Store identity format and legacy admission

Status: **proposed**, documentation only; fifth bounded [#35](https://github.com/duong6003/hostbook/issues/35)
slice under [#33](https://github.com/duong6003/hostbook/issues/33). This chooses an
identity representation and admission policy, extending [identity](store-identity.md),
[transfer](store-transfer.md), [mutation ownership](store-mutations.md) and
[migration](store-migration.md). It does not implement them or complete #35.

## Decision and physical boundary

| Representation | Benefit | Association failure / cost |
| --- | --- | --- |
| Inline SSH comments | IDs can travel beside edited blocks; SSH ignores comments. | Stripped/copied comments lose or duplicate associations; repeated aliases and multi-alias blocks need additional record/reference rules. Old writers ignore comments. |
| One store sidecar | SSH bytes remain unchanged; one complete record per selectable alias can bind every contributing block. | File edits/moves require an association review; copying only SSH files loses identity. Old writers ignore the sidecar. |

Choose **one tracked UTF-8 JSON sidecar, `.hostbook/identity.json`**, relative to the
store checkout root. SSH files contain no required identity comments or generated
aliases. Do not flatten, split, reorder or rewrite SSH blocks to attach identities.
The sidecar is authoritative identity data, not a regenerable index. Its loss is
an unresolved store, not an invitation to assign IDs again. This choice deliberately
requires reviewed metadata updates after manual SSH edits; no heuristic matching.

The private store and every independent shared store have their own sidecar and
vault. Device registration selects exactly one active checkout per store ID;
duplicate registrations require explicit selection/reconciliation. State, caches,
locks, status, connections and modes are separately scoped by store ID (and
checkout/connection generations where required). No process-global singleton
store, shared vault or alias-only state key is implied. Registration/permission
encoding and concrete filesystem/platform isolation remain future work.

## Exact v1 data and validation

The top-level object has exactly `format`, `version`, `store_id`, `generation`,
`sources`, `hosts`. `format` is `"hostbook-store"`; `version` is integer `1`;
`generation` is a positive integer incremented by each admitted metadata change.
It is a snapshot discriminator, never a substitute for current-file/ref checks.
JSON must have unique object keys; unknown fields, wrong types, duplicate array
members where uniqueness is required and trailing non-JSON data are invalid.
A compatible extension needs a newly supported version, not ignored required data.

`store_id` and every `host_id` are independently generated UUIDv4 values persisted
once, in lowercase `8-4-4-4-12` hexadecimal text with RFC variant/version bits.
Never derive them from aliases, paths, URLs, addresses, file order or hashes.
Store ID survives checkout relocation, remote URL/label changes and role changes;
another checkout of that store keeps it. An explicitly independent store gets a
new store ID through reviewed creation/fork admission, never by loading a copy.
The lookup key remains **(store_id, host_id)**; equal aliases across stores are valid.

Each `sources` entry has exactly `path` and `sha256`: a unique case-sensitive
checkout-relative regular-file path and the lowercase 64-hex SHA-256 of its exact
bytes. Paths use `/`, with no empty, `.` or `..` components, absolute form or
symlink escape. Reject platform path collisions before admission. These digests
are association/revalidation guards, not identity or authenticity proofs, and
contain no secret-derived values. Managed source files must contain no credentials;
secret-bearing legacy content needs protected migration rather than this sidecar.

Each `hosts` entry has exactly `host_id`, `name`, `bindings`. `name` is one positive
concrete SSH alias in `A-Za-z0-9._-`, retaining display spelling; compare names by
ASCII case folding. IDs and normalized names are each unique within the store.
`bindings` is a nonempty array of objects with exactly `path`, `host_ordinal`,
`token_ordinal`: references to an inventoried source, its 1-based `Host` header
in source order and its 1-based pattern token in that header. Count all Host
headers/tokens, including wildcard/negated tokens; do not count `Match` as Host.
A binding must select a positive concrete token equal to `name` after case folding.
No token occurrence can bind twice. Every eligible positive concrete token in
the admitted managed file set binds exactly once, including repeated occurrences.
No line number, ordinal or digest may manufacture or recover a missing identity.

Synthetic complete shape (the digest is illustrative, not a verified file hash):

```json
{"format":"hostbook-store","version":1,
 "store_id":"11111111-1111-4111-8111-111111111111","generation":1,
 "sources":[{"path":"team.conf","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}],
 "hosts":[
  {"host_id":"22222222-2222-4222-8222-222222222222","name":"web",
   "bindings":[{"path":"team.conf","host_ordinal":1,"token_ordinal":1},
               {"path":"team.conf","host_ordinal":2,"token_ordinal":1}]},
  {"host_id":"33333333-3333-4333-8333-333333333333","name":"web-admin",
   "bindings":[{"path":"team.conf","host_ordinal":1,"token_ordinal":2}]}
 ]}
```

This represents `Host web web-admin` followed later by `Host WEB`; both original
blocks remain intact. Real admission verifies current source bytes and bindings.

## SSH granularity and manual changes

One identity represents **one concrete selection name**, not a physical stanza or
an inferred endpoint. `Host web web-admin` therefore has two records: selecting
these names can differ through `%n`, patterns or Match. Repeated `Host web` / `Host
WEB` occurrences contribute to the same case-insensitive SSH selection, so the
single `web` record explicitly binds *all* occurrences; it does not discard any
block or create competing same-name search rows. Admission shows this mapping for
review. Distinct names with the same address remain distinct records, never
collapsed because their endpoint/settings happen to match.

Wildcard and negated patterns, `Match`, defaults, Include ordering and arbitrary
ordinary SSH directives remain configuration, with no additional host IDs.
For example, `Host * !web`, `Host web !web` and `Match host web` retain their
normal SSH effects; metadata never turns an excluded token into a matching block.
Concrete tokens outside the managed name grammar remain unchanged legacy SSH
input; do not silently sanitize or expose them as identity-backed records.
Inventory Include/external dependencies without executing them; preserve them.
If their relationship to a proposed managed selection cannot be established,
stop that admission for explicit reconciliation. The concrete SSH resolver and
platform path handling must still be designed; this is no promise of a parser.
No new store-specific ProxyJump restriction or automatic dependency transfer is
introduced. Selecting `(store_id, host_id)` must preserve existing effective SSH
semantics, including jump resolution, as required by the identity contract.

A manual rename preserves the ID when the corresponding `name`, every affected
binding and source digest are explicitly updated into a valid association. A
reviewed file move similarly updates paths/bindings, preserving IDs. Unchanged
aliases, addresses, matching hashes or nearby ordinals alone never prove an
association after source changes. Any digest mismatch, missing source, new
unmapped occurrence, copied ID, stripped sidecar or ambiguous moved file blocks
identity-dependent actions on that store until explicit review/repair. A copied
record is a duplicate-ID error, even if byte-identical. Copying an SSH block is
an association change requiring review, even if it repeats the same alias.
Repair may restore metadata or explicitly admit an independent record with a new
ID and valid name; reading never performs either. Show conflicting locations and
recovery/compare guidance; do not pick a first record or overwrite existing data.

## Versions, admission and old writers

| Reader/writer | Legacy, no sidecar | Valid v1 | Future/unknown version or malformed metadata |
| --- | --- | --- | --- |
| Existing binary | Existing alias-based behavior. | Ignores sidecar; may edit SSH and leave stale associations. No promised rejection. | Also ignores metadata; no promised rejection or safety. |
| Identity-aware reader | Inventory only; explicit legacy admission required for identity-backed use. | Validate full sidecar, source set and associations before identity-backed use. | Fail closed for affected store; preserve data, identify version/error, request compatible upgrade or reviewed recovery. |
| Identity-aware writer | No automatic initialization; reviewed admission with USER guards. | Revalidate snapshots and USER/RECEIVE boundaries before effects. | No writes, downgrade, metadata stripping or ID regeneration. |

An unsupported store must not prevent unrelated valid private/shared stores from
being used. No fallback to another store/alias is permitted. SSH itself ignores
the sidecar and can still read the original files; that fact is not identity-aware
application compatibility or permission to bypass an admission failure.

Before exposing v1 to any writer, satisfy [migration writer isolation and
quiescence](store-migration.md#writers-ordered-gates-and-upgrade-boundary), including
other devices and publication paths. A new marker/lock cannot make an old binary
reject data. Concrete enforceable isolation/upgrade/restore mechanisms remain
unselected; if they cannot be established, stop before cutover and keep the original.
Admission inventories/classifies first, verifies protected backup/restore and
reviews the complete alias-to-occurrence mapping, then generates/persists IDs once.
Detect existing remote vault identity before any shared vault creation; independent
local/remote vaults require the migration contract's explicit reconciliation.

All metadata creation, association repair, rename and legacy admission mutations
are USER writes under [mutation guards](store-mutations.md); shared targets need
effective two-way mode and actual rights. Receive-only may apply validated remote
v1 content as RECEIVE, never mint IDs or repair metadata locally. Default imports
are private; new shared registrations are receive-only. Changed inputs invalidate
the plan. SSH file(s), sidecar and vault updates use existing [transfer revalidation](store-transfer.md#serialization-and-revalidation)
and [migration backup/recovery](store-migration.md#preflight-snapshot-and-protected-backup)
requirements; multiple file replacements are not one atomic transaction. Preserve
intent/recovery and expose partial failure instead of claiming an atomic rename.

Existing v1 `.hbshare` validation remains mandatory and its schema unchanged.
Genuine new identity-less imports enter private-by-default admission with fresh
IDs after review; known metadata loss instead requires identity repair/restoration.
Neither is recognized as a known transfer retry by name/content.
Legacy export can omit identity only after explaining loss of lineage and retry
recognition. A future identity-bearing package needs separate negotiated format
and old-reader handling; do not add unrecognized members to existing packages.

## Ten synthetic decisions and validation limits

All values above and cases below are documentation-only; no app/SSH/Git execution.

| Case | Required decision |
| --- | --- |
| 1. `Host web web-admin`, later `Host WEB` | Two IDs; web binds both contributors, web-admin one; preserve block bytes/order. |
| 2. Private `web`, shared A `web`, shared B `web` | Three valid complete lookup keys; isolated vault/cache/lock/status/mode resources. |
| 3. Sidecar and source both rename web → api | Valid reviewed association preserves web's host ID; revalidate every contributor. |
| 4. File moved but sidecar still points to old path | Stop association use; explicit move/repair preserves IDs after comparison. |
| 5. Duplicate host ID, or two records named web/WEB | Invalid store; expose both records; no first-match choice or minted replacement. |
| 6. UUID uppercase/malformed, missing field, unknown field/version | Fail closed with exact error/upgrade guidance; unrelated stores still usable. |
| 7. SSH file edited or copied; digest/coverage differs | Stop and review full association change; hashes/aliases never recover IDs. |
| 8. Sidecar stripped, or genuine legacy SSH import | Known metadata loss requires repair; new legacy import uses reviewed private admission, no retry inference. |
| 9. `Host * !web`; `Match host web`; `ProxyJump jump` | Retain ordinary SSH semantics; no wildcard/Match IDs or automatic jump transfer. |
| 10. Old writer still active; shared registration receive-only | No cutover until enforceable isolation; no local identity repair/bootstrap under receive-only. |

Validate from repository root (local documentation only):

```bash
git diff --check -- docs/design/store-*.md
python3 - <<'PY'
import json, pathlib, re
root = pathlib.Path('docs/design')
for p in root.glob('store-*.md'):
    for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', p.read_text()):
        if '://' not in target:
            assert (p.parent / target.split('#')[0]).exists(), (p, target)
s = (root / 'store-format.md').read_text()
assert len(re.findall(r'^\| \d+\.', s, re.M)) == 10
sample = json.loads(re.search(r'```json\n(.*?)\n```', s, re.S)[1])
assert sample['version'] == 1 and len(sample['hosts']) == 2
print('store format documentation: PASS (links, 10 cases, JSON shape)')
PY
```

Broad runtime suites provide no evidence for an unimplemented format.
These examples are decision cases, not a parser,
crash-safety proof or complete parent acceptance map. Remaining #35 work includes
registration/permission representation,
lineage/update-existing, concrete writer isolation/restore, platform mechanisms,
SSH resolver and full acceptance mapping. The [journal/retention proposal](store-journal.md)
selects device-local recovery encoding and collection. Downstream #36–#46 own runtime work.
