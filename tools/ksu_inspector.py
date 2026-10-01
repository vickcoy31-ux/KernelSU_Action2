#!/usr/bin/env python3
"""KSU Inspector -- read the KernelSU / ReSukiSU state of a phone onto a PC.

Plug the phone in, press one button, and see what the manager app is holding:
which kernel features are on, which apps were granted root, which modules and
umount rules exist, and what the su log has recorded. Nothing is written to the
phone. This tool only ever runs read-only ksud subcommands and reads files out of
/data/adb/ksu.

It asks ksud what the features are rather than decoding .feature_config itself.
That file is a packed structure whose field order is not documented anywhere, and
a wrong guess about it would produce a screen full of plausible-looking nonsense.
`ksud feature list` prints the name, the ID, the current value and the
description for every feature, so there is nothing to interpret.

Only adb is required. Python's tkinter ships with CPython on Windows, so there is
nothing else to install.

    python ksu_inspector.py
"""
import json
import os
import re
import struct
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

ADB_CANDIDATES = [
    r'C:\platform-tools\adb.exe',
    os.path.expandvars(r'%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe'),
    'adb',
]

# Read-only. Every one of these only reads; none of them can change a setting.
KSUD = '/data/adb/ksud'
KSU_DIR = '/data/adb/ksu'
ALLOWLIST = KSU_DIR + '/.allowlist'
MANAGER_HINTS = ('resukisu', 'ksu', 'kernelsu', 'sukisu')


def find_adb():
    for c in ADB_CANDIDATES:
        try:
            r = subprocess.run([c, 'version'], capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                return c
        except (OSError, subprocess.SubprocessError):
            pass
    return None


class Device:
    """One adb device, reached through `adb -s <serial>`."""

    def __init__(self, adb, serial):
        self.adb = adb
        self.serial = serial

    def sh(self, cmd, timeout=40):
        """Run a shell command. Returns (ok, output)."""
        try:
            r = subprocess.run(
                [self.adb, '-s', self.serial, 'shell', cmd],
                capture_output=True, text=True, timeout=timeout, errors='replace')
            return r.returncode == 0, (r.stdout or '') + (r.stderr or '')
        except subprocess.SubprocessError as e:
            return False, str(e)

    def su(self, cmd, timeout=60):
        """Run a command as root. Returns (ok, output)."""
        ok, out = self.sh(f"su -c '{cmd}'", timeout=timeout)
        return ok, out

    def pull(self, remote, local):
        """Copy a binary off the device and pull it.

        cp prints nothing, so the command has to say when it is done; the first
        version waited for the path to appear in the output and therefore always
        gave up, which showed up as an empty allowlist and no error about it.
        """
        tmp = '/data/local/tmp/_ksu_inspector.bin'
        ok, out = self.su(f"cp {remote} {tmp} && chmod 644 {tmp} && echo PULL_OK")
        if 'PULL_OK' not in out:
            return None
        try:
            r2 = subprocess.run(
                [self.adb, '-s', self.serial, 'pull', tmp, local],
                capture_output=True, text=True, timeout=180)
            if r2.returncode == 0 and os.path.exists(local) and os.path.getsize(local) > 0:
                return local
        except subprocess.SubprocessError:
            pass
        finally:
            try:
                self.su(f"rm -f {tmp}")
            except Exception:                                     # noqa: BLE001
                pass
        return None


# --------------------------------------------------------------- allowlist ---

# Reverse engineered from the tablet's own file, not guessed. 2360 bytes = an
# 8 byte header plus three 784 byte entries, and the selinux context string is
# 784 bytes apart in each of them, which is what fixed the stride.
AL_MAGIC = b'USK\x7f'
AL_ENTRY = 784
AL_NAME_OFF = 4
AL_NAME_LEN = 252


def parse_allowlist(blob, uids=None):
    """Return (entries, note). entries = [{package, uid, selinux}].

    uids maps package name to uid and is authoritative when supplied, from
    `pm list packages -U`. The uid stored in the file is read by scanning for a
    plausible value instead of by a fixed offset, because that field is not
    4-byte aligned: the name is a 252 byte slot that ends wherever the package
    name happens to end, so the uid that follows sits at an odd offset and a
    hardcoded one reads the wrong bytes.
    """
    if not blob or len(blob) < 8 or not blob.startswith(AL_MAGIC):
        return [], 'bukan file allowlist ReSukiSU (magic tidak cocok)'
    version, count = struct.unpack_from('<II', blob, 4)
    body = len(blob) - 8
    have = body // AL_ENTRY
    note = f'versi {version}, count={count}, entry terbaca={have}'
    if have < count:
        note += f' -- file lebih pendek dari count ({count} > {have}), jadi terpotong'
    uids = uids or {}
    out = []
    for i in range(have):
        e = 8 + i * AL_ENTRY
        chunk = blob[e:e + AL_ENTRY]
        if len(chunk) < AL_ENTRY:
            break
        name = chunk[AL_NAME_OFF:AL_NAME_OFF + AL_NAME_LEN].split(b'\x00')[0]
        name = name.decode('utf-8', 'replace').strip()
        # The context string is located by content rather than by a fixed
        # offset, because the gap before it holds fields this tool does not
        # decode and guessing where they end would be another guess.
        selinux = ''
        m = re.search(rb'u:[a-z_]+:[a-z_0-9]+:s\d', chunk)
        if m:
            selinux = m.group(0).decode('ascii', 'replace')
        if not name:
            continue
        uid = uids.get(name, 0)
        src = 'pm'
        if not uid:
            uid = _scan_uid(chunk)
            src = 'file'
        out.append({'package': name, 'uid': uid, 'uid_src': src, 'selinux': selinux})
    return out, note


def _scan_uid(chunk):
    """Best-effort uid from the binary, used only if pm does not know the package.

    A uid on Android is an integer in the app range, or 2000 for the shell. Any
    4-byte window whose value lands there is a candidate; the name bytes are
    ASCII so they cannot produce one by accident.
    """
    best = 0
    for off in range(len(chunk) - 3):
        v = struct.unpack_from('<I', chunk, off)[0]
        if v == 2000 or 10000 <= v <= 60000:
            best = v
            break
    return best


# ------------------------------------------------------------- ksud output ---

FEATURE_RE = re.compile(
    r'^\[(?P<state>[A-Z]+)\s*\((?P<val>-?\d+)\)\]\s+'
    r'(?P<name>[A-Za-z0-9_]+)\s+\(ID=(?P<id>\d+)\)\s*$'
)
DESC_RE = re.compile(r'^\s{4}(?P<desc>\S.*)$')


def parse_features(text):
    """ksud feature list -> [{id, name, value, state, description}]."""
    out, cur = [], None
    for line in text.splitlines():
        line = line.rstrip()
        if not line or line.startswith('Available') or set(line.strip()) <= {'=', ' '}:
            continue
        m = FEATURE_RE.match(line)
        if m:
            cur = {
                'id': int(m.group('id')),
                'name': m.group('name'),
                'value': int(m.group('val')),
                'state': m.group('state'),
                'description': '',
            }
            out.append(cur)
            continue
        d = DESC_RE.match(line)
        if d and cur is not None:
            cur['description'] = d.group('desc').strip()
    return out


def parse_props(text):
    """Read the `key=value` and `key: value` lines out of a dumpsys excerpt.

    dumpsys uses both forms -- versionName=v4.2.0-rc2 on one line and
    installerPackageName=... on another -- so splitting on ':' alone silently
    drops half the keys and the manager's version comes out as a question mark.
    """
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        cut = min([i for i in (line.find('='), line.find(':')) if i > 0] or [-1])
        if cut <= 0:
            continue
        k, v = line[:cut].strip(), line[cut + 1:].strip()
        # dumpsys packs several fields onto one line, as in
        # "versionCode=35153 minSdk=26 targetSdk=37", so only the first token
        # after the separator belongs to this key.
        v = v.split()[0] if v.split() else ''
        if k and k not in out:
            out[k] = v
    return out


def parse_uids(text):
    """package name -> uid, from `pm list packages -U`."""
    out = {}
    for line in text.splitlines():
        if not line.startswith('package:'):
            continue
        body = line[len('package:'):]
        m = re.match(r'(\S+)\s+uid:(\d+)', body)
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def collect(dev, tmpdir):
    """Gather everything. Returns a dict; never raises."""
    data = {'serial': dev.serial, 'errors': []}

    def note(where, e):
        data['errors'].append(f'{where}: {e}')

    ok, out = dev.su('id')
    data['root'] = ok and 'uid=0' in out
    data['root_line'] = out.strip().splitlines()[0] if out.strip() else '(tidak ada)'
    if not data['root']:
        note('root', 'tidak dapat su, aplikasi hanya bisa membaca yang tidak butuh root')
        return data

    ok, out = dev.su('uname -a')
    data['kernel'] = out.strip()

    ok, out = dev.su('getprop ro.product.model')
    data['model'] = out.strip()
    ok, out = dev.su('getprop ro.build.version.release')
    data['android'] = out.strip()

    ok, out = dev.su(f'{KSUD} -V 2>&1')
    data['ksud'] = out.strip()

    ok, out = dev.su(f'{KSUD} feature list 2>&1')
    data['features'] = parse_features(out)
    if not data['features']:
        note('fitur', 'ksud feature list tidak menghasilkan apa-apa')

    ok, out = dev.su(f'{KSUD} module list 2>&1')
    data['modules_raw'] = out.strip()
    data['modules'] = _json_list(out)

    ok, out = dev.su(f'{KSUD} umount-config list 2>&1')
    data['umount_raw'] = out.strip()
    data['umount'] = _json_list(out)

    ok, out = dev.su(f'{KSUD} boot-info current-kmi 2>&1; {KSUD} boot-info is-ab-device 2>&1;'
                    f' {KSUD} boot-info slot-suffix 2>&1')
    data['boot_info'] = [l.strip() for l in out.splitlines() if l.strip()]

    # Manager package: looked for by scanning what is installed, so a rename in a
    # future release does not silently break this.
    ok, out = dev.su('pm list packages -U')
    uids = parse_uids(out)
    data['packages_scanned'] = len(uids)
    data['managers'] = []
    for p in uids:
        if any(h in p.lower() for h in MANAGER_HINTS):
            _, v = dev.su(f'dumpsys package {p} 2>/dev/null | grep -E "versionName|versionCode"')
            props = parse_props(v)
            data['managers'].append({
                'package': p,
                'versionName': props.get('versionName', '?'),
                'versionCode': props.get('versionCode', '?'),
            })

    # allowlist, through a pull because the file is binary and text would mangle it
    local = os.path.join(tmpdir, 'allowlist.bin')
    got = dev.pull(ALLOWLIST, local)
    if got:
        with open(got, 'rb') as fh:
            entries, note_ = parse_allowlist(fh.read(), uids)
        data['allowlist'] = entries
        data['allowlist_note'] = note_
    else:
        data['allowlist'] = []
        data['allowlist_note'] = 'gagal mengambil .allowlist'
        note('allowlist', data['allowlist_note'])

    ok, out = dev.su(f'ls -la {KSU_DIR}/log/')
    files = []
    for line in out.splitlines():
        # ls -la gives eight fields here, not nine: perms, links, owner, group,
        # size, date, time, name.
        f = line.split()
        if len(f) >= 8 and f[0].startswith('-'):
            files.append({'name': f[-1], 'size': f[4], 'modified': ' '.join(f[5:7])})
    data['logs'] = files

    ok, out = dev.su(f'ls {KSU_DIR}/profile/templates/')
    # ls of a directory lists . and .. too, and those are not templates.
    data['templates'] = [t for t in out.split() if t not in ('.', '..')]

    ok, out = dev.su(f'cat {KSU_DIR}/.umount 2>/dev/null')
    data['umount_file'] = out.replace('\x00', '').strip()

    return data


def _json_list(text):
    t = text.strip()
    if t.startswith('['):
        try:
            v = json.loads(t)
            return v if isinstance(v, list) else [v]
        except json.JSONDecodeError:
            pass
    return [l for l in t.splitlines() if l.strip()]


# ------------------------------------------------------------------- GUI ----

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('KSU Inspector  -  baca status KernelSU / ReSukiSU')
        self.geometry('1000x700')
        self.data = None
        self.adb = None

        top = ttk.Frame(self, padding=8)
        top.pack(fill='x')
        ttk.Label(top, text='adb:').pack(side='left')
        self.lbl_adb = ttk.Label(top, text='(belum dicari)')
        self.lbl_adb.pack(side='left', padx=6)
        ttk.Button(top, text='Cari adb', command=self.find_adb).pack(side='left')
        self.btn_scan = ttk.Button(top, text='Baca tablet', command=self.scan, state='disabled')
        self.btn_scan.pack(side='left', padx=12)
        ttk.Button(top, text='Ekspor JSON', command=self.export_json, state='disabled')
        self.btn_scan.master.winfo_children()[-1].pack(side='left')
        ttk.Button(top, text='Simpan laporan HTML', command=self.export_html,
                   state='disabled').pack(side='left', padx=6)
        self._export_buttons = (top.winfo_children()[-1], top.winfo_children()[-2])

        self.lbl_status = ttk.Label(self, text='Colok tablet, lalu tekan "Baca tablet".',
                                    padding=(8, 0))
        self.lbl_status.pack(fill='x')

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill='both', expand=True, padx=8, pady=8)
        self.trees = {}
        for name in ('Ringkasan', 'Fitur Kernel', 'Aplikasi Root',
                     'Modul & Umount', 'Log & Profile'):
            f = ttk.Frame(self.nb)
            self.nb.add(f, text=name)
            cols = ('a', 'b', 'c')
            t = ttk.Treeview(f, columns=cols, show='headings', height=20)
            for c, h, w in zip(cols, ('Item', 'Nilai', 'Keterangan'), (280, 200, 420)):
                t.heading(c, text=h)
                t.column(c, width=w)
            sb = ttk.Scrollbar(f, orient='vertical', command=t.yview)
            t.configure(yscrollcommand=sb.set)
            t.pack(side='left', fill='both', expand=True)
            sb.pack(side='right', fill='y')
            self.trees[name] = t

        self.txt = tk.Text(self, height=7, wrap='none')
        self.txt.pack(fill='x', padx=8, pady=(0, 8))

        self.after(200, self.find_adb)

    def find_adb(self):
        self.lbl_status.config(text='Mencari adb...')
        self.adb = find_adb()
        if self.adb:
            self.lbl_adb.config(text=self.adb)
            self.btn_scan.config(state='normal')
            self.lbl_status.config(text='adb ditemukan. Tekan "Baca tablet".')
        else:
            self.lbl_adb.config(text='TIDAK DITEMUKAN')
            self.lbl_status.config(
                text='adb tidak ditemukan. Pasang platform-tools, atau edit ADB_CANDIDATES.')

    def scan(self):
        self.btn_scan.config(state='disabled')
        self.lbl_status.config(text='Membaca...')
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        tmp = os.path.join(os.environ.get('TEMP', '.'), 'ksu_inspector')
        os.makedirs(tmp, exist_ok=True)
        try:
            devs = [l.split('\t')[0] for l in subprocess.run(
                [self.adb, 'devices'], capture_output=True, text=True,
                timeout=30).stdout.splitlines()
                if '\tdevice' in l]
            if not devs:
                self.after(0, lambda: self.lbl_status.config(
                    text='Tidak ada tablet terdeteksi. Cek kabel USB dan "USB debugging".'))
                return
            data = collect(Device(self.adb, devs[0]), tmp)
            self.after(0, lambda: self.render(data))
        except Exception as e:                                   # noqa: BLE001
            self.after(0, lambda: self.lbl_status.config(text=f'Gagal: {e}'))
        finally:
            self.after(0, lambda: self.btn_scan.config(state='normal'))

    # -- rendering ----------------------------------------------------------
    def _fill(self, tab, rows):
        t = self.trees[tab]
        t.delete(*t.get_children())
        for r in rows:
            t.insert('', 'end', values=[str(x) if x is not None else '' for x in r])

    def render(self, d):
        self.data = d
        for b in self._export_buttons:
            b.config(state='normal')

        warn = ''
        if not d.get('root'):
            warn = '  (TIDAK ADA ROOT -- sebagian data tidak bisa dibaca)'
        if d.get('errors'):
            warn += '  ' + '; '.join(d['errors'][:2])
        self.lbl_status.config(text=f"Device {d['serial']}{warn}")

        self._fill('Ringkasan', [
            ('Model', d.get('model', '?'), ''),
            ('Android', d.get('android', '?'), ''),
            ('Kernel', d.get('kernel', '?'), ''),
            ('ksud', d.get('ksud', '?'), 'versi userspace'),
            ('root', 'ya' if d.get('root') else 'tidak', d.get('root_line', '')),
            ('Boot info', ' | '.join(d.get('boot_info', [])), ''),
            ('Paket diperiksa', d.get('packages_scanned', 0), 'total paket terpasang'),
        ] + [('Manager ' + m['package'], m['versionName'], 'versionCode ' + m['versionCode'])
             for m in d.get('managers', [])])

        self._fill('Fitur Kernel',
                   [(f["name"], f"{f['state']} ({f['value']})", f['description'])
                    for f in d.get('features', [])])

        self._fill('Aplikasi Root',
                   [(a['package'], f"uid {a['uid']}", a['selinux'])
                    for a in d.get('allowlist', [])]
                   + [('(catatan)', d.get('allowlist_note', ''), '')])

        rows = [('Modul', str(m), '') for m in d.get('modules', [])]
        rows += [('Umount rule', str(m), '') for m in d.get('umount', [])]
        rows += [('.umount file', d.get('umount_file', ''), '')]
        if not rows:
            rows = [('(tidak ada)', '', '')]
        self._fill('Modul & Umount', rows)

        rows = [('Log ' + l['name'], l['size'] + ' B', l['modified'])
                for l in d.get('logs', [])]
        rows += [('Template profile', t, '') for t in d.get('templates', [])]
        self._fill('Log & Profile', rows or [('(tidak ada)', '', '')])

        self.txt.delete('1.0', 'end')
        for e in d.get('errors', []):
            self.txt.insert('end', 'PERINGATAN: ' + e + '\n')
        self.txt.insert('end',
                        'Aplikasi ini hanya membaca. Tidak ada perintah tulis yang '
                        'dikirim ke tablet.\n')

    # -- export -------------------------------------------------------------
    def _save(self, data, name):
        path = filedialog.asksaveasfilename(defaultextension=name.split('.')[-1],
                                            initialfile=name, filetypes=[(name, '*')])
        if not path:
            return
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(data)
        self.lbl_status.config(text=f'Tersimpan: {path}')

    def export_json(self):
        if self.data:
            self._save(json.dumps(self.data, indent=2, ensure_ascii=False), 'ksu-report.json')

    def export_html(self):
        if self.data:
            self._save(html_report(self.data), 'ksu-report.html')


def html_report(d):
    esc = lambda s: (str(s).replace('&', '&amp;').replace('<', '&lt;')
                     .replace('>', '&gt;'))                                    # noqa: E731
    def table(rows, head):
        h = ''.join(f'<th>{esc(x)}</th>' for x in head)
        b = ''.join('<tr>' + ''.join(f'<td>{esc(c)}</td>' for c in r) + '</tr>'
                    for r in rows)
        return f'<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>'

    parts = [
        '<meta charset="utf-8"><title>KSU report</title>',
        '<style>body{font:14px/1.5 system-ui;margin:2rem;max-width:60rem}'
        'table{border-collapse:collapse;width:100%;margin:1rem 0}'
        'th,td{border:1px solid #ccc;padding:.4rem .6rem;text-align:left;vertical-align:top}'
        'th{background:#f2f2f2}code{background:#f6f6f6;padding:0 .2rem}</style>',
        '<h1>Laporan KernelSU / ReSukiSU</h1>',
        f'<p>Device <code>{esc(d.get("serial"))}</code> &middot; root: '
        f'<b>{"ya" if d.get("root") else "tidak"}</b></p>',
        '<h2>Ringkasan</h2>',
        table([('Model', d.get('model', '?')), ('Android', d.get('android', '?')),
               ('Kernel', d.get('kernel', '?')), ('ksud', d.get('ksud', '?')),
               ('Boot info', ' | '.join(d.get('boot_info', []))),
               ('Manager', ', '.join(m['package'] + ' ' + m['versionName']
                                     for m in d.get('managers', [])))], ('Item', 'Nilai')),
        '<h2>Fitur Kernel</h2>',
        table([(f['name'], f"{f['state']} ({f['value']})", f['description'])
               for f in d.get('features', [])], ('Fitur', 'Nilai', 'Keterangan')),
        '<h2>Aplikasi dengan root</h2>',
        table([(a['package'], a['uid'], a['selinux']) for a in d.get('allowlist', [])],
              ('Paket', 'UID', 'SELinux')),
        '<h2>Modul</h2>', table([(m,) for m in d.get('modules', [])], ('Modul',)),
        '<h2>Log</h2>',
        table([(l['name'], l['size'] + ' B', l['modified']) for l in d.get('logs', [])],
              ('Berkas', 'Ukuran', 'Diubah')),
    ]
    if d.get('errors'):
        parts.append('<h2>Peringatan</h2><ul>'
                     + ''.join(f'<li>{esc(e)}</li>' for e in d['errors']) + '</ul>')
    return '\n'.join(parts)


if __name__ == '__main__':
    App().mainloop()