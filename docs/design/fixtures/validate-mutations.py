#!/usr/bin/env python3
"""Validate conceptual permission examples only; never load or run Hostbook."""
import json
import sys
import uuid
from pathlib import Path


USER = {'new', 'import', 'undo', 'vault-recovery', 'vault-bootstrap',
        'internal-callback', 'rollback', 'ordinary-edit', 'ordinary-publish', 'background-publish'}
PUBLISH = {'ordinary-publish', 'background-publish'}
LOCAL = {'derived', 'unlock-existing', 'local-settings'}
RESOURCES = {'directory', 'vault', 'unlock_cache', 'index_cache', 'writer_gate',
             'sync_status', 'connection', 'mode'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def decision(case, stores):
    op = case['operation']
    require(op in USER | LOCAL | {'receive'}, 'unknown operation')
    if 'destination' in case:
        require(case['destination'] is None and op in {'new', 'import'} and
                'store' not in case, 'only new/import have default routing')
    index = case.get('store', 0)
    require(type(index) is int and 0 <= index < len(stores), 'store index')
    store = stores[index]
    if case.get('captured_store', index) != index or case.get('snapshot', 'fresh') != 'fresh':
        return 'reject-snapshot'
    if case.get('resource_store', index) != index:
        return 'reject-resource'
    rights = case.get('rights', store['rights'])
    require(rights in {'none', 'read', 'read-write', 'unknown'}, 'rights')
    preflight = case.get('preflight', 'available')
    require(preflight in {'available', 'attempted-unavailable'}, 'preflight')
    if op in USER and store['role'] == 'shared':
        if not case.get('explicit_destination', False):
            return 'reject-destination'
        if store['mode'] != 'two-way':
            return 'reject-mode'
        if preflight == 'attempted-unavailable':
            return 'reject-offline'
        if rights != 'read-write':
            return 'reject-rights'
    if op == 'receive':
        require(store['role'] == 'shared', 'receive example must be shared')
        if rights not in {'read', 'read-write'}:
            return 'reject-rights'
    if op in PUBLISH:
        require(store['role'] == 'shared', 'publisher example must be shared')
        if case.get('fenced', False):
            return 'reject-fence'
        if not case.get('reservation', False):
            return 'reject-admission'
    return 'allow'


def validate(data):
    require(data['fixture_kind'] == 'documentation-only', 'documentation marker')
    require(data['contract_version'] == 1, 'conceptual version')
    stores = data['stores']
    require([s['role'] for s in stores] == ['private', 'shared', 'shared'], 'three store roles')
    require([s['mode'] for s in stores] == ['private', 'receive-only', 'two-way'], 'independent modes')
    ids, resources = set(), set()
    for store in stores:
        sid = store['store_id']
        require(str(uuid.UUID(sid)) == sid and sid not in ids, 'unique canonical store ID')
        ids.add(sid)
        if store['role'] == 'shared':
            require(store['default_mode'] == 'receive-only', 'shared default mode')
        require(set(store['resources']) == RESOURCES, 'resource ownership fields')
        for value in store['resources'].values():
            require(value and value not in resources, 'resources must be independent')
            resources.add(value)
    names = set()
    operations = set()
    for case in data['cases']:
        name = case['case']
        require(name not in names, 'duplicate case: ' + name)
        names.add(name)
        operations.add(case['operation'])
        require(decision(case, stores) == case['expected'], name + ': decision mismatch')
        if case.get('pending_before', False):
            require(case['pending_retained'] and not case['reported_published'],
                    name + ': preserve pending without publication claim')
    require({'new', 'import', 'undo', 'vault-recovery', 'internal-callback',
             'receive', 'derived', 'ordinary-publish'} <= operations, 'missing operation family')
    require({'reject-mode', 'reject-rights', 'reject-snapshot', 'reject-fence',
             'reject-offline', 'allow'} <=
            {c['expected'] for c in data['cases']}, 'missing decision family')
    return len(data['cases'])


if __name__ == '__main__':
    try:
        count = validate(json.loads(Path(__file__).with_name('mutation-permissions.json').read_text()))
    except (ValueError, KeyError, TypeError) as exc:
        print('FAIL: ' + str(exc), file=sys.stderr)
        sys.exit(1)
    print(f'PASS: {count} synthetic mutation permission cases; no runtime enforcement tested')
