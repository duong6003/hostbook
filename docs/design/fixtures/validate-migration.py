#!/usr/bin/env python3
"""Check synthetic migration decisions only; no application or external effects."""
import json
import sys
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def classify(case):
    remote, vault = case['remote'], case['vault']
    require(remote in {'none', 'verified', 'unreachable', 'ambiguous'}, 'remote state')
    require(vault in {'absent', 'shared-existing', 'independent', 'locked', 'partial',
                      'complete'}, 'vault state')
    require(type(case['pending']) is bool, 'pending state')
    require(case['identity'] in {'unresolved-unambiguous', 'history-mismatch',
                                'unknown-version', 'ambiguous-mapping'}, 'identity state')
    if remote == 'unreachable':
        return 'stop-remote-discovery'
    if remote == 'ambiguous':
        return 'stop-target-choice'
    if case['identity'] != 'unresolved-unambiguous':
        return 'stop-identity-reconciliation'
    if case['pending']:
        return 'stop-work-reconciliation'
    if vault in {'independent', 'locked', 'partial'}:
        return {'independent': 'stop-vault-reconciliation', 'locked': 'stop-unlock',
                'partial': 'stop-vault-recovery'}[vault]
    if remote == 'verified' and vault == 'shared-existing':
        return 'unlock-existing-shared'
    return 'private-candidate' if remote == 'none' else 'shared-candidate'


def upgrade(case):
    kind, local, remote = case['kind'], case['legacy_local'], case['legacy_remote']
    require(kind in {'fresh', 'legacy'}, 'upgrade kind')
    require(local in {'never-exposed', 'kernel-denied', 'writable', 'unknown'}, 'local writer evidence')
    require(remote in {'verified-excluded', 'writable', 'unknown', 'private'}, 'remote writer evidence')
    require(case['backup'] in {'not-applicable', 'present', 'restored-in-staging'}, 'backup evidence')
    require(case['prior_sends'] in {'none', 'settled', 'uncertain'}, 'prior sender state')
    for field in ('disjoint_checkout', 'binding_current', 'identity_ready', 'legacy_quiescent'):
        require(type(case[field]) is bool, 'upgrade field: ' + field)
    if not case['disjoint_checkout'] or not case['binding_current']:
        return 'stop-target-binding'
    required_local = 'never-exposed' if kind == 'fresh' else 'kernel-denied'
    if local != required_local or remote not in {'verified-excluded', 'private'} or not case['legacy_quiescent']:
        return 'stop-writer-access'
    if case['prior_sends'] == 'uncertain':
        return 'stop-publication-reconciliation'
    if not case['identity_ready']:
        return 'stop-identity-admission'
    if kind == 'fresh':
        require(remote == 'verified-excluded' and case['backup'] == 'not-applicable',
                'fresh shared connection only')
        return 'connect-receive-only'
    if case['backup'] != 'restored-in-staging':
        return 'stop-backup-verification'
    return 'eligible-for-handoff'


def validate(data):
    require(data['fixture_kind'] == 'documentation-only' and
            data['contract_version'] == 1, 'conceptual fixture marker/version')
    names = set()
    for case in data['classification'] + data['interruptions']:
        require(case['case'] not in names, 'duplicate case')
        names.add(case['case'])
    require(8 <= len(names) <= 12, 'bounded independent example count')
    for case in data['classification']:
        require(classify(case) == case['expected'], case['case'] + ': decision')
    by_name = {case['case']: case for case in data['classification']}
    require(by_name['remote-shared-vault-found-first']['private_vault'] == 'separate' and
            not by_name['remote-shared-vault-found-first']['may_create_shared_vault'],
            'existing shared identity must precede creation')
    require(not by_name['offline-is-still-shared']['ordinary_pending_exception'] and
            by_name['independently-created-vaults']['both_originals_retained'] and
            not by_name['required-vault-locked']['reencryption_allowed'] and
            not by_name['partial-envelopes']['may_create_vault'] and
            by_name['staged-and-untracked-pending']['pending_retained'] and
            not by_name['same-id-divergent-history']['automatic_merge'], 'classification safety')
    candidate = by_name['no-remote-new-private']
    for identity in ('history-mismatch', 'unknown-version', 'ambiguous-mapping'):
        require(classify(dict(candidate, identity=identity)) ==
                'stop-identity-reconciliation', 'unsafe identity candidate')
    expected_events = {
        'encryption-failed': ('staged', 'retain-original-and-stage-evidence'),
        'lost-acknowledgement': ('publication-pending', 'reconcile-exact-remote-attempt'),
        'newer-work': ('rollback-pending', 'stop-compare-and-reconcile')}
    require({c['event'] for c in data['interruptions']} == set(expected_events),
            'interruption coverage')
    for case in data['interruptions']:
        require((case['checkpoint'], case['expected']) == expected_events[case['event']],
                case['case'] + ': boundary/decision')
        require(case['original_retained'] and case['backup_verified'], 'retain recovery')
        if case['event'] == 'encryption-failed':
            require(not case['promoted'], 'failed encryption cannot promote')
        elif case['event'] == 'lost-acknowledgement':
            require(case['reservation_retained'] and case['publisher_quiescent'] and
                    case['remote_outcome'] == 'uncertain' and not case['reported_complete'],
                    'quiescence alone cannot settle a lost acknowledgement')
        else:
            require(not case['overwrite_allowed'] and not case['reported_rolled_back'],
                    'rollback must not clobber newer work')
    gates = data['mutation_preflight']
    required = {'backup_verified', 'snapshot_fresh', 'writers_isolated',
                'publications_drained', 'required_vaults_unlocked',
                'explicit_shared_destination', 'effective_two_way', 'actual_write_rights'}
    require(set(gates) == required and all(v is True for v in gates.values()),
            'all preconditions required before shared migration mutation')
    # Independent counterexamples: no ordinary-edit outage exemption here.
    for gate in required:
        changed = dict(gates, **{gate: False})
        require(not all(changed.values()), 'missing gate admitted: ' + gate)
    handoff = data['migration_handoff']
    drain, cutover, reserve, lost = handoff['sequence']
    require([s['step'] for s in handoff['sequence']] ==
            ['drain', 'cutover', 'reserve', 'lost-ack'], 'handoff sequence')

    def can_handoff(state):
        return (state['guard'] == 'transition-fence' and state['old_quiescent'] and
                state['old_reconciled'] and not state['new_admission'])

    authority = handoff['authority']
    require(set(authority['effects']) == {'write-identity', 'publish-candidate'},
            'scoped effects')

    def can_mutate(state):
        return (state['guard'] == 'migration-owned' and state['actor'] == 'migration' and
                state['intent'] == handoff['intent'] and state['target'] == handoff['target'] and
                state['generation'] == handoff['new_generation'] and
                all(state[key] == authority[key] for key in ('owner', 'checkout', 'base', 'git_identity')) and
                state['effect'] in authority['effects'] and state['authority_current'] and
                state['rights'] == 'verified' and state['snapshot_fresh'])

    def can_reserve(state):
        return can_mutate(state) and state['effect'] == 'publish-candidate' and state['reservation_durable']

    local_write = dict(reserve, effect='write-identity', reservation_durable=False)
    require(can_handoff(drain) and cutover['guard'] == 'migration-owned' and
            cutover['handoff_durable'] and cutover['generation'] == handoff['new_generation'] and
            cutover['regular_mode'] == 'receive-only' and can_mutate(local_write) and
            can_reserve(reserve) and not can_reserve(local_write), 'durable scoped handoff/admission')
    require(lost['guard'] == 'migration-owned' and lost['ownership_retained'] and
            lost['reservation_retained'] and lost['outcome'] == 'uncertain' and
            not lost['reported_complete'], 'lost acknowledgement retains ownership')
    require(set(handoff['rejected']) == {'premature-handoff', 'ordinary-publisher'},
            'handoff counterexamples')
    for changed in ({'old_quiescent': False}, {'old_reconciled': False}):
        require(not can_handoff(dict(drain, **changed)), 'premature handoff admitted')
    rejected_admissions = (
        {'actor': 'ordinary'}, {'guard': 'transition-fence'}, {'intent': 'other-intent'},
        {'target': 'other-repo/same-ref'}, {'target': 'same-repo/other-ref'}, {'generation': 1},
        {'owner': 'other-owner'}, {'checkout': 'other-checkout'}, {'base': 'other-tip'}, {'git_identity': 'other-credential'},
        {'effect': 'write-unreviewed-vault'}, {'authority_current': False},
        {'rights': 'denied'}, {'rights': 'unknown'}, {'rights': 'unavailable'},
        {'snapshot_fresh': False},
    )
    for changed in rejected_admissions:
        require(not can_mutate(dict(local_write, **changed)) and not can_reserve(dict(reserve, **changed)),
                'unowned migration effect admitted: ' + str(changed))
    require(not can_reserve(dict(reserve, reservation_durable=False)), 'send without reservation')
    claims = data['resource_claims']
    unresolved = claims['unresolved']
    registrations = claims['later_registrations']
    for registration in registrations:
        require(registration['store'] != unresolved['store'], 'cross-ID claim coverage')
        overlap = any(registration[key] == unresolved[key] for key in ('checkout', 'target'))
        decision = 'reject-overlap' if overlap else 'allow-review'
        require(decision == registration['expected'], 'unresolved claim bypassed')
    fresh, eligible, blocked = cases = data['upgrade']
    require([c['expected'] for c in cases] ==
            ['connect-receive-only', 'eligible-for-handoff', 'stop-writer-access'],
            'upgrade outcome coverage')
    for case in cases:
        require(upgrade(case) == case['expected'], case['case'] + ': upgrade decision')
    require(upgrade(dict(fresh, identity_ready=False)) == 'stop-identity-admission',
            'fresh receive-only connection cannot initialize identity')
    for changes, expected in (
            ({'disjoint_checkout': False}, 'stop-target-binding'),
            ({'binding_current': False}, 'stop-target-binding'),
            ({'legacy_local': 'never-exposed'}, 'stop-writer-access'),
            ({'legacy_local': 'unknown'}, 'stop-writer-access'),
            ({'legacy_remote': 'writable'}, 'stop-writer-access'),
            ({'legacy_remote': 'unknown'}, 'stop-writer-access'),
            ({'legacy_quiescent': False}, 'stop-writer-access'),
            ({'backup': 'present'}, 'stop-backup-verification'),
            ({'prior_sends': 'uncertain'}, 'stop-publication-reconciliation')):
        require(upgrade(dict(eligible, **changes)) == expected, 'unsafe upgrade admitted: ' + str(changes))
    return len(names), len(required), len(cases), len(rejected_admissions), len(registrations)


if __name__ == '__main__':
    try:
        fixture = Path(__file__).with_name('migration-decisions.json')
        count, gates, upgrades, rejected_admissions, claims = validate(json.loads(fixture.read_text()))
    except (ValueError, KeyError, TypeError) as error:
        print('FAIL: ' + str(error), file=sys.stderr)
        sys.exit(1)
    print(f'PASS: {count} synthetic migration cases, {gates} preflight counterexamples, '
          f'4 handoff checkpoints, {rejected_admissions} rejected scoped admissions, {claims} resource claim checks and {upgrades} upgrade decisions '
          '(documentation only; no runtime validation)')
