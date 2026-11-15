#!/usr/bin/env python3
"""Measure one-key pointer response in isolated, committed application snapshots.

Run in a quiet window: python3 tests/pointer-performance.py --output pointer.json
Use --warmups 0 for the first-input pass and the default 3 for warm navigation.
"""
import argparse
import json
import math
import os
from pathlib import Path
import platform
import re
import shlex
import signal
import statistics
import subprocess
import sys
import tempfile
import time


def run(args, **kwargs):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, **kwargs)


def summary(values):
    ordered = sorted(values)
    return dict(median_ms=round(statistics.median(values), 3),
                p95_ms=round(ordered[math.ceil(len(values) * .95) - 1], 3),
                min_ms=round(min(values), 3), max_ms=round(max(values), 3))


def pointer_from(capture):
    for row, line in enumerate(capture.splitlines()):
        host = re.search(r'sample-\d{4}', line)
        if '▌' in line and host:
            return row, host.group()
    return None


def fixture_env(root):
    # Start fresh so personal agents, Git settings and app overrides cannot reach the fixture.
    return dict(HOME=str(root / 'home'), HOSTBOOK_DIR=str(root / 'hosts'),
                HOSTBOOK_STATE=str(root / 'state'),
                HOSTBOOK_SSH_CONFIG=str(root / 'ssh_config'),
                XDG_CONFIG_HOME=str(root / 'config'),
                HOSTBOOK_KEYCHAIN='file:' + str(root / 'keychain/id'),
                HOSTBOOK_LANG='vi', USER='fixture',
                PATH=str(root / 'bin') + ':' + os.environ.get('PATH', os.defpath),
                SHELL='/bin/bash', LC_ALL='en_US.UTF-8', COLUMNS='100', LINES='24',
                TERM='xterm-256color', GIT_AUTHOR_NAME='fixture',
                GIT_AUTHOR_EMAIL='fixture@example.com', GIT_COMMITTER_NAME='fixture',
                GIT_COMMITTER_EMAIL='fixture@example.com',
                GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null')


def materialize(repo, revision, target):
    target.mkdir()
    archive = target.parent / (target.name + '.tar')
    with archive.open('wb') as output:
        subprocess.run(['git', '-C', str(repo), 'archive', revision],
                       stdout=output, check=True)
    run(['tar', '-xf', str(archive), '-C', str(target)])


def fixture(root, revision, app, count, samples, warmups):
    for folder in ('home/.ssh', 'hosts', 'state', 'config/hostbook', 'bin'):
        (root / folder).mkdir(parents=True)
    clipboard = root / 'bin/pbpaste'
    clipboard.write_text('#!/bin/sh\nexit 1\n')
    clipboard.chmod(0o755)
    hosts = root / 'hosts'
    (hosts / 'sample.conf').write_text(''.join(
        f'# tags: staging api\nHost sample-{i:04d}\n  HostName 192.0.2.{i % 250 + 1}\n  User fixture\n'
        for i in range(count)))
    (root / 'ssh_config').write_text(f'Include "{hosts}/*.conf"\n')
    (root / 'config/hostbook/config').write_text(
        'list = compact\nlang = vi\npreview = right\nborder = rounded\n')
    env = fixture_env(root)
    for args in (['git', 'init', '-q', str(hosts)],
                 ['git', '-C', str(hosts), 'add', '.'],
                 ['git', '-C', str(hosts), 'commit', '-qm', 'Pointer fixture'],
                 ['/bin/bash', str(app / 'hostbook'), '_index']):
        run(args, env=env, cwd=root, stdin=subprocess.DEVNULL)
    prefix = ['tmux', '-S', str(root / 'tmux.sock')]

    def tmux(*args):
        return run(prefix + list(args), env=env, cwd=root).stdout.decode()

    def pointer():
        start = time.perf_counter()
        capture = tmux('capture-pane', '-p', '-t', 'ui:0.0')
        return pointer_from(capture), capture, (time.perf_counter() - start) * 1000

    groups = []
    measured = []
    try:
        command = 'exec env -i ' + ' '.join(shlex.quote(k + '=' + v) for k, v in env.items())
        command += ' /bin/bash ' + shlex.quote(str(app / 'hostbook'))
        tmux('-f', '/dev/null', 'new-session', '-d', '-x', '100', '-y', '24', '-s', 'ui', command)
        groups = [int(p) for p in tmux('list-panes', '-a', '-F', '#{pane_pid}').splitlines()]
        deadline = time.perf_counter() + 20
        while True:
            current, capture, _ = pointer()
            if current is not None:
                break
            if time.perf_counter() > deadline:
                raise RuntimeError('No pointer: ' + capture)
            time.sleep(.05)
        time.sleep(.5)
        for sample in range(-warmups, samples):
            before, _, _ = pointer()
            if before is None:
                raise RuntimeError('Pointer missing before key')
            start = time.perf_counter()
            tmux('send-keys', '-t', 'ui:0.0', 'Down')
            send_ms = (time.perf_counter() - start) * 1000
            captures = []
            while True:
                after, capture, duration = pointer()
                captures.append(round(duration, 3))
                if after is not None and after != before:
                    break
                if time.perf_counter() - start > 5:
                    raise RuntimeError('Down did not move pointer: ' + capture)
                time.sleep(.005)
            elapsed_ms = (time.perf_counter() - start) * 1000
            if sample >= 0:
                measured.append(dict(key='Down', before=list(before), after=list(after),
                                     elapsed_ms=round(elapsed_ms, 3), send_ms=round(send_ms, 3),
                                     capture_ms=captures, captures=len(captures)))
            # Reset outside the measured interval and let focus/preview work settle.
            tmux('send-keys', '-t', 'ui:0.0', 'Up')
            deadline = time.perf_counter() + 5
            while pointer()[0] != before:
                if time.perf_counter() > deadline:
                    raise RuntimeError('Up reset failed')
                time.sleep(.005)
            time.sleep(.08)
    finally:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for group in groups:
                if group > 1 and group != os.getpgrp():
                    try:
                        os.killpg(group, sig)
                    except ProcessLookupError:
                        pass
            if sig == signal.SIGTERM:
                time.sleep(.2)
        subprocess.run(prefix + ['kill-server'], env=env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    rows = run(['ps', '-axo', 'pgid=,pid=,command=']).stdout.decode().splitlines()
    survivors = [line for line in rows if line.split() and int(line.split()[0]) in groups]
    if survivors:
        raise RuntimeError('Owned process groups remain: ' + str(survivors))
    return dict(revision=revision, hosts=count, style='compact', terminal='100x24',
                samples=measured, summary=summary([s['elapsed_ms'] for s in measured]),
                send_summary=summary([s['send_ms'] for s in measured]),
                owned_process_survivors=survivors)


def self_check():
    assert summary(list(range(1, 21))) == dict(median_ms=10.5, p95_ms=19, min_ms=1, max_ms=20)
    assert pointer_from('header\n│ ▌ sample-0001\npreview sample-0002') == (1, 'sample-0001')
    assert pointer_from('sample-0001 without pointer') is None
    env = fixture_env(Path('/tmp/pointer-self-check'))
    assert env['HOSTBOOK_DIR'] == '/tmp/pointer-self-check/hosts'
    assert not {'SSH_AUTH_SOCK', 'SSH_AGENT_PID', 'HOSTBOOK_LIB_DIR', 'TMUX'} & env.keys()
    print('Pointer parser, statistics and fixture isolation checks passed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--revisions', nargs=2, default=['ef847ee', 'c3b1d67'], metavar=('BEFORE', 'AFTER'))
    parser.add_argument('--samples', type=int, default=12)
    parser.add_argument('--warmups', type=int, default=3)
    parser.add_argument('--output', type=Path, help='write JSON here; otherwise print it to stdout')
    parser.add_argument('--self-check', action='store_true', help='check helpers without launching the app')
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    if args.samples < 9 or args.warmups < 0:
        parser.error('use at least nine samples and a nonnegative warmup count')
    repo = args.repo.resolve()
    revisions = [run(['git', '-C', str(repo), 'rev-parse', '--verify', r + '^{commit}'])
                 .stdout.decode().strip() for r in args.revisions]
    report = dict(method='Single Down, immediate capture, then 5 ms sleeps between captures until visible ▌ '
                  'selection changes. Timing includes send-keys and capture subprocess overhead. Up resets '
                  'selection outside the interval; 80 ms settling. Compact/right-preview/rounded/vi at 100x24; '
                  'index built before launch. No pure render-time or universal latency guarantee.',
                  warmups=args.warmups, samples=args.samples, fixtures=[],
                  metadata=dict(machine=platform.platform(), revisions=dict(zip(args.revisions, revisions)),
                                tools={tool: run(command).stdout.decode().splitlines()[0]
                                       for tool, command in [('tmux', ['tmux', '-V']), ('fzf', ['fzf', '--version']),
                                                             ('bash', ['/bin/bash', '--version'])]}))
    with tempfile.TemporaryDirectory(prefix='hb-pointer-', dir='/tmp') as temp:
        base = Path(temp)
        apps = [base / ('snapshot-' + str(i)) for i in range(2)]
        for revision, app in zip(revisions, apps):
            materialize(repo, revision, app)
        for count in (20, 500):
            for index, (revision, app) in enumerate(zip(revisions, apps)):
                result = fixture(base / f'fixture-{index}-{count}', revision, app, count,
                                 args.samples, args.warmups)
                report['fixtures'].append(result)
                print(revision[:7], count, json.dumps(result['summary']), file=sys.stderr, flush=True)
    output = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output)
    else:
        print(output, end='')


if __name__ == '__main__':
    main()
