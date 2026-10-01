#!/usr/bin/env python3
"""Progress monitor for the SUSFS port, readable from a phone.

The tablet is not connected and must not be touched, so the only way to see
whether this work is moving while away is a page. This serves one, on the local
network, guarded by a token in the URL because the network is not private.

State lives in watch_state.json and the running commentary in watch.log, both
next to this file. Worker steps append to them through the set/log/task
subcommands rather than editing them by hand.

  python tools/watch.py serve
  python tools/watch.py phase "applying 22 kernel files"
  python tools/watch.py log "some line of commentary"
  python tools/watch.py task "apply kernel patches" running|done|failed
  python tools/watch.py reset
"""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, 'watch_state.json')
LOG = os.path.join(HERE, 'watch.log')
TOKEN = os.environ.get('WATCH_TOKEN') or hashlib.sha256(
    (str(time.time()) + os.environ.get('USERNAME', '')).encode()).hexdigest()[:16]
PORT = int(os.environ.get('WATCH_PORT', '8730'))
REPO = 'vickcoy31-ux/KernelSU_Action2'

LOCK = threading.Lock()


def blank():
    return {
        'phase': 'idle',
        'detail': '',
        'started': time.strftime('%Y-%m-%d %H:%M:%S'),
        'updated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'tasks': [],
    }


def load():
    if not os.path.exists(STATE):
        return blank()
    try:
        with open(STATE, encoding='utf-8') as fh:
            s = json.load(fh)
    except Exception:
        return blank()
    s.setdefault('tasks', [])
    s.setdefault('phase', 'idle')
    s.setdefault('detail', '')
    return s


def save(s):
    s['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
    tmp = STATE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(s, fh, indent=2)
    os.replace(tmp, STATE)


def logline(msg):
    with LOCK, open(LOG, 'a', encoding='utf-8') as fh:
        fh.write(f"{time.strftime('%H:%M:%S')}  {msg}\n")


def tail(n=160):
    if not os.path.exists(LOG):
        return []
    with open(LOG, encoding='utf-8', errors='replace') as fh:
        return fh.read().splitlines()[-n:]


def runs():
    """Latest workflow runs, so the phone shows the build too, not just my log."""
    try:
        out = subprocess.run(
            ['gh', 'run', 'list', '--repo', REPO, '--limit', '8',
             '--json', 'name,status,conclusion,headBranch,displayTitle,createdAt,url'],
            capture_output=True, text=True, timeout=25)
        if out.returncode != 0:
            return [{'error': (out.stderr or 'gh failed').strip()[:200]}]
        return json.loads(out.stdout or '[]')
    except Exception as e:
        return [{'error': str(e)[:200]}]


PAGE = """<!doctype html><html><head>
<meta charset="utf-8"><meta name=viewport content="width=device-width,initial-scale=1">
<title>SUSFS port</title><style>
:root{--bg:#F6FAFE;--card:#FFFFFF;--line:#C4E7FF;--ink:#0B2A38;--acc:#1D6586;--ok:#1B7F4B;--bad:#B3261E}
*{box-sizing:border-box}
body{margin:0;padding:12px;background:var(--bg);color:var(--ink);
 font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
h1{font-size:17px;margin:0 0 2px}
.sub{color:#557; font-size:12px; margin-bottom:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
 padding:10px 12px;margin-bottom:10px}
.phase{font-weight:650;color:var(--acc);font-size:16px}
.detail{font-size:13px;margin-top:4px;white-space:pre-wrap}
ul{list-style:none;padding:0;margin:8px 0 0}
li{display:flex;gap:8px;align-items:flex-start;padding:4px 0;font-size:13px;
 border-top:1px solid #EAF4FC}
li:first-child{border-top:0}
.mark{flex:0 0 18px;font-weight:700}
.done .mark{color:var(--ok)} .running .mark{color:var(--acc)}
.failed .mark{color:var(--bad)} .todo .mark{color:#9AB}
.todo{color:#7A8C96}
pre{font:12px/1.45 ui-monospace,Consolas,monospace;background:#0B2A38;color:#D6EEFB;
 padding:10px;border-radius:8px;overflow-x:auto;white-space:pre-wrap;word-break:break-word;
 max-height:52vh;overflow-y:auto;margin:0}
.run{border-top:1px solid #EAF4FC;padding:6px 0;font-size:12px}
.run:first-child{border-top:0}
a{color:var(--acc)}
.b{font-weight:700} .g{color:var(--ok)} .r{color:var(--bad)} .y{color:#8A6100}
.note{font-size:11px;color:#7A8C96;margin-top:8px}
</style></head><body>
<h1>SUSFS port &mdash; Galaxy Tab A9</h1>
<div class=sub id=upd>connecting</div>
<div class=card><div class=phase id=phase>&nbsp;</div>
 <div class=detail id=detail></div>
 <ul id=tasks></ul></div>
<div class=sub>build runs</div>
<div class=card id=runs></div>
<div class=sub>log</div>
<div class=card><pre id=log></pre></div>
<div class=note>read-only. nothing here writes to the tablet.</div>
<script>
const T="__TOKEN__";
async function tick(){
 try{
  const r=await fetch("api/status?t="+T+"&_="+Date.now());
  const d=await r.json();
  document.getElementById('phase').textContent=d.phase||'idle';
  document.getElementById('detail').textContent=d.detail||'';
  document.getElementById('upd').textContent=
    'updated '+d.updated+' \\u00b7 '+(d.counts||'');
  document.getElementById('tasks').innerHTML=(d.tasks||[]).map(t=>
    '<li class="'+t.state+'"><span class=mark>'+
    ({done:'\\u2713',running:'\\u25b6',failed:'\\u2717',todo:'\\u25cb'}[t.state]||'\\u25cb')
    +'</span><span>'+(t.name||'')+'</span></li>').join('');
  document.getElementById('runs').innerHTML=(d.runs||[]).map(x=>{
    if(x.error) return '<div class="run r">'+x.error+'</div>';
    const c={success:'g',failure:'r',cancelled:'r',in_progress:'y',queued:'y','':''}[x.conclusion]||'';
    const s=x.status==='completed'?x.conclusion:x.status;
    return '<div class=run><span class="b '+c+'">'+s+'</span> \\u00b7 '+
      (x.name||'')+' <span style="color:#7A8C96">'+((x.headBranch||''))+'</span><br>'+
      '<a href="'+x.url+'">'+(x.displayTitle||'').slice(0,70)+'</a></div>';
  }).join('');
  const L=document.getElementById('log');
  const atBottom = L.scrollTop+L.clientHeight >= L.scrollHeight-40;
  L.textContent=(d.log||[]).join('\\n');
  if(atBottom) L.scrollTop=L.scrollHeight;
 }catch(e){document.getElementById('upd').textContent='reconnecting';}
}
tick(); setInterval(tick,5000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def ok(self, body, ctype):
        b = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def deny(self):
        self.send_response(404)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        path, _, qs = self.path.partition('?')
        args = dict(p.split('=', 1) for p in qs.split('&') if '=' in p)
        if path == '/health':
            return self.ok('ok', 'text/plain')
        if args.get('t') != TOKEN:
            return self.deny()
        if path.rstrip('/') in ('', '/index.html'):
            return self.ok(PAGE.replace('__TOKEN__', TOKEN), 'text/html; charset=utf-8')
        if path == '/api/status':
            s = load()
            c = {}
            for t in s['tasks']:
                c[t.get('state', 'todo')] = c.get(t.get('state', 'todo'), 0) + 1
            counts = f"{c.get('done',0)} done"
            if c.get('running'):
                counts += f", {c['running']} running"
            if c.get('failed'):
                counts += f", {c['failed']} FAILED"
            todo = c.get('todo', 0) + c.get('pending', 0)
            if todo:
                counts += f", {todo} to go"
            return self.ok(json.dumps({
                'phase': s['phase'], 'detail': s['detail'], 'updated': s['updated'],
                'started': s['started'], 'counts': counts,
                'tasks': s['tasks'], 'runs': runs(), 'log': tail(),
            }, ensure_ascii=False), 'application/json; charset=utf-8')
        return self.deny()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'serve'

    if cmd == 'serve':
        s = load()
        if not s.get('started_real'):
            s = blank()
            s['started_real'] = True
            save(s)
            logline('monitor started')
        srv = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
        logline(f'serving on 0.0.0.0:{PORT}')
        print(f'TOKEN={TOKEN}  PORT={PORT}', flush=True)
        srv.serve_forever()

    if cmd == 'reset':
        save(blank())
        if os.path.exists(LOG):
            os.remove(LOG)
        logline('reset')
        print('ok')
        return

    if cmd == 'phase':
        s = load()
        s['phase'] = sys.argv[2] if len(sys.argv) > 2 else 'idle'
        s['detail'] = sys.argv[3] if len(sys.argv) > 3 else ''
        save(s)
        logline(f"== {s['phase']} == {s['detail']}")
        print('ok')
        return

    if cmd == 'log':
        for a in sys.argv[2:]:
            logline(a)
        print('ok')
        return

    if cmd == 'task':
        name, state = sys.argv[2], sys.argv[3]
        s = load()
        for t in s['tasks']:
            if t['name'] == name:
                t['state'] = state
                break
        else:
            s['tasks'].append({'name': name, 'state': state})
        save(s)
        logline(f"[{state}] {name}")
        print('ok')
        return

    print(__doc__)
    sys.exit(1)


if __name__ == '__main__':
    main()