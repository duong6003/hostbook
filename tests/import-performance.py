#!/usr/bin/env python3
"""Measure isolated SSH-config import staging and real first preflight rendering.

Only runs when given --revision; the archived copy is instrumented with three
builtin file writes. No host is contacted, imported, or written to the live app.
"""
import argparse, json, math, os, pathlib, platform, shlex, shutil, signal, statistics, subprocess, tempfile, time

REPO = pathlib.Path(__file__).resolve().parents[1]

def run(argv, **kw):
    return subprocess.run(argv, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)

def summary(items):
    ordered=sorted(items)
    return {'median_ms':round(statistics.median(items),3),'min_ms':min(items),'max_ms':max(items),
            'p95_ms':ordered[math.ceil(.95*len(items))-1]}

def snapshot(repo, revision, root):
    resolved=run(['git','-C',str(repo),'rev-parse','--verify',revision+'^{commit}']).stdout.decode().strip()
    app=root/'app'; app.mkdir()
    with (root/'app.tar').open('wb') as output:
        subprocess.run(['git','-C',str(repo),'archive',resolved],stdout=output,check=True)
    run(['tar','-xf',str(root/'app.tar'),'-C',str(app)])
    path=app/'lib/import.sh'; src=path.read_text()
    for name,content in (
        ('import_content','printf x > "$HB_BENCH_DIR/content-entry"'),
        ('import_review','printf x > "$HB_BENCH_DIR/review-entry"; printf "%s" "$3" > "$HB_BENCH_DIR/review-count"'),
        ('import_review_screen','printf x > "$HB_BENCH_DIR/screen-entry"')):
        prefix=name+'(){'
        assert src.count(prefix)==1, f'instrumentation point changed: {name}'
        line=src.index('\n',src.index(prefix))
        src=src[:line+1]+'  '+content+'\n'+src[line+1:]
    path.write_text(src)
    return resolved,app

def trial(root, app, count, timeout):
    for part in ('home/.ssh','hosts','state','config/hostbook','bin','marks'):
        (root/part).mkdir(parents=True,exist_ok=True)
    (root/'home/.ssh/config').write_text('')
    (root/'ssh_config').write_text('Include "'+str(root/'hosts')+'/*.conf"\n')
    (root/'config/hostbook/config').write_text('list = compact\nlang = en\nborder = rounded\n')
    source=root/'input.conf'
    source.write_text(''.join(f'# tags: api\n# desc: test service {i}\nHost sample-{i:04d}\n  HostName 192.0.2.{i%250+1}\n  User fixture\n  Port 2222\n' for i in range(count)))
    ssh=shutil.which('ssh'); assert ssh and pathlib.Path(ssh).is_file()
    guard=root/'bin/ssh'; guard.write_text('#!/bin/sh\nfor arg do [ "$arg" != -G ] || exec '+shlex.quote(ssh)+' "$@"; done\nprintf "network SSH prohibited by benchmark\\n" >&2\nexit 97\n'); guard.chmod(0o755)
    for tool in ('pbpaste','wl-paste','xclip','xsel'):
        item=root/'bin'/tool; item.write_text('#!/bin/sh\nexit 1\n'); item.chmod(0o755)
    bindirs=[]
    for tool in ('fzf','gum','age','age-keygen','age-plugin-batchpass','tmux'):
        path=shutil.which(tool)
        if path and str(pathlib.Path(path).parent) not in bindirs: bindirs.append(str(pathlib.Path(path).parent))
    path=':'.join([str(root/'bin'),'/usr/bin','/bin','/usr/sbin','/sbin',*bindirs])
    env={'HOME':str(root/'home'),'XDG_CONFIG_HOME':str(root/'config'),'HOSTBOOK_DIR':str(root/'hosts'),
         'HOSTBOOK_STATE':str(root/'state'),'HOSTBOOK_SSH_CONFIG':str(root/'ssh_config'),
         'HOSTBOOK_KEYCHAIN':'file:'+str(root/'keychain/id'),'HOSTBOOK_LANG':'en','HB_BENCH_DIR':str(root/'marks'),
         'PATH':path,'SHELL':'/bin/bash','LC_ALL':'en_US.UTF-8','TERM':'xterm-256color','USER':'fixture',
         'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null',
         'GIT_AUTHOR_NAME':'fixture','GIT_AUTHOR_EMAIL':'fixture@example.com',
         'GIT_COMMITTER_NAME':'fixture','GIT_COMMITTER_EMAIL':'fixture@example.com'}
    for cmd in (['git','init','-q',str(root/'hosts')], ['git','-C',str(root/'hosts'),'commit','--allow-empty','-qm','fixture'],
                ['/bin/bash',str(app/'hostbook'),'_index']):
        run(cmd,env=env,cwd=root,stdin=subprocess.DEVNULL)
    prefix=['tmux','-S',str(root/'socket')]
    def tm(*args): return run(prefix+list(args),env=env,cwd=root).stdout.decode()
    groups=[]; captures=[]; last_capture=''; observed=None
    try:
        command='exec env -i '+' '.join(shlex.quote(k+'='+v) for k,v in env.items())
        command+=' /bin/bash '+shlex.quote(str(app/'hostbook'))+' import '+shlex.quote(str(source))
        launch_wall=time.time_ns(); launch_mono=time.perf_counter_ns()
        tm('-f','/dev/null','new-session','-d','-x','100','-y','24','-s','ui',command)
        groups=[int(x) for x in tm('list-panes','-a','-F','#{pane_pid}').splitlines()]
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if (root/'marks/screen-entry').exists():
                a=time.perf_counter_ns(); last_capture=tm('capture-pane','-p','-t','ui:0.0'); b=time.perf_counter_ns()
                captures.append(round((b-a)/1e6,3))
                if 'Review import' in last_capture and str(count)+' new' in last_capture:
                    observed=(time.time_ns(),time.perf_counter_ns()); break
            time.sleep(.005 if (root/'marks/screen-entry').exists() else .02)
        if observed is None: raise RuntimeError(f'preflight timeout for {count} hosts; last screen:\n{last_capture}')
        marks=root/'marks'; times={name:(marks/name).stat().st_mtime_ns for name in ('content-entry','review-entry','screen-entry')}
        assert (marks/'review-count').read_text()==str(count), 'unexpected accepted-host count'
        for _ in range(3):
            a=time.perf_counter_ns(); tm('capture-pane','-p','-t','ui:0.0'); captures.append(round((time.perf_counter_ns()-a)/1e6,3))
        assert not list((root/'hosts').glob('*.conf')), 'preflight modified host data'
        return {'launch_to_visible_ms':round((observed[1]-launch_mono)/1e6,3),
                'staging_ms':round((times['review-entry']-times['content-entry'])/1e6,3),
                'review_prepare_ms':round((times['screen-entry']-times['review-entry'])/1e6,3),
                'review_screen_to_visible_ms':round((observed[0]-times['screen-entry'])/1e6,3),
                'review_entry_to_visible_ms':round((observed[0]-times['review-entry'])/1e6,3),
                'capture_overhead_ms':captures,'launch_wall_ns':launch_wall}
    finally:
        for sig in (signal.SIGTERM,signal.SIGKILL):
            for group in groups:
                if group>1 and group!=os.getpgrp():
                    try: os.killpg(group,sig)
                    except ProcessLookupError: pass
            if sig==signal.SIGTERM: time.sleep(.2)
        subprocess.run(prefix+['kill-server'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--revision',required=True,help='fixed committed revision to archive')
    ap.add_argument('--repo',type=pathlib.Path,default=REPO)
    ap.add_argument('--samples',type=int,default=5)
    ap.add_argument('--warmups',type=int,default=1)
    ap.add_argument('--timeout',type=int,default=90)
    ap.add_argument('--output',type=pathlib.Path,required=True)
    args=ap.parse_args()
    if args.samples<3 or args.warmups<0: ap.error('at least 3 samples; nonnegative warmups')
    report={'method':'SSH-config file CLI import to actual preflight at 100x24, en/rounded. Bash builtin marker file mtimes divide import_content staging, import_review row preparation and import_review_screen-to-visible; final segment includes 5ms poll and tmux capture. CLI launch includes startup and source copy. One fresh fixture per sample, no keys, no network SSH; review canceled by process-group teardown. Warmups discarded.', 'machine':platform.platform(),'samples':args.samples,'warmups':args.warmups,'fixtures':[]}
    with tempfile.TemporaryDirectory(prefix='hb-import-bench-',dir='/tmp') as temp:
        root=pathlib.Path(temp); report['revision'],app=snapshot(args.repo,args.revision,root)
        for count in (30,500):
            measurements=[]
            for i in range(-args.warmups,args.samples):
                row=trial(root/f'{count}-{i}',app,count,args.timeout)
                if i>=0: measurements.append(row)
                print(f'{count} {i}: '+json.dumps(row),flush=True)
            metrics=('launch_to_visible_ms','staging_ms','review_prepare_ms','review_screen_to_visible_ms','review_entry_to_visible_ms')
            report['fixtures'].append({'hosts':count,'samples':measurements,
                'summary':{k:summary([m[k] for m in measurements]) for k in metrics},
                'capture_overhead_summary':summary([x for m in measurements for x in m['capture_overhead_ms']])})
    args.output.write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__': main()
