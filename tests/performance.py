#!/usr/bin/env python3
"""Measure app callbacks against disposable host data; never connect to a host.

Usage: python3 tests/performance.py /path/to/hostbook [--output report.json]
Run each revision on the same machine with the same options, without other tests.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--samples', type=int, default=9)
    parser.add_argument('--sizes', type=int, nargs='+', default=[20, 500])
    args = parser.parse_args()
    if args.samples < 3 or any(n < 1 for n in args.sizes):
        parser.error('use at least 3 samples and positive fixture sizes')
    app = args.app.resolve()
    revision = subprocess.run(['git', '-C', str(app.parent), 'rev-parse', 'HEAD'],
                              capture_output=True, text=True).stdout.strip()
    report = {'app': str(app), 'revision': revision, 'machine': platform.platform(),
              'columns': 100, 'lines': 24, 'samples': args.samples, 'fixtures': []}
    with tempfile.TemporaryDirectory(prefix='hb-performance-') as temp:
        base = Path(temp)
        for count in args.sizes:
            root = base/str(count)
            home, hosts, state = root/'home', root/'hosts', root/'state'
            for folder in (home/'.ssh', hosts, state, root/'config'/'hostbook', root/'tmux'):
                folder.mkdir(parents=True, exist_ok=True)
            sshconfig = root/'ssh_config'
            sshconfig.write_text(f'Include "{hosts}/*.conf"\n')
            (hosts/'sample.conf').write_text(''.join(
                f'# tags: staging api\nHost sample-{i:04}\n  HostName 192.0.2.{i%250+1}\n  User fixture\n'
                for i in range(count)))
            (root/'config'/'hostbook'/'config').write_text('list = compact\nlang = vi\n')
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(root/'config'),
                       HOSTBOOK_DIR=str(hosts), HOSTBOOK_STATE=str(state),
                       HOSTBOOK_SSH_CONFIG=str(sshconfig), HOSTBOOK_KEYCHAIN='file:'+str(root/'keychain'),
                       HOSTBOOK_LANG='vi', HOSTBOOK_LANG_DIR=str(app.parent/'lang'), TMUX_TMPDIR=str(root/'tmux'),
                       COLUMNS='100', LINES='24', TERM='xterm-256color', LC_ALL='en_US.UTF-8',
                       GIT_AUTHOR_NAME='fixture', GIT_AUTHOR_EMAIL='fixture@example.com',
                       GIT_COMMITTER_NAME='fixture', GIT_COMMITTER_EMAIL='fixture@example.com')
            for key in ('SSH_AUTH_SOCK', 'SSH_AGENT_PID', 'HOSTBOOK_MASTER', 'HOSTBOOK_ONLY',
                        'HOSTBOOK_LIB_DIR', 'TMUX', 'FZF_DEFAULT_OPTS', 'FZF_DEFAULT_OPTS_FILE'):
                env.pop(key, None)
            for command in (['git', 'init', '-q', str(hosts)], ['git', '-C', str(hosts), 'add', '.'],
                            ['git', '-C', str(hosts), 'commit', '-qm', 'Performance fixture']):
                subprocess.run(command, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

            def measure(command):
                start = time.perf_counter()
                run = subprocess.run(['/bin/bash', str(app), *command], cwd=root, env=env,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE, timeout=120)
                elapsed = (time.perf_counter()-start)*1000
                if run.returncode:
                    raise RuntimeError(f'{command}: {run.returncode}: {run.stderr.decode(errors="replace")}')
                return round(elapsed, 3)

            fixture = {'hosts': count, 'cold_index_ms': measure(['_index']), 'warm': {}}
            (state/'index.tsv').unlink(missing_ok=True)
            shutil.rmtree(state/'idx2', ignore_errors=True)
            fixture['cold_list_ms'] = measure(['_list'])
            for name, command in [('version', ['--version']), ('preview', ['_preview', 'h:sample-0000']),
                                  ('header', ['_header']), ('footer', ['_footer', 'h:sample-0000']),
                                  ('list', ['_list']), ('demo', ['_demo', 'preset=compact'])]:
                measure(command)
                values = [measure(command) for _ in range(args.samples)]
                fixture['warm'][name] = dict(median_ms=round(statistics.median(values), 3),
                                            min_ms=min(values), max_ms=max(values), samples_ms=values)
            report['fixtures'].append(fixture)
            print(f'{count} hosts: '+', '.join(f'{k}={v["median_ms"]:.1f}ms' for k,v in fixture['warm'].items()), flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
