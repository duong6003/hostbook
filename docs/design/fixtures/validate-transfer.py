#!/usr/bin/env python3
"""Check adjacent documentation checkpoints; never exercise Hostbook runtime."""
import ipaddress
import json
import sys
import uuid
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_barrier(case):
    """Check only conceptual local publication/transition barrier facts."""
    require(case['side'] in {'source', 'destination'}, 'barrier store side')
    require(case['requested_mode'] == 'receive-only', 'barrier requested mode')
    require(case['effective_mode'] in {'two-way', 'receive-only'}, 'barrier mode')
    require(case['transition'] in {'pending', 'failed', 'effective'}, 'barrier transition')
    reservation = case['reservation']
    require(reservation['status'] in {'in-flight', 'uncertain', 'settled-accepted',
                                      'settled-rejected'}, 'barrier attempt status')
    require(reservation['persisted_before_send'], 'admission-must-precede-send')
    require(case['admissions_fenced'], 'transition-must-fence-admissions')
    require(not case['new_publication_admitted'], 'new-admission-crosses-transition-fence')
    unsettled = reservation['status'] in {'in-flight', 'uncertain'}
    require(unsettled or reservation['publisher_quiescent'], 'settlement-requires-quiescence')
    require(not unsettled or reservation['retained'], 'unsettled-reservation-discarded')
    if unsettled:
        require(case['transition'] == 'pending' and case['effective_mode'] == 'two-way' and
                not case['reported_effective'] and case['effective_configuration_unchanged'],
                'effective-transition-with-unsettled-attempt')
    if case['transition'] == 'effective':
        require(reservation['status'] == 'settled-accepted' and
                reservation['publisher_quiescent'] and case['effective_mode'] == 'receive-only' and
                case['reported_effective'], 'effective-transition-requires-successful-drain')
    if reservation['status'] == 'settled-rejected':
        require(reservation['publisher_quiescent'] and case['transition'] == 'failed' and
                case['effective_mode'] == 'two-way' and not case['reported_effective'] and
                case['effective_configuration_unchanged'], 'failed-send-must-preserve-mode')


def existing_destination_decision(case):
    observed, attempt = case['observed'], case['attempt']
    review = case.get('review', 'unreviewed')
    require(observed in {'matching', 'different-revision', 'inconsistent-revision', 'other-id-alias'},
            'existing destination observation')
    require(attempt in {'none', 'unsettled', 'reachable-only', 'settled-accepted', 'settled-rejected'},
            'existing destination attempt')
    require(review in {'unreviewed', 'confirmed-current', 'confirmed-stale'}, 'replacement review')
    if attempt in {'unsettled', 'reachable-only'}:
        return 'reconcile-attempt'
    if observed == 'matching' and attempt == 'settled-accepted':
        return 'revalidate-destination-effect'
    if observed == 'other-id-alias':
        return 'resolve-name-collision'
    if observed == 'different-revision':
        if case.get('lineage_verified', False) is True and review == 'confirmed-current':
            return 'prepare-same-id-replacement'
        return 'compare-versions'
    return 'reconcile-evidence'


def source_reference_decision(case):
    jump, groups = case['jump'], case['groups']
    inputs = case.get('inputs', 'current')
    require(jump in {'unchanged', 'missing', 'retargeted', 'unverified'}, 'source jump result')
    require(groups in {'none', 'reviewed', 'unreviewed', 'ambiguous'}, 'source group review')
    require(inputs in {'current', 'prepared-removal', 'changed'}, 'source input freshness')
    if inputs == 'changed':
        return 'refresh-plan'
    if jump != 'unchanged':
        return 'block-dependent-host'
    if groups == 'ambiguous':
        return 'reconcile-group-binding'
    if groups == 'unreviewed':
        return 'review-groups'
    return 'revalidate-removal'


def validate(data):
    require(data['fixture_kind'] == 'documentation-only', 'documentation marker')
    require(data['contract_version'] == 1, 'unsupported conceptual version')
    for key in ('transfer_id', 'source_store_id', 'destination_store_id', 'host_id'):
        require(str(uuid.UUID(data[key])) == data[key], 'noncanonical ' + key)
    require(data['source_store_id'] != data['destination_store_id'], 'distinct stores')
    require(data['source_role'] == data['destination_role'] == 'shared', 'shared examples')
    require(data['recovery'] == 'vault-protected-original-retained', 'protected recovery')
    address = ipaddress.ip_address(data['connection_settings']['address'])
    require(address in ipaddress.ip_network('192.0.2.0/24'), 'documentation address')
    require(data['connection_settings']['ProxyJump'] == 'example-jump', 'jump preserved')
    names = set()
    checkpoints = {'prepared', 'destination-saved', 'destination-confirmed',
                   'source-removed-local', 'source-confirmed', 'completed'}
    publications = {'not-attempted', 'pending', 'uncertain', 'accepted', 'rejected'}
    observations = {'absent', 'matching', 'changed', 'unknown'}
    actions = {'retry-encryption', 'none', 'review-mode',
               'reconcile-destination-publication', 'revalidate-and-remove-source',
               'compare-conflict', 'review-compensation',
               'revalidate-and-publish-source', 'retry-or-restore-source',
               'reconcile-source-publication', 'revalidate-and-complete',
               'report-without-replay'}
    for case in data['cases']:
        name = case['case']
        require(name not in names, 'duplicate case: ' + name)
        names.add(name)
        stage, dest, source, expected = (case[k] for k in
            ('checkpoint', 'destination', 'source', 'expected'))
        require(stage in checkpoints, name + ': checkpoint')
        require(expected['next'] in actions, name + ': action')
        require(expected['outcome'] in {'blocked', 'recoverable', 'cancelled', 'completed'},
                name + ': outcome')
        require(expected['retry_host_id'] == data['host_id'], name + ': retry identity')
        require(expected['destination_host_count_max'] == 1, name + ': no duplicate')
        require(type(dest['durable']) is bool and type(dest['verified']) is bool,
                name + ': destination flags')
        require(type(expected['may_remove_source']) is bool, name + ': removal flag')
        require(not dest['verified'] or dest['durable'], name + ': verify after save')
        require(source['local'] in {'present', 'absent', 'changed'}, name + ': local source')
        for side in (dest, source):
            require(side['publication'] in publications, name + ': publication')
            require(side['observed'] in observations, name + ': observation')
        require(all(mode in {'two-way', 'receive-only'} for mode in case['modes'].values()),
                name + ': mode')
        usable_destination = (dest['durable'] and dest['verified'] and
                              dest['publication'] == 'accepted' and
                              dest['observed'] == 'matching')
        if stage in {'destination-confirmed', 'source-removed-local', 'source-confirmed', 'completed'}:
            require(dest['durable'] and dest['verified'] and dest['publication'] == 'accepted',
                    name + ': historical destination confirmation')
        if source['local'] == 'absent':
            require(stage in {'source-removed-local', 'source-confirmed', 'completed'},
                    name + ': source deletion checkpoint')
        if expected['may_remove_source']:
            require(stage == 'destination-confirmed' and usable_destination and
                    source['local'] == 'present' and source['observed'] == 'matching' and
                    all(mode == 'two-way' for mode in case['modes'].values()) and
                    case['signal'] != 'cancel', name + ': unsafe source removal')
        if stage in {'source-confirmed', 'completed'}:
            require(source['local'] == 'absent' and source['publication'] == 'accepted',
                    name + ': required source confirmation')
        if expected['outcome'] == 'completed':
            require(stage == 'completed' and usable_destination and
                    source['local'] == 'absent' and source['publication'] == 'accepted' and
                    source['observed'] == 'absent' and
                    expected['next'] == 'report-without-replay', name + ': false completion')
        if any(side['publication'] in {'pending', 'uncertain', 'rejected'} for side in (dest, source)):
            require(expected['outcome'] == 'blocked' and not expected['may_remove_source'],
                    name + ': publication failure must block')
        if dest['observed'] == 'changed' or source['observed'] == 'changed':
            require(expected['outcome'] == 'blocked' and expected['next'] == 'compare-conflict' and
                    not expected['may_remove_source'], name + ': changed content must block')
        if case['signal'] == 'cancel':
            require(not expected['may_remove_source'] and
                    expected['outcome'] in {'cancelled', 'recoverable'}, name + ': cancellation')
            if dest['publication'] == 'accepted':
                require(expected['next'] == 'review-compensation', name + ': remote cancellation')
    required = {'encryption-failed', 'cancel', 'mode-changed', 'offline', 'rejected',
                'response-lost', 'restart', 'remote-edit', 'same-id-conflict'}
    require(required <= {case['signal'] for case in data['cases']}, 'missing failure coverage')
    require(checkpoints <= {case['checkpoint'] for case in data['cases']}, 'missing checkpoint')
    barriers = data['publication_barriers']
    require({case['side'] for case in barriers} == {'source', 'destination'},
            'both publication sides covered')
    require({'in-flight', 'uncertain', 'settled-accepted', 'settled-rejected'} <=
            {case['reservation']['status'] for case in barriers}, 'barrier outcome coverage')
    for case in barriers:
        validate_barrier(case)
    negatives = data['invalid_publication_barriers']
    require({case['expected_rejection'] for case in negatives} ==
            {'effective-transition-with-unsettled-attempt', 'unsettled-reservation-discarded',
             'new-admission-crosses-transition-fence'}, 'barrier counterexample coverage')
    for invalid in negatives:
        try:
            validate_barrier(invalid['example'])
        except ValueError as error:
            require(str(error) == invalid['expected_rejection'], 'wrong counterexample rejection')
        else:
            raise ValueError('accepted invalid barrier: ' + invalid['expected_rejection'])
    existing = data['existing_destination']
    decisions = set()
    for case in existing:
        decision = existing_destination_decision(case)
        require(decision == case['expected'], case['case'] + ': existing destination decision')
        decisions.add(decision)
    require(decisions == {'reconcile-attempt', 'revalidate-destination-effect',
                          'resolve-name-collision', 'prepare-same-id-replacement',
                          'compare-versions', 'reconcile-evidence'}, 'existing destination coverage')
    references = data['source_references']
    for case in references:
        require(source_reference_decision(case) == case['expected'], case['case'] + ': source reference decision')
    require({case['expected'] for case in references} ==
            {'refresh-plan', 'block-dependent-host', 'review-groups',
             'reconcile-group-binding', 'revalidate-removal'},
            'source reference coverage')
    return len(names), len(barriers), len(negatives), len(existing), len(references)


if __name__ == '__main__':
    try:
        fixture = Path(__file__).with_name('transfer-recovery.json')
        count, barriers, negatives, existing, references = validate(json.loads(fixture.read_text()))
    except (ValueError, KeyError, TypeError) as error:
        print('FAIL: ' + str(error), file=sys.stderr)
        sys.exit(1)
    print(f'PASS: {count} transfer checkpoints, {barriers} publication barriers, '
          f'{negatives} rejected counterexamples, {existing} existing-destination decisions, {references} source-reference checks '
          '(documentation only; no runtime validation)')
