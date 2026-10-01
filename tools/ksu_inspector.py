#!/usr/bin/env python3
"""KSU Inspector -- see, and carefully change, the KernelSU / ReSukiSU state of a phone.

Plug the phone in, press one button, and see what the manager app is holding:
which kernel features are on, which apps were granted root, which modules and
umount rules exist, and what the su log has recorded. The "Ubah (tulis)" tab can
change the kernel features, the umount rules, the profile templates and the
allowlist.

Every write goes through one pipeline in WriteTab.commit, and there is no second
path to the device: work out what is being asked for, run the checks, show the
findings in red when something is wrong, back the current file up, confirm the
backup actually landed, ask, write, then read the result back and report it. A
write cannot begin before a backup exists.

It asks ksud what the features are rather than decoding .feature_config itself.
That file is a packed structure whose field order is not documented anywhere, and
a wrong guess about it would produce a screen full of plausible-looking nonsense.
`ksud feature list` prints the name, the ID, the current value and the
description for every feature, so there is nothing to interpret.

The allowlist can be exported and restored from a file the phone produced, and
cannot be composed field by field; see the top of ksu_ops.py for why.

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ksu_ops                                                     # noqa: E402
from ksu_ops import ERR, OK, WARN, Finding                        # noqa: E402

KSU_DIR = '/data/adb/ksu'
ALLOWLIST = KSU_DIR + '/.allowlist'
FEATURE_FILE = KSU_DIR + '/.feature_config'
BACKUP_DIR = os.path.join(os.environ.get('USERPROFILE', '.'), 'ksu-inspector-backups')

ADB_CANDIDATES = [
    r'C:\platform-tools\adb.exe',
    os.path.expandvars(r'%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe'),
    'adb',
]

KSUD = '/data/adb/ksud'
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

    def push(self, local, remote):
        """adb push to a world-writable path, then chmod so root can move it."""
        stage = '/data/local/tmp/_ksu_push.bin'
        try:
            r = subprocess.run(
                [self.adb, '-s', self.serial, 'push', local, stage],
                capture_output=True, text=True, timeout=180)
            if r.returncode != 0:
                return False
            ok, out = self.su(f'cp {stage} {remote} && chmod 600 {remote} && echo PUSH_OK')
            return 'PUSH_OK' in out
        except subprocess.SubprocessError:
            return False
        finally:
            try:
                self.su('rm -f /data/local/tmp/_ksu_push.bin')
            except Exception:                                     # noqa: BLE001
                pass


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
    data['uids'] = uids
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

    # The su log is the one thing here worth reading in full, so it is pulled and
    # parsed rather than summarised by size. Newest file by name, since the names
    # sort chronologically: sulog-YYYY-MM-DD.log.
    sulogs = [f for f in files if f['name'].startswith('sulog-')]
    data['sulog'] = []
    data['sulog_file'] = None
    if sulogs:
        newest = sorted(sulogs, key=lambda x: x['name'])[-1]
        got = dev.pull(f"{KSU_DIR}/log/{newest['name']}", os.path.join(tmpdir, 'sulog.log'))
        if got:
            with open(got, encoding='ascii', errors='replace') as fh:
                data['sulog'] = ksu_ops.parse_sulog(fh.read())
            data['sulog_summary'] = ksu_ops.sulog_summary(data['sulog'])
            data['sulog_file'] = newest
        else:
            note('sulog', 'gagal mengambil ' + newest['name'])

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

# Every colour below was read out of a screenshot of the manager app running on
# this tablet, not chosen by eye: a histogram over the whole 800x1340 frame gave
# the dominant tone of each surface.
#
#   #F6FAFE  page background       51.6% of the frame
#   #EAEEF3  second surface        37.7%
#   #C4E7FF  the blue status card   6.4%
#   #1D6586  the accent used for section headings
#   #004C69  the darker accent used for the built-in badge
#   #181C1F  primary text
#   #41484D  secondary text
#   #ABAFB3  muted text
#   #DFE3E7  hairline
#   #EF2056  the red the app uses for errors
#
# The manager is a light theme, which is worth saying because light was not what
# was expected of a root manager.
BG, SURF, CARD = '#F6FAFE', '#EAEEF3', '#FFFFFF'
BORDER, ACCENT, ACCENT_D = '#DFE3E7', '#1D6586', '#004C69'
STATUS, STATUS_D = '#C4E7FF', '#0E4A66'
TEXT, TEXT2, MUTED, DANGER = '#181C1F', '#41484D', '#ABAFB3', '#EF2056'

FONT = 'Segoe UI'


class Card(tk.Frame):
    """A surface with a hairline border, holding whatever is put inside it.

    This began as a canvas drawing a rounded rectangle with four arcs, which is
    how the manager's cards look. It was dropped after it rendered as an empty
    box: the canvas item is what gives the inner frame its height, so deriving
    that height from the frame means waiting for an event only a nonzero height
    would produce, and none ever arrives. Square corners that always show beat
    rounded corners that sometimes do not.

        Card(master, bg=CARD).body
    """

    def __init__(self, master, bg=CARD, pad=16, **kw):
        super().__init__(master, bg=BORDER, bd=0, highlightthickness=1,
                         highlightbackground=BORDER)
        self.body = tk.Frame(self, bg=bg, padx=pad, pady=pad)
        self.body.pack(fill='both', expand=True, padx=1, pady=1)
        if kw:
            self.body.configure(**kw)


def pill(parent, text, command, bg=CARD, fg=TEXT2, padx=14, pady=7, **kw):
    """A flat button that reads like the manager's navigation pill."""
    opts = dict(relief='flat', bd=0, highlightthickness=1,
                highlightbackground=BORDER, highlightcolor=BORDER,
                activebackground=SURF, activeforeground=TEXT,
                bg=bg, fg=fg, font=(FONT, 10, 'bold'),
                padx=padx, pady=pady, cursor='hand2')
    opts.update(kw)
    return tk.Button(parent, text=text, command=command, **opts)


class App(tk.Tk):
    TABS = ('Ringkasan', 'Fitur Kernel', 'Aplikasi Root', 'Riwayat root',
            'Modul & Umount', 'Ubah (tulis)')

    def __init__(self):
        super().__init__()
        self.title('ReSukiSU  -  Status & Pengaturan')
        self.geometry('1060x840')
        self.configure(bg=BG)
        self.data = None
        self.adb = None
        self.dev = None
        self.write = None
        self.sulog_all = []
        self.sulog_filter = None
        self._pages = {}
        self._pills = {}

        self._build()
        self.after(200, self.find_adb)

    # -- chrome -------------------------------------------------------------
    def _build(self):
        head = tk.Frame(self, bg=BG)
        head.pack(fill='x', padx=20, pady=(18, 10))
        tk.Label(head, text='ReSukiSU', bg=BG, fg=TEXT,
                 font=(FONT, 26, 'bold')).pack(side='left')
        right = tk.Frame(head, bg=BG)
        right.pack(side='right')
        self.lbl_adb = tk.Label(right, text='(mencari adb)', bg=BG, fg=MUTED,
                                font=(FONT, 9), anchor='e')
        self.lbl_adb.pack(fill='x')
        self.btn_scan = pill(right, 'Baca tablet', self.scan, bg=ACCENT, fg='#FFFFFF',
                             highlightbackground=ACCENT, activebackground=ACCENT_D,
                             activeforeground='#FFFFFF')
        self.btn_scan.pack(anchor='e', pady=(4, 0))

        self.status = Card(self, bg=STATUS, pad=18)
        self.status.pack(fill='x', padx=20, pady=(0, 12))
        self.lbl_status_big = tk.Label(self.status.body, text='Belum dibaca', bg=STATUS,
                                       fg=STATUS_D, font=(FONT, 14, 'bold'), anchor='w')
        self.lbl_status_big.pack(fill='x')
        self.lbl_status_sub = tk.Label(self.status.body,
                                       text='Colok tablet, lalu tekan "Baca tablet".',
                                       bg=STATUS, fg=STATUS_D, font=(FONT, 10), anchor='w',
                                       justify='left')
        self.lbl_status_sub.pack(fill='x')

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill='x', padx=20, pady=(0, 8))
        for name in self.TABS:
            b = pill(bar, name, lambda n=name: self.show(n))
            b.pack(side='left', padx=(0, 6))
            self._pills[name] = b
        body = tk.Frame(self, bg=BG)
        body.pack(fill='both', expand=True, padx=20, pady=(0, 10))
        # The pages have to be created with body as their parent. Created on the
        # window and then placed into body, Tk lays them out against a master
        # they are not children of: body ends up as tall as the whole window and
        # covers the header, the status card and the tab bar.
        for name in self.TABS:
            self._pages[name] = tk.Frame(body, bg=BG)
        self.nb_write = self._pages['Ubah (tulis)']
        for name, f in self._pages.items():
            if name != 'Ubah (tulis)':
                self._build_page(name, f)

        self.txt = tk.Text(self, height=5, bg=SURF, fg=TEXT2, relief='flat',
                           font=(FONT, 9), padx=10, pady=6)
        self.txt.pack(fill='x', padx=20, pady=(0, 12))

        self.show('Ringkasan')

    def _build_page(self, name, f):
        if name == 'Ringkasan':
            self.pg_summary = tk.Frame(f, bg=BG)
            self.pg_summary.pack(fill='both', expand=True)
            self.lbl_kernel = tk.Label(self.pg_summary, text='', bg=BG, fg=TEXT2,
                                       font=(FONT, 10), anchor='w', justify='left')
            self.lbl_kernel.pack(fill='x')
            self.cards = tk.Frame(self.pg_summary, bg=BG)
            self.cards.pack(fill='both', expand=True, pady=(10, 0))
            self.summary_cards = []
        elif name == 'Riwayat root':
            top = tk.Frame(f, bg=BG)
            top.pack(fill='x', pady=(0, 6))
            self.sulog_info = tk.Label(top, text='', bg=BG, fg=TEXT2, font=(FONT, 9),
                                       anchor='w')
            self.sulog_info.pack(fill='x')
            ent = tk.Frame(top, bg=BG)
            ent.pack(fill='x', pady=(6, 0))
            self.e_sulog = tk.Entry(ent, bg=CARD, fg=TEXT, relief='flat',
                                    highlightthickness=1, highlightbackground=BORDER,
                                    font=(FONT, 10))
            self.e_sulog.pack(side='left', fill='x', expand=True, ipady=4)
            self.e_sulog.bind('<Return>', lambda e: self.fill_sulog())
            pill(ent, 'Saring', self.fill_sulog).pack(side='left', padx=(8, 0))
            types = tk.Frame(top, bg=BG)
            types.pack(fill='x', pady=(6, 0))
            self.sulog_types = {}
            for t in ('semua', 'sucompat', 'ioctl_grant_root', 'root_execve', 'daemon_start'):
                b = pill(types, t, lambda x=t: self.filter_sulog(x), padx=10, pady=4)
                b.pack(side='left', padx=(0, 4))
                self.sulog_types[t] = b
            self.t_sulog = self._tree(f, (('Waktu', 90), ('Tipe', 150), ('Proses', 150),
                                         ('UID', 70), ('Perintah', 520)))
        else:
            self.t_pages = getattr(self, 't_pages', {})
            cols = {'Fitur Kernel': (('Fitur', 200), ('Nilai', 70), ('Keterangan', 620)),
                    'Aplikasi Root': (('Paket', 260), ('UID', 90), ('SELinux', 200)),
                    'Modul & Umount': (('Item', 260), ('Nilai', 200), ('Keterangan', 420))}[name]
            self.t_pages[name] = self._tree(f, cols)

    def _tree(self, parent, cols):
        wrap = tk.Frame(parent, bg=CARD)
        wrap.pack(fill='both', expand=True)
        names = [c[0] for c in cols]
        t = ttk.Treeview(wrap, columns=names, show='headings', height=18)
        style = ttk.Style(self)
        style.theme_use('clam')
        # A new ttk style name has no layout until it inherits one; configure()
        # on its own leaves Tk reporting "Layout KSU.Tree not found".
        style.layout('KSU.Tree', style.layout('Treeview'))
        style.layout('KSU.Tree.Heading', style.layout('Treeview.Heading'))
        style.configure('KSU.Tree', background=CARD, fieldbackground=CARD,
                        foreground=TEXT, borderwidth=0, rowheight=26, font=(FONT, 10))
        style.map('KSU.Tree', background=[('selected', ACCENT)],
                  foreground=[('selected', '#FFFFFF')])
        style.configure('KSU.Tree.Heading', background=SURF, foreground=TEXT2,
                        relief='flat', font=(FONT, 10, 'bold'))
        style.map('KSU.Tree.Heading', background=[('active', SURF)])
        t.configure(style='KSU.Tree')
        for n, (h, w) in zip(names, cols):
            t.heading(n, text=h)
            t.column(n, width=w, anchor='w', stretch=(n == names[-1]))
        sb = ttk.Scrollbar(wrap, orient='vertical', command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        return t

    def show(self, name):
        # Every page is forgotten, the write tab included. Skipping it left that
        # one permanently placed, so once it was opened it sat on top of
        # everything and no pill could be reached to switch away from it again.
        for f in self._pages.values():
            f.place_forget()
        self._pages[name].place(relwidth=1, relheight=1)
        for n, b in self._pills.items():
            on = (n == name)
            b.configure(bg=ACCENT if on else CARD,
                        fg='#FFFFFF' if on else TEXT2,
                        highlightbackground=ACCENT if on else BORDER,
                        activebackground=ACCENT_D if on else SURF,
                        activeforeground='#FFFFFF' if on else TEXT)

    # -- adb ----------------------------------------------------------------
    def find_adb(self):
        self.adb = find_adb()
        if self.adb:
            self.lbl_adb.config(text=self.adb)
            self.lbl_status_sub.config(text='adb ditemukan. Tekan "Baca tablet".')
        else:
            self.lbl_adb.config(text='adb TIDAK ditemukan')
            self.lbl_status_sub.config(text='Pasang Android platform-tools, lalu jalankan ulang.')

    def scan(self):
        self.btn_scan.config(state='disabled', bg=SURF, fg=MUTED)
        self.lbl_status_big.config(text='Membaca tablet...')
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        tmp = os.path.join(os.environ.get('TEMP', '.'), 'ksu_inspector')
        os.makedirs(tmp, exist_ok=True)
        try:
            devs = [l.split('\t')[0] for l in subprocess.run(
                [self.adb, 'devices'], capture_output=True, text=True,
                timeout=30).stdout.splitlines() if '\tdevice' in l]
            if not devs:
                self.after(0, lambda: self.lbl_status_big.config(text='Tablet tidak terdeteksi'))
                self.after(0, lambda: self.lbl_status_sub.config(
                    text='Cek kabel USB dan izinkan "USB debugging" di tablet.'))
                return
            self.dev = Device(self.adb, devs[0])
            data = collect(self.dev, tmp)
            self.after(0, lambda: self.render(data))
        except Exception as e:                                    # noqa: BLE001
            self.after(0, lambda: self.lbl_status_big.config(text=f'Gagal: {e}'))
        finally:
            self.after(0, lambda: self.btn_scan.config(
                state='normal', bg=ACCENT, fg='#FFFFFF'))

    # -- rendering ----------------------------------------------------------
    def _fill(self, t, rows):
        t.delete(*t.get_children())
        for r in rows:
            t.insert('', 'end', values=[('' if x is None else str(x)) for x in r])

    def render(self, d):
        self.data = d
        warn = ''
        if not d.get('root'):
            warn = '  (TIDAK ADA ROOT -- sebagian data tidak terbaca)'
        if d.get('errors'):
            warn += '  ' + '; '.join(d['errors'][:2])

        if d.get('root'):
            mgr = (d.get('managers') or [{}])[0]
            self.lbl_status_big.config(text='Berfungsi      Built-in')
            self.lbl_status_sub.config(
                text=f"SuperUser: {len(d.get('allowlist', []))}, "
                     f"Modul: {len(d.get('modules', []))}"
                     f"     Manajer {mgr.get('package', '?')} {mgr.get('versionName', '?')}"
                     + warn)
        else:
            self.lbl_status_big.config(text='Root tidak tersedia')
            self.lbl_status_sub.config(text=warn or 'tablet tidak merespons su')

        self.lbl_kernel.config(
            text=f"Kernel   {d.get('kernel', '?')}\n"
                 f"Model    {d.get('model', '?')}      Android {d.get('android', '?')}"
                 f"      ksud {d.get('ksud', '?')}")

        for c in self.summary_cards:
            c.destroy()
        self.summary_cards = []
        groups = [
            ('Info versi', [
                ('Versi Kernel', (d.get('kernel', '').split(' ')[2]
                                  if len(d.get('kernel', '').split(' ')) > 2 else '-')),
                ('Versi Android', d.get('android', '?')),
                ('Versi driver kernel', d.get('driver_version', 'tidak terbaca')),
                ('Versi Manajer', ' '.join(f"{m['versionName']} ({m['versionCode']})"
                                           for m in d.get('managers', [])) or '?'),
                ('Slot boot', ' | '.join(d.get('boot_info', [])) or '-')]),
            ('Info status', [
                ('Status SELinux', d.get('selinux', '-')),
                ('Akses root', 'uid=0 lewat adb' if d.get('root') else 'tidak ada'),
                ('Paket terpasang', str(d.get('packages_scanned', 0))),
                ('Modul terpasang', str(len(d.get('modules', [])))),
                ('Aturan umount', str(len(d.get('umount', []))))]),
            ('Info log', [
                ('Berkas su log', (d.get('sulog_file') or {}).get('name', 'tidak ada')),
                ('Jumlah record', str(d.get('sulog_summary', {}).get('total', 0))),
                ('Jenis', ', '.join(f'{k} {v}' for k, v in sorted(
                    d.get('sulog_summary', {}).get('counts', {}).items(),
                    key=lambda x: -x[1])) or '-'),
                ('UID yang terlihat', ', '.join(
                    d.get('sulog_summary', {}).get('uids', [])) or '-')]),
        ]
        for title, rows in groups:
            card = Card(self.cards, pad=14)
            card.pack(fill='x', pady=(0, 10))
            self.summary_cards.append(card)
            tk.Label(card.body, text=title, bg=CARD, fg=ACCENT,
                     font=(FONT, 11, 'bold'), anchor='w').pack(fill='x', pady=(0, 8))
            for k, v in rows:
                row = tk.Frame(card.body, bg=CARD)
                row.pack(fill='x', pady=1)
                tk.Label(row, text=k, bg=CARD, fg=TEXT, font=(FONT, 10, 'bold'),
                         anchor='w', width=22).pack(side='left')
                tk.Label(row, text=v, bg=CARD, fg=TEXT2, font=(FONT, 10),
                         anchor='w').pack(side='left', fill='x', expand=True)

        self._fill(self.t_pages['Fitur Kernel'],
                   [(f['name'], f['value'], f['description']) for f in d.get('features', [])])
        self._fill(self.t_pages['Aplikasi Root'],
                   [(a['package'], a['uid'], a['selinux']) for a in d.get('allowlist', [])])

        rows = [('Modul', str(m), '') for m in d.get('modules', [])]
        rows += [('Umount rule', str(m), '') for m in d.get('umount', [])]
        if d.get('umount_file'):
            rows.append(('.umount', ' '.join(d['umount_file'].split())[:70], ''))
        rows.append(('Catatan allowlist', d.get('allowlist_note', ''), ''))
        rows += [(f"Log {l['name']}", l['size'] + ' B', l['modified']) for l in d.get('logs', [])]
        rows += [('Template ' + t, '', '') for t in d.get('templates', [])]
        self._fill(self.t_pages['Modul & Umount'], rows)

        sf = d.get('sulog_file') or {}
        self.sulog_info.config(
            text=f"{sf.get('name', 'tidak ada')}   ·   "
                 f"{d.get('sulog_summary', {}).get('total', 0)} record"
                 f"   ·   waktu dihitung sejak boot, bukan jam dinding")
        self.sulog_all = list(d.get('sulog', []))
        self.sulog_filter = None
        self.e_sulog.delete(0, 'end')
        self.fill_sulog()

        self.txt.delete('1.0', 'end')
        for e in d.get('errors', []):
            self.txt.insert('end', 'PERINGATAN: ' + e + '\n')
        self.txt.insert('end',
                        'Tab "Ubah (tulis)" mengubah tablet: dicek, backup otomatis, '
                        'konfirmasi, lalu diverifikasi.\n'
                        f'Backup: {BACKUP_DIR}\n')

        if self.write is None:
            self.write = WriteTab(self, self.nb_write)
        w = self.write
        w.t_feat.delete(*w.t_feat.get_children())
        for f in d.get('features', []):
            w.t_feat.insert('', 'end', values=(f['name'], f['value'], f['description']))
        w.lb_umount.delete(0, 'end')
        for m in d.get('umount', []):
            w.lb_umount.insert('end', m if isinstance(m, str) else str(m))
        ids = d.get('templates', [])
        w.cb_tmpl['values'] = ids
        if ids and not w.cb_tmpl.get():
            w.cb_tmpl.current(0)
        w.lb_allow.delete(0, 'end')
        for a in d.get('allowlist', []):
            w.lb_allow.insert('end', f"{a['package']:<30} uid={a['uid']}  {a['selinux']}")
        w.refresh_backups()

    # -- sulog --------------------------------------------------------------
    def filter_sulog(self, t):
        self.sulog_filter = None if t == 'semua' else t
        self.fill_sulog()

    def fill_sulog(self):
        want = (self.e_sulog.get() or '').strip().lower()
        rows = []
        for r in reversed(self.sulog_all):                    # newest first
            if self.sulog_filter and r.get('type') != self.sulog_filter:
                continue
            if want and want not in ' '.join(str(v) for v in r.values()).lower():
                continue
            rows.append((ksu_ops.sulog_elapsed(r.get('ts_ns')),
                         r.get('type', ''),
                         r.get('comm') or r.get('file') or '-',
                         r.get('uid', '-'),
                         r.get('argv') or r.get('file') or r.get('boot_id') or ''))
            if len(rows) >= 1200:
                break
        self._fill(self.t_sulog, rows)

    def refresh_feature_state(self):
        if not self.dev or not self.write:
            return
        _, out = self.dev.su(f'{KSUD} feature list 2>&1')
        feats = {x['name']: x for x in parse_features(out)}
        t = self.write.t_feat
        for iid in t.get_children():
            name = t.item(iid, 'values')[0]
            if name in feats:
                t.set(iid, 'val', str(feats[name]['value']))

    def reload_quiet(self):
        if not self.dev:
            return
        tmp = os.path.join(os.environ.get('TEMP', '.'), 'ksu_inspector')
        os.makedirs(tmp, exist_ok=True)
        threading.Thread(target=self._reload_worker, args=(tmp,), daemon=True).start()

    def _reload_worker(self, tmp):
        try:
            data = collect(self.dev, tmp)
            self.after(0, lambda: self.render(data))
        except Exception as e:                                    # noqa: BLE001
            self.after(0, lambda: self.lbl_status_big.config(text=f'Gagal memuat ulang: {e}'))

    def export_json(self):
        if self.data:
            self._save(json.dumps(self.data, indent=2, ensure_ascii=False), 'ksu-report.json')

    def export_html(self):
        if self.data:
            self._save(html_report(self.data), 'ksu-report.html')

    def _save(self, data, name):
        path = filedialog.asksaveasfilename(defaultextension=name.split('.')[-1],
                                            initialfile=name, filetypes=[(name, '*')])
        if not path:
            return
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(data)
        self.lbl_status_sub.config(text=f'Tersimpan: {path}')


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


# ------------------------------------------------------------ write tab ----

class WriteTab:
    """Everything that can change something on the phone.

    One pipeline for all four kinds of write, and it does not have a shortcut:
    work out what is being asked for, run the checks, show them, back the current
    state up and confirm the backup landed, ask the user, write, then read back
    and report. A write cannot start before the backup exists.
    """

    BG_ERR, BG_WARN, BG_OK = '#fdecea', '#fff6e5', '#eef7ee'
    FG_ERR, FG_WARN, FG_OK = '#8b0000', '#7a5200', '#1f5c1f'

    def __init__(self, app, parent):
        self.app = app
        self.f = parent
        self.busy = False

        self.lbl_banner = tk.Label(parent, text='', bg=self.BG_OK, fg=self.FG_OK,
                                   anchor='w', justify='left', padx=8, pady=6,
                                   wraplength=900)
        self.lbl_banner.pack(fill='x', padx=6, pady=(6, 2))

        nb = ttk.Notebook(parent)
        nb.pack(fill='both', expand=True, padx=6, pady=(2, 6))

        self.nb_feat = ttk.Frame(nb); nb.add(self.nb_feat, text='Fitur kernel')
        self.nb_umount = ttk.Frame(nb); nb.add(self.nb_umount, text='Aturan umount')
        self.nb_tmpl = ttk.Frame(nb); nb.add(self.nb_tmpl, text='Template profil')
        self.nb_allow = ttk.Frame(nb); nb.add(self.nb_allow, text='Allowlist (root)')
        self.nb_bak = ttk.Frame(nb); nb.add(self.nb_bak, text='Backup & pemulihan')

        self._build_features()
        self._build_umount()
        self._build_template()
        self._build_allowlist()
        self._build_backups()

    # -- banner -------------------------------------------------------------
    def say(self, findings):
        """Show findings in the banner, red for anything that blocks."""
        if not findings:
            self.lbl_banner.config(text='Siap.', bg=self.BG_OK, fg=self.FG_OK)
            return
        errs = [f for f in findings if f.level == ERR]
        warns = [f for f in findings if f.level == WARN]
        lines = []
        for f in findings:
            mark = 'BAHAYA' if f.level == ERR else ('AWAS' if f.level == WARN else 'OK')
            lines.append(f'{mark}  {f.text}' + (f'\n       {f.detail}' if f.detail else ''))
        if errs:
            bg, fg = self.BG_ERR, self.FG_ERR
        elif warns:
            bg, fg = self.BG_WARN, self.FG_WARN
        else:
            bg, fg = self.BG_OK, self.FG_OK
        self.lbl_banner.config(text='\n'.join(lines), bg=bg, fg=fg)
        if errs:
            self.lbl_banner.config(
                text='⛔ ' + '\n'.join(lines)
                + f'\n\n{len(errs)} masalah yang MENOLAK penulisan.')

    # -- shared write pipeline ---------------------------------------------
    def commit(self, title, findings, backup_remote, apply_fn, verify_fn=None):
        """The only path to a write. Returns True on success."""
        self.say(findings)
        if any(f.level == ERR for f in findings):
            messagebox.showerror('Ditolak',
                                 'Penulisan dibatalkan karena ada masalah:\n\n'
                                 + '\n'.join(f'• {f.text}' for f in findings
                                             if f.level == ERR))
            return False

        body = '\n'.join(f'• {f.text}' + (f'  ({f.detail})' if f.detail else '')
                         for f in findings) or '• (tidak ada catatan)'
        if not messagebox.askyesno(
                title, f'{title}\n\n{body}\n\nBackup otomatis dibuat lebih dulu.\n'
                       'Lanjutkan?'):
            return False

        dev = self.app.dev
        if self.busy:
            messagebox.showwarning('Busy', 'Ada operasi lain yang sedang berjalan.')
            return False
        self.busy = True
        self.lbl_banner.config(text='Mencadangkan...', bg=self.BG_WARN, fg=self.FG_WARN)
        threading.Thread(target=self._commit_worker,
                         args=(title, backup_remote, apply_fn, verify_fn),
                         daemon=True).start()
        return True

    def _commit_worker(self, title, backup_remote, apply_fn, verify_fn):
        dev = self.app.dev
        try:
            ok, path, bfind = ksu_ops.backup_file(dev, backup_remote, BACKUP_DIR, title)
            self.after(0, lambda: self.say(bfind))
            if not ok:
                return
            ok, out = apply_fn(dev)
            res = [Finding(OK if ok else ERR,
                           f'{title}: ' + ('berhasil' if ok else 'GAGAL'),
                           out.strip()[:200])]
            if ok and verify_fn:
                vok, vout = verify_fn(dev)
                res.append(Finding(OK if vok else WARN,
                                   'Verifikasi setelah tulis',
                                   (vout.strip()[:160] or '(kosong)')))
            ksu_ops.prune_backups(BACKUP_DIR)
            self.after(0, lambda: self.say(res))
            self.after(0, lambda: self.app.reload_quiet())
        except Exception as e:                                    # noqa: BLE001
            self.after(0, lambda: self.say([Finding(ERR, 'Galat', str(e))]))
        finally:
            self.busy = False

    # -- features -----------------------------------------------------------
    def _build_features(self):
        p = self.nb_feat
        ttk.Label(p, text='Pilih fitur, lalu nyalakan atau matikan. '
                          'Ini perintah yang sama dengan sakelar di aplikasi manager.').pack(
            anchor='w', padx=8, pady=(8, 2))
        self.t_feat = ttk.Treeview(p, columns=('id', 'val', 'desc'), show='headings', height=8)
        for c, h, w in (('id', 'ID', 50), ('val', 'Nilai', 80), ('desc', 'Keterangan', 640)):
            self.t_feat.heading(c, text=h)
            self.t_feat.column(c, width=w)
        self.t_feat.pack(fill='both', expand=True, padx=8, pady=4)
        self.t_feat.bind('<<TreeviewSelect>>', lambda e: self._on_feat_select())
        b = ttk.Frame(p); b.pack(fill='x', padx=8, pady=(0, 8))
        ttk.Button(b, text='Nyalakan (1)', command=lambda: self.set_feature(1)).pack(side='left')
        ttk.Button(b, text='Matikan (0)', command=lambda: self.set_feature(0)).pack(side='left', padx=6)
        ttk.Label(b, text='Kernel mungkin perlu reboot agar fitur彻底 berlaku.').pack(side='left', padx=10)

    def _on_feat_select(self):
        sel = self.t_feat.selection()
        if sel:
            self.app.refresh_feature_state()

    def _selected_feature(self):
        sel = self.t_feat.selection()
        if not sel:
            return None
        vals = self.t_feat.item(sel[0], 'values')
        return vals[0] if vals else None

    def set_feature(self, value):
        if not self.app.data:
            messagebox.showinfo('Belum dibaca', 'Tekan "Baca tablet" dulu.')
            return
        name = self._selected_feature()
        if not name:
            messagebox.showinfo('Belum dipilih', 'Pilih satu fitur di tabel.')
            return
        feats = {x['name']: x for x in self.app.data.get('features', [])}
        cur = feats.get(name, {}).get('value', 0)
        findings = ksu_ops.check_feature(name, value, list(feats), cur)

        def apply(dev):
            _, out = dev.su(f'{KSUD} feature set {name} {value} 2>&1')
            return ('error' not in out.lower(), out)

        def verify(dev):
            _, out = dev.su(f'{KSUD} feature get {name} 2>&1')
            return (f'({value})' in out or f'={value}' in out, out)

        self.commit(f'Fitur {name} → {value}', findings,
                    FEATURE_FILE, apply, verify)

    # -- umount -------------------------------------------------------------
    def _build_umount(self):
        p = self.nb_umount
        ttk.Label(p, text='Aturan umount: path yang akan dilepas sistem saat modul tidak dipakai.'
                          ).pack(anchor='w', padx=8, pady=(8, 2))
        top = ttk.Frame(p); top.pack(fill='x', padx=8)
        self.e_umount = ttk.Entry(top)
        self.e_umount.pack(side='left', fill='x', expand=True)
        ttk.Button(top, text='Tambah', command=self.umount_add).pack(side='left', padx=4)
        ttk.Button(top, text='Hapus', command=self.umount_del).pack(side='left')
        ttk.Button(top, text='Kosongkan semua', command=self.umount_clear).pack(
            side='left', padx=6)
        self.lb_umount = tk.Listbox(p, height=8)
        self.lb_umount.pack(fill='both', expand=True, padx=8, pady=8)

    def umount_add(self):
        if not self.app.data:
            return messagebox.showinfo('Belum dibaca', 'Tekan "Baca tablet" dulu.')
        path = self.e_umount.get().strip()
        existing = list(self.app.data.get('umount', []))
        dirs = [m if isinstance(m, str) else str(m) for m in self.app.data.get('modules', [])]
        findings = ksu_ops.check_umount(path, existing, dirs)

        def apply(dev):
            _, out = dev.su(f'{KSUD} umount-config add {path} 2>&1')
            return ('error' not in out.lower() and 'usage' not in out.lower(), out)

        self.commit(f'Umount + {path}', findings, KSU_DIR + '/.umount', apply)

    def umount_del(self):
        sel = list(self.lb_umount.curselection())
        if not sel:
            return messagebox.showinfo('Belum dipilih', 'Pilih aturan di daftar.')
        path = self.lb_umount.get(sel[0])
        findings = [Finding(WARN, f'Akan menghapus aturan umount: {path}')]

        def apply(dev):
            _, out = dev.su(f'{KSUD} umount-config del {path} 2>&1')
            return ('error' not in out.lower(), out)

        self.commit(f'Umount − {path}', findings, KSU_DIR + '/.umount', apply)

    def umount_clear(self):
        findings = [Finding(WARN, 'Menghapus SEMUA aturan umount kustom'),
                    Finding(WARN, 'Modul mungkin tidak lagi dilepas saat tidak dipakai')]
        if not messagebox.askyesno(
                'Kosongkan semua',
                'Semua aturan umount kustom akan dihapus.\n\nBackup otomatis dibuat '
                'lebih dulu. Lanjutkan?'):
            return

        def apply(dev):
            _, out = dev.su(f'{KSUD} umount-config clear 2>&1')
            return ('error' not in out.lower(), out)

        self.commit('Umount clear semua', findings, KSU_DIR + '/.umount', apply)

    # -- profile template ---------------------------------------------------
    def _build_template(self):
        p = self.nb_tmpl
        ttk.Label(p, text='Template profil: satu berkas .root per aplikasi. '
                          'Formatnya skrip yang dijalankan root.').pack(
            anchor='w', padx=8, pady=(8, 2))
        top = ttk.Frame(p); top.pack(fill='x', padx=8)
        self.cb_tmpl = ttk.Combobox(top, state='readonly', width=34)
        self.cb_tmpl.pack(side='left')
        self.cb_tmpl.bind('<<ComboboxSelected>>', lambda e: self._load_template())
        ttk.Button(top, text='Muat dari tablet', command=self._load_template).pack(side='left', padx=4)
        ttk.Button(top, text='Simpan ke tablet', command=self._save_template).pack(side='left')
        self.t_tmpl = tk.Text(p, height=12, wrap='none')
        self.t_tmpl.pack(fill='both', expand=True, padx=8, pady=8)

    def _load_template(self):
        tid = self.cb_tmpl.get().strip()
        if not tid or not self.app.dev:
            return
        _, out = self.app.dev.su(f'{KSUD} profile get-template {tid} 2>&1')
        self.t_tmpl.delete('1.0', 'end')
        self.t_tmpl.insert('1.0', out)

    def _save_template(self):
        if not self.app.data:
            return messagebox.showinfo('Belum dibaca', 'Tekan "Baca tablet" dulu.')
        tid = self.cb_tmpl.get().strip()
        if not tid:
            return messagebox.showinfo('Belum dipilih', 'Pilih ID template dari daftar.')
        text = self.t_tmpl.get('1.0', 'end')
        existing = list(self.app.data.get('templates', []))
        findings = ksu_ops.check_template(tid, text, existing)

        def apply(dev):
            _, out = dev.su(f"{KSUD} profile set-template {tid} '{text}' 2>&1")
            return ('error' not in out.lower(), out)

        self.commit(f'Template {tid}', findings, FEATURE_FILE, apply)

    # -- allowlist ----------------------------------------------------------
    def _build_allowlist(self):
        p = self.nb_allow
        warn = ('Berkas ini dibaca kernel setiap kali ada permintaan su. Formatnya tidak '
                'didokumentasikan publik, jadi alat ini hanya bisa memulihkan berkas yang '
                'dihasilkan tablet itu sendiri -- tidak bisa menyusun dari nol. Mengubah '
                'root per aplikasi tetap lewat aplikasi manager.')
        tk.Label(p, text=warn, fg='#7a5200', wraplength=880, justify='left',
                 anchor='w').pack(fill='x', padx=8, pady=(8, 4))
        b = ttk.Frame(p); b.pack(fill='x', padx=8)
        ttk.Button(b, text='Ekspor dari tablet', command=self.allow_export).pack(side='left')
        ttk.Button(b, text='Impor berkas ke tablet', command=self.allow_import).pack(side='left', padx=6)
        ttk.Label(b, text=f'Backup: {BACKUP_DIR}').pack(side='left', padx=10)
        self.lb_allow = tk.Listbox(p, height=6)
        self.lb_allow.pack(fill='both', expand=False, padx=8, pady=8)

    def allow_export(self):
        if not self.app.data:
            return messagebox.showinfo('Belum dibaca', 'Tekan "Baca tablet" dulu.')
        got = self.app.dev.pull(ALLOWLIST, os.path.join(BACKUP_DIR, 'allowlist-export.bin'))
        if got:
            messagebox.showinfo('Ekspor selesai', f'Tersimpan:\n{got}')
        else:
            messagebox.showerror('Gagal', 'Tidak bisa mengambil .allowlist dari tablet.')

    def allow_import(self):
        path = filedialog.askopenfilename(
            title='Pilih .allowlist hasil ekspor', filetypes=[('Allowlist', '*.bin'), ('Semua', '*')])
        if not path:
            return
        with open(path, 'rb') as fh:
            blob = fh.read()
        uids = self.app.data.get('uids', {}) if self.app.data else {}
        findings = list(ksu_ops.check_allowlist_restore(blob, uids))
        cur = os.path.join(BACKUP_DIR, 'allowlist-current.bin')
        if os.path.exists(cur):
            findings.append(Finding(WARN, f' Berkas saat ini {os.path.getsize(cur)} B',
                                    f'akan diganti {len(blob)} B'))

        def apply(dev):
            return dev.push(path, ALLOWLIST), f'{len(blob)} B ditulis'

        def verify(dev):
            tmp = os.path.join(BACKUP_DIR, 'allowlist-verify.bin')
            got = dev.pull(ALLOWLIST, tmp)
            if not got:
                return False, 'tidak bisa membaca kembali'
            with open(got, 'rb') as fh:
                same = fh.read() == blob
            os.remove(got)
            return same, 'isi identik dengan yang dikirim' if same else 'ISI BERBEDA'

        self.commit('Impor .allowlist', findings, ALLOWLIST, apply, verify)

    # -- backups ------------------------------------------------------------
    def _build_backups(self):
        p = self.nb_bak
        ttk.Label(p, text=f'Semua backup ada di {BACKUP_DIR}').pack(anchor='w', padx=8, pady=(8, 2))
        b = ttk.Frame(p); b.pack(fill='x', padx=8)
        ttk.Button(b, text='Segarkan daftar', command=self.refresh_backups).pack(side='left')
        ttk.Button(b, text='Pulihkan yang dipilih', command=self.restore_selected).pack(
            side='left', padx=6)
        self.lb_bak = tk.Listbox(p, height=12)
        self.lb_bak.pack(fill='both', expand=True, padx=8, pady=8)
        self.refresh_backups()

    def refresh_backups(self):
        self.lb_bak.delete(0, 'end')
        for it in ksu_ops.list_backups(BACKUP_DIR):
            self.lb_bak.insert('end', f"{it['mtime']}  {it['size']:>9} B  {it['name']}")

    def restore_selected(self):
        sel = list(self.lb_bak.curselection())
        if not sel:
            return messagebox.showinfo('Belum dipilih', 'Pilih satu backup di daftar.')
        idx = sel[0]
        items = ksu_ops.list_backups(BACKUP_DIR)
        if idx >= len(items):
            return
        it = items[idx]
        target = ALLOWLIST if 'allowlist' in it['name'] else FEATURE_FILE
        findings = [Finding(WARN, f'Menimpa {target} dengan backup {it["name"]}',
                            f'{it["size"]} B, dibuat {it["mtime"]}'),
                    Finding(WARN, 'Backup yang sekarang ada akan digantikan')]
        if not messagebox.askyesno(
                'Pulihkan',
                f'Menimpa {target}?\n\n{it["name"]}\n{it["size"]} B, {it["mtime"]}\n\n'
                'Backup file saat ini dibuat otomatis lebih dulu. Lanjutkan?'):
            return

        def apply(dev):
            return ksu_ops.restore_file(dev, it['path'], target)

        self.commit(f'Pulihkan {it["name"]}', findings, target, apply)


if __name__ == '__main__':
    App().mainloop()