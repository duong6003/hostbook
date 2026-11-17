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
    return len(names)


if __name__ == '__main__':
    try:
        fixture = Path(__file__).with_name('transfer-recovery.json')
        count = validate(json.loads(fixture.read_text()))
    except (ValueError, KeyError, TypeError) as error:
        print('FAIL: ' + str(error), file=sys.stderr)
        sys.exit(1)
    print(f'PASS: {count} conceptual transfer checkpoints (documentation only; no runtime validation)')
