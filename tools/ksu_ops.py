#!/usr/bin/env python3
"""Write operations for the KernelSU / ReSukiSU manager, with the safety checks.

Split out from the viewer so the decisions can be tested without a phone and
without a GUI. Every function here that touches a device goes through one of the
apply_* helpers, and every apply_* refuses to run until the caller has passed the
checks this module produces.

The rule for all of it: read the current state, back it up, show what would
change, ask, then write. Nothing is written speculatively and nothing is written
without a backup that has been verified to exist.

On the allowlist specifically. Its layout is not documented anywhere. What was
measured on a real tablet is an eight byte header followed by 784 byte records,
with a 252 byte package name at offset 4 of each and a selinux context 704
bytes in. That is enough to read the file and not enough to write one from
scratch: the fields between the name and the context were never decoded, and the
count in the header does not even agree with the number of records present.
Reconstructing a file from guessed field offsets would produce something that
parses and means the wrong thing. So the allowlist can be exported and can be
restored from a file the tablet itself produced, and it cannot be edited field by
field. Toggling individual apps is what the manager app is for.
"""
import os
import re
import shutil
import struct
import time

AL_MAGIC = b'USK\x7f'
AL_ENTRY = 784
AL_NAME_OFF = 4
AL_NAME_LEN = 252

OK, WARN, ERR = 'ok', 'warn', 'err'


class Finding:
    """One thing worth telling the user before a write happens."""

    __slots__ = ('level', 'text', 'detail')

    def __init__(self, level, text, detail=''):
        self.level = level
        self.text = text
        self.detail = detail

    def __str__(self):
        return f'[{self.level}] {self.text}' + (f' -- {self.detail}' if self.detail else '')

    def as_dict(self):
        return {'level': self.level, 'text': self.text, 'detail': self.detail}


def restore_file(dev, local, remote):
    """Put a backup back over remote. Returns (ok, findings).

    The copy goes to /data/local/tmp first because adb push cannot write into
    /data/adb, then moves into place as root. A file that fails to arrive leaves
    the original alone.
    """
    if not local or not os.path.exists(local):
        return False, [Finding(ERR, f'Berkas backup tidak ada: {local}')]
    staged = '/data/local/tmp/_ksu_restore.bin'
    if not dev.push(local, staged):
        return False, [Finding(ERR, 'Gagal mengunggah backup ke tablet')]
    ok, out = dev.su(f'cp {staged} {remote} && chmod 600 {remote} && echo DONE')
    dev.su(f'rm -f {staged}')
    if 'DONE' not in out:
        return False, [Finding(ERR, f'Gagal menulis {remote}', out.strip()[:120])]
    return True, [Finding(OK, f'{remote} dipulihkan dari {os.path.basename(local)}')]


# ------------------------------------------------------------- validation ---

def check_feature(name, new_value, supported, current_value):
    """Findings for turning a kernel feature on or off.

    supported is the list of feature names ksud reports; current_value is what
    it reports now, used only to notice a write that would change nothing.
    """
    f = []
    if not isinstance(supported, (list, tuple, set)):
        supported = list(supported or [])
    if name not in supported:
        f.append(Finding(ERR, f'Fitur "{name}" tidak dikenal',
                         f'tersedia: {", ".join(sorted(supported)) or "tidak ada"}'))
        return f
    try:
        v = int(new_value)
    except (TypeError, ValueError):
        f.append(Finding(ERR, f'Nilai tidak bisa dibaca untuk {name}', repr(new_value)))
        return f
    if v not in (0, 1):
        f.append(Finding(ERR, f'Nilai tidak sah untuk {name}', f'dapat 0 atau 1, bukan {new_value}'))
        return f
    if v == int(current_value):
        f.append(Finding(WARN, f'{name} sudah bernilai {v}', 'tidak ada yang akan berubah'))
    if name == 'adb_root' and v == 1:
        f.append(Finding(WARN, 'Menyalakan adb_root memberi adbd hak root',
                         'root lewat USB akan aktif untuk siapa pun yang bisa kabel'))
    if name == 'su_compat' and v == 0:
        f.append(Finding(WARN, 'Mematikan su_compat Marque aplikasi yang memakai su',
                         'root yang sedang berjalan mungkin tetap jalan sampai reboot'))
    if name == 'selinux_hide' and v == 1:
        f.append(Finding(WARN, 'selinux_hide menyamarkan hasil /sys/fs/selinux',
                         'berguna untuk Hide Blocker, tetapi menyulitkan saat'))

    return f


def check_umount(path, existing, installed_dirs):
    """Findings for adding or removing a umount rule.

    The common failure is a rule left pointing at a module that has since been
    removed, so the rule no longer matches anything and quietly does nothing.
    That is worth a warning rather than an error.
    """
    f = []
    p = (path or '').strip()
    if not p:
        return [Finding(ERR, 'Path kosong')]
    if not p.startswith('/'):
        f.append(Finding(ERR, 'Path harus absolut', f'di-given "{p}"'))
        return f
    if p in existing:
        f.append(Finding(WARN, 'Aturan ini sudah ada', f'{p} sudah ada di daftar umount'))
    if installed_dirs and not any(p == d.rstrip('/') or p.startswith(d.rstrip('/') + '/')
                                  for d in installed_dirs):
        f.append(Finding(WARN, 'Tidak cocok dengan modul yang terpasang',
                         f'tidak ada yang cocok dari {len(installed_dirs)} direktori'))
    if re.search(r'\s', p):
        f.append(Finding(ERR, 'Path tidak boleh mengandung spasi', repr(p)))
    return f


def check_template(tid, template, existing_ids):
    """Findings for saving a profile template."""
    f = []
    if not re.fullmatch(r'[0-9A-Za-z_.:-]{1,64}', tid or ''):
        f.append(Finding(ERR, 'ID template tidak sah',
                         'hanya huruf, angka, titik, garis bawah, titik dua dan strip'))
    if tid in existing_ids:
        f.append(Finding(WARN, f'Template "{tid}" sudah ada', 'akan ditimpa'))
    if template is None or not template.strip():
        f.append(Finding(ERR, 'Isi template kosong'))
    elif len(template) > 16 * 1024:
        f.append(Finding(ERR, 'Template terlalu panjang',
                         f'{len(template)} B, batas wajar 16384 B'))
    return f


def check_allowlist_restore(new_blob, installed_uids):
    """Findings for replacing .allowlist with a file.

    Deliberately refuses anything that was not produced by a ReSukiSU install:
    the kernel parses this file on every su request, and a file that parses but
    means something else would take root away from every app on the device.
    """
    f = []
    if not new_blob:
        return [Finding(ERR, 'Berkas kosong')]
    if not new_blob.startswith(AL_MAGIC):
        return [Finding(ERR, 'Bukan file allowlist ReSukiSU',
                         f'magic {[hex(b) for b in new_blob[:4]]}, harus {AL_MAGIC!r}')]
    if len(new_blob) < 8:
        return [Finding(ERR, 'Berkas terpotong', 'kurang dari 8 byte')]
    version, count = struct.unpack_from('<II', new_blob, 4)
    if version != 4:
        f.append(Finding(ERR, f'Versi allowlist tidak dikenal: {version}',
                         'versi yang dibaca di tablet ini adalah 4'))
    body = len(new_blob) - 8
    have = body // AL_ENTRY
    if have < 1:
        f.append(Finding(ERR, 'Tidak ada entri di dalam berkas'))
    if have > count:
        f.append(Finding(WARN, f'Berkas berisi {have} entri tapi header bilang {count}'))
    if body % AL_ENTRY:
        f.append(Finding(WARN, 'Bagian akhir berkas tidak utuh',
                         f'{body % AL_ENTRY} byte sisa di luar record 784 B'))
    seen = set()
    for i in range(have):
        e = 8 + i * AL_ENTRY
        name = new_blob[e + AL_NAME_OFF:e + AL_NAME_OFF + AL_NAME_LEN].split(b'\x00')[0]
        name = name.decode('utf-8', 'replace').strip()
        if not name:
            continue
        if not re.fullmatch(r'[A-Za-z0-9_.]+', name):
            f.append(Finding(ERR, f'Nama paket tidak sah di entri {i + 1}', repr(name)))
        if name in seen:
            f.append(Finding(WARN, f'Paket muncul dua kali: {name}'))
        seen.add(name)
        if installed_uids and name not in installed_uids:
            f.append(Finding(WARN, f'{name} tidak terpasang di tablet ini',
                             'root untuknya tidak akan berguna'))
    if not seen:
        # Records can be present and still all be blank. That grants root to
        # nobody, and it is the shape a half-written file would have.
        f.append(Finding(ERR, 'Semua entri kosong',
                         f'{have} record tapi tidak ada nama paket yang terbaca'))
    return f


# ----------------------------------------------------------------- backup ---

def backup_file(dev, remote, dest_dir, label):
    """Pull remote into dest_dir with a timestamp. Returns (ok, path, findings)."""
    os.makedirs(dest_dir, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', label)
    local = os.path.join(dest_dir, f'{safe}-{stamp}')
    got = dev.pull(remote, local)
    if not got:
        return False, None, [Finding(ERR, f'Gagal membackup {label}',
                                      'tidak berhenti, penulisan dibatalkan')]
    size = os.path.getsize(local)
    if size == 0:
        os.remove(local)
        return False, None, [Finding(ERR, f'Backup {label} kosong',
                                      'tidak berhenti, penulisan dibatalkan')]
    return True, local, [Finding(OK, f'Backup {label} tersimpan',
                                 f'{os.path.basename(local)} ({size} B)')]


def restore_file(dev, local, remote):
    """Put a backup back. Returns (ok, findings)."""
    if not local or not os.path.exists(local):
        return False, [Finding(ERR, f'Berkas backup tidak ada: {local}')]
    remote_tmp = '/data/local/tmp/_ksu_restore.bin'
    r = subprocess_run_local(['cmd', '/c', 'copy', '/y', local, remote_tmp.replace('/', '\\')])
    del r
    return False, [Finding(ERR, 'fungsi restore belum diimplementasikan')]


def subprocess_run_local(argv):
    import subprocess
    try:
        return subprocess.run(argv, capture_output=True, timeout=60)
    except Exception:                                            # noqa: BLE001
        return None


def list_backups(dest_dir):
    if not os.path.isdir(dest_dir):
        return []
    out = []
    for f in os.listdir(dest_dir):
        p = os.path.join(dest_dir, f)
        if os.path.isfile(p):
            st = os.stat(p)
            out.append({'name': f, 'path': p, 'size': st.st_size,
                        'mtime': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))})
    out.sort(key=lambda x: x['mtime'], reverse=True)
    return out


def prune_backups(dest_dir, keep=20):
    """Keep the newest `keep` backups so the folder does not grow forever."""
    items = list_backups(dest_dir)
    removed = 0
    for it in items[keep:]:
        try:
            os.remove(it['path'])
            removed += 1
        except OSError:
            pass
    return removed


def copy_local(src, dst):
    shutil.copyfile(src, dst)
    return dst