"""doctor.py: Stark World Engine first-start doctor. Finds the usual first-install mistakes and says how to fix them.

  python doctor.py                 run the quick checks, print a friendly report, write doctor.log
  python doctor.py --launch        what the launchers run before the studio starts (may ask Y/N questions)
  python doctor.py --full          also hash every file against files_manifest.json (a little slower)
  python doctor.py --json          print the results as JSON (for tools)

For the studio (in-app panel):  doctor.run_checks(app_dir, quick=True) -> list of
  {id, ok, severity, title, fix_text, auto_fixed}   (+ 'detail' and sometimes 'action')
  severity: 'blocker' (do not start; show the fix), 'warn' (start; show the tip), 'info'.
  Pass running_port=<the studio's own port> when calling from inside the running studio.

--launch exit codes (read by START_STARK_WORLD_ENGINE.bat / .command / .sh):
  0 start the studio   2 a blocker was found (fix shown)   3 the studio is already running (browser opened)
  4 a working copy was made elsewhere and started from there   anything else: doctor broke, start anyway.

Safety: everything is read-only except (a) doctor.log, (b) one automatic repair that keeps a backup (the bundled
runtime's ._pth file gets its missing app-folder line back; the original is saved as ._pth.bak), and (c) copying
the app to a new folder, only after the user typed Y, (d) once, copying the folders older versions used to their
Stark names (verified file by file; the originals stay as they are, see migrate_legacy). Nothing is deleted, no setting or firewall is changed, and
the only network use is 127.0.0.1. Standard library only; Python 3.10+.
"""
import datetime, glob, hashlib, importlib.util, json, os, re, shutil, socket, subprocess, sys, tempfile
import textwrap, threading, time, urllib.request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PORTS = [p for p in range(8505, 8521) if p != 8510]     # same list as studio_app: 8510 belongs to the Creator Hub
MANIFEST = 'files_manifest.json'
LOG_NAME = 'doctor.log'
LOG_MAX = 256 * 1024
CRITICAL = ('studio_app.py', 'licensing.py')
MANIFEST_SKIP = re.compile(r'(^|/)(key\.txt|files_manifest\.json|launcher\.log|doctor\.log)$|\._pth$', re.I)
PTH_LINE = '..\\..'
LOW_DISK = 1024 ** 3
LONG_PATH = 200
IS_WIN = os.name == 'nt'
IS_MAC = sys.platform == 'darwin'
NOWIN = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW
HUB_RULE = 'Stark World Engine Creator Hub 8510'

WEBGL2_FIX = ('Stark World Engine draws its 3D worlds with WebGL2, which needs graphics acceleration in your browser. '
              'If the studio says WebGL2 is not available: (1) Chrome/Edge: Settings > System > turn on "Use graphics '
              'acceleration when available", then restart the browser. Firefox: Settings > General > Performance > '
              'untick "Use recommended performance settings" and tick "Use hardware acceleration when available". '
              '(2) Update your graphics driver (NVIDIA, AMD or Intel website, or Windows Update > Advanced options > '
              'Optional updates). (3) Try another browser (Chrome, Edge or Firefox).')
FIX_TEXTS = {'webgl2': WEBGL2_FIX}

AV_FIX = ('Your antivirus may have quarantined it right after unzipping (this happens with new programs). '
          'Windows: open Windows Security > Virus & threat protection > Protection history, click the item about '
          'Stark World Engine and choose Restore (or Actions > Allow on device). Other antivirus: open its quarantine '
          'list and restore/allow the file. Then extract the zip again into a fresh folder.')


# ---------------- small helpers ----------------
def result(cid, ok, severity, title, fix_text='', auto_fixed=False, detail='', action=None):
    r = {'id': cid, 'ok': bool(ok), 'severity': severity, 'title': title, 'fix_text': fix_text,
         'auto_fixed': bool(auto_fixed), 'detail': detail}
    if action:
        r['action'] = action
    return r


def norm(p):
    return os.path.normcase(os.path.abspath(p))


def under(path, root):
    """True when `path` is `root` or inside it (case-insensitive on Windows)."""
    if not root:
        return False
    p, r = norm(path), norm(root).rstrip('\\/')
    return p == r or p.startswith(r + os.sep)


def writable(path):
    """Really try to create (and remove) a file in `path`. Folder attributes lie on Windows, so test it."""
    # One attempt only: tempfile.mkstemp retries 10000 times on a Windows "access denied" folder.
    # unique per process, thread and call: concurrent checks (e.g. "Fix my setup" clicked twice) never collide
    name = os.path.join(path, f'.gs_write_test_{os.getpid()}_{threading.get_ident()}_{os.urandom(4).hex()}')
    try:
        fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return False
    os.close(fd)
    try:
        os.remove(name)
    except OSError:
        pass
    return True


def home():
    return os.environ.get('USERPROFILE') or os.path.expanduser('~')


def program_files_dirs(env=None):
    env = os.environ if env is None else env
    out = []
    for k in ('ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432'):
        v = env.get(k)
        if v and v not in out:
            out.append(v)
    if IS_WIN and not out:
        out = [r'C:\Program Files', r'C:\Program Files (x86)']
    return out


def run(cmd, timeout=5.0):
    """-> (returncode, stdout) or (None, '') when the program is missing or hangs."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=NOWIN,
                           errors='replace')
        return r.returncode, r.stdout or ''
    except (OSError, subprocess.SubprocessError, ValueError):
        return None, ''


# ---------------- 1. run from inside the zip ----------------
ZIP_TEMP_RX = [
    re.compile(r'[\\/]temp\d*_[^\\/]*\.zip([\\/]|$)', re.I),        # Windows "Compressed Folders": Temp1_x.zip
    # 7-Zip / WinRAR / WinZip "open without extracting" views. They live DIRECTLY in a temp folder; anchoring on it
    # keeps user names / folders such as "wz1988" or "7zombies" from being taken for one (a false blocker).
    re.compile(r'[\\/]te?mp[\\/](7zo[0-9a-f]{3,}[^\\/]*|rar\$[a-z]{2,3}[0-9][^\\/]*|wz[0-9a-f]{2,}[^\\/]*)([\\/]|$)',
               re.I),
    re.compile(r'\.zip[\\/]', re.I),                                # any folder that is still "something.zip"
    re.compile(r'/AppTranslocation/', re.I),                        # macOS Gatekeeper read-only copy
    re.compile(r'/\.fr-[^/]+/', re.I),                              # Linux file-roller temp view
]


def zip_name_from_path(app_dir):
    m = re.search(r'[\\/](?:temp\d*_)?([^\\/]+\.zip)([\\/]|$)', app_dir, re.I)
    return m.group(1) if m else None


def find_zip(name, env=None):
    """Look for the downloaded zip in the usual places (no recursion)."""
    if not name:
        return None
    h = (env or os.environ).get('USERPROFILE') or os.path.expanduser('~')
    for d in ('Downloads', 'Desktop', 'Documents', ''):
        p = os.path.join(h, d, name)
        if os.path.isfile(p):
            return p
    return None


def is_steam(app_dir):
    """The Steam depot ships steam_build.json: Steam owns the install folder."""
    return os.path.isfile(os.path.join(app_dir, 'steam_build.json'))


def check_zip_temp(app_dir, env=None):
    if is_steam(app_dir):
        return result('zip_temp', True, 'info', 'Installed by Steam')
    if not any(rx.search(app_dir) for rx in ZIP_TEMP_RX):
        return result('zip_temp', True, 'info', 'Running from an extracted folder')
    name = zip_name_from_path(app_dir)
    z = find_zip(name, env)
    if '/AppTranslocation/' in app_dir:
        fix = ('macOS is running Stark World Engine from a hidden read-only copy. Move the Stark World Engine folder out of '
               'Downloads (for example into your home folder or Documents) with Finder, then start it again.')
    else:
        fix = ('Stark World Engine is being run straight from inside the .zip, so Windows only gave it a temporary '
               'copy and your games would be lost. Close this window, right-click the .zip you downloaded, choose '
               '"Extract All...", click Extract, then open the new folder and double-click START_STARK_WORLD_ENGINE.bat '
               'there.')
    action = {'type': 'open_explorer', 'path': z or os.path.join(home(), 'Downloads')}
    return result('zip_temp', False, 'blocker', 'Stark World Engine has to be extracted from the zip first', fix,
                  detail=f'running from {app_dir}' + (f'; zip found at {z}' if z else ''), action=action)


# ---------------- 2. folder problems ----------------
CLOUD_RX = re.compile(r'[\\/](onedrive[^\\/]*|dropbox[^\\/]*|google ?drive|my drive|icloud ?drive[^\\/]*|'
                      r'mobile documents|pcloud ?drive|box sync)([\\/]|$)', re.I)


def documents_dir():
    d = None
    if IS_WIN:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as k:
                d = os.path.expandvars(winreg.QueryValueEx(k, 'Personal')[0])
        except OSError:
            d = None
    return d or os.path.join(home(), 'Documents')


def suggested_home(env=None):
    """Documents\\StarkWorldEngine, unless Documents itself is synced; then %USERPROFILE%\\StarkWorldEngine."""
    docs = documents_dir()
    if docs and not in_cloud(docs, env):
        return os.path.join(docs, 'StarkWorldEngine')
    return os.path.join(home(), 'StarkWorldEngine')


def in_cloud(path, env=None):
    env = os.environ if env is None else env
    if CLOUD_RX.search(path):
        return True
    return any(under(path, env.get(k)) for k in ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial'))


def check_folder(app_dir, env=None, can_write=None):
    """-> list: writable, Program Files, path length, cloud sync."""
    env = os.environ if env is None else env
    can_write = writable if can_write is None else can_write
    out = []
    if is_steam(app_dir):        # Steam: test the data folder, never offer to copy the app out of the Steam install
        data = os.path.abspath(env.get('GS_DATA_DIR') or os.path.join(os.path.expanduser('~'), 'Documents', 'Stark World Engine'))
        try:
            os.makedirs(data, exist_ok=True)
        except OSError:
            pass
        if can_write(data):
            return [result('folder_writable', True, 'info', 'Your games folder can save files', detail=data)]
        return [result('folder_writable', False, 'blocker', 'Stark World Engine cannot save into your Documents folder',
                       'Check that Documents\Stark World Engine is not read-only and that your security software allows '
                       'Stark World Engine to write there.', detail=data)]
    in_pf = IS_WIN and any(under(app_dir, pf) for pf in program_files_dirs(env))
    if can_write(app_dir):
        out.append(result('folder_writable', True, 'info', 'The Stark World Engine folder can save files'))
    else:
        dest = suggested_home(env)
        if in_pf:
            title = 'Stark World Engine is in Program Files, where it is not allowed to save your games'
            fix = (f'Move the folder somewhere you own, for example {dest}. When asked, press Y and Stark World Engine '
                   'copies itself there and starts from the copy (the original stays untouched; delete it later '
                   'if you like).')
        else:
            title = 'This folder is read-only, so Stark World Engine cannot save your games'
            fix = (f'Copy the folder somewhere you own, for example {dest} (press Y when asked and it is done for '
                   'you). Or right-click the folder > Properties, untick "Read-only", Apply. If it is on a CD, USB '
                   'stick or network drive, copy it to your PC first.')
        out.append(result('folder_writable', False, 'blocker', title, fix, detail=app_dir,
                          action={'type': 'relocate', 'dest': dest}))
    if len(app_dir) > LONG_PATH:
        out.append(result('path_length', False, 'warn', f'The folder path is very long ({len(app_dir)} characters)',
                          'Windows has trouble with very long file paths and saving could fail. Move the Stark World Engine '
                          f'folder to a short place such as {suggested_home(env)}.', detail=app_dir))
    else:
        out.append(result('path_length', True, 'info', 'Folder path length is fine'))
    if re.search(r'[%^&!]', app_dir):
        out.append(result('path_chars', False, 'warn', 'The folder name contains %, ^, & or !',
                          'Windows treats these characters as commands in START_STARK_WORLD_ENGINE.bat (it expands '
                          '%NAME% pairs before the launcher even runs), so starting can fail or pick the wrong '
                          f'Python. Rename the folder without them, for example {suggested_home(env)}.',
                          detail=app_dir))
    if in_cloud(app_dir, env):
        out.append(result('cloud_sync', False, 'warn', 'Stark World Engine is inside a syncing folder (OneDrive/Dropbox/'
                          'iCloud/Google Drive)',
                          'Sync apps can lock or pause files while you save, and "online-only" files can vanish. It '
                          f'works, but for a smooth ride move the folder to {suggested_home(env)} '
                          '(a folder that is not synced).', detail=app_dir))
    else:
        out.append(result('cloud_sync', True, 'info', 'Not inside a syncing folder'))
    return out


def pick_dest(base):
    """`base`, or `base (2)`, `base (3)`... - never an existing folder (nothing is ever overwritten)."""
    if not os.path.exists(base):
        return base
    for i in range(2, 100):
        c = f'{base} ({i})'
        if not os.path.exists(c):
            return c
    raise OSError(f'no free folder name next to {base}')


def _launcher(folder, name, old):
    """The launcher inside `folder`: today's name, or the pre-28.7 name that an older, updated copy still has."""
    new = os.path.join(folder, name)
    return os.path.join(folder, old) if not os.path.isfile(new) and os.path.isfile(os.path.join(folder, old)) else new


def relocate(app_dir, dest_base, relaunch=True):
    """Copy the app to a new folder (never over an existing one) and optionally start it from there. -> dest."""
    dest = pick_dest(dest_base)
    ignore = shutil.ignore_patterns('launcher.log', LOG_NAME, '__pycache__', '.gs_write_test_*')
    shutil.copytree(app_dir, dest, ignore=ignore)
    if relaunch:
        start_from(dest)
    return dest


def start_from(dest):
    """Start the launcher inside `dest` in a new window (Windows / macOS; Linux users start it themselves)."""
    if IS_WIN:
        os.startfile(_launcher(dest, 'START_STARK_WORLD_ENGINE.bat', 'START_GORGON_STUDIOS.bat'))  # runs from the copy
    elif IS_MAC:
        subprocess.Popen(['open', _launcher(dest, 'start_stark_world_engine.command', 'start_gorgon_studios.command')])


# ---------------- 2b. first start after the Stark rename (28.7): pre-28.7 folders move to Stark names ----------------
MIGRATED = '.stark_migrated.json'
LEGACY_HOME = re.compile(r'GorgonStudios( \(\d+\))?')   # the folder names relocate() used before 28.7


def legacy_pairs(app_dir, env=None):
    """-> [(pre-28.7 folder, its Stark name)]. The app home only while the app runs from it (a pre-28.7 relocate
    target); a source checkout (.git) migrates nothing."""
    env = os.environ if env is None else env
    if os.path.isdir(os.path.join(app_dir, '.git')):
        return []
    local, docs = env.get('LOCALAPPDATA') or '', env.get('GS_DOCS_DIR') or documents_dir()
    pairs = []
    if local:
        pairs += [(os.path.join(local, 'GorgonPrivate', 'gorgon_studios'),
                   os.path.join(local, 'StarkPrivate', 'stark_world_engine')),       # Creator Hub secrets
                  (os.path.join(local, 'GorgonStudios'), os.path.join(local, 'StarkWorldEngine'))]   # Godot download
    pairs.append((os.path.join(docs, 'Gorgon Studios'), os.path.join(docs, 'Stark World Engine')))   # Steam data
    parent, name = os.path.split(os.path.abspath(app_dir).rstrip('\\/'))
    m = LEGACY_HOME.fullmatch(name)
    if m and norm(parent) in {norm(docs), norm(env.get('USERPROFILE') or home())}:
        pairs.append((os.path.abspath(app_dir), os.path.join(parent, 'StarkWorldEngine' + (m.group(1) or ''))))
    return pairs


def tree_hashes(root):
    """{relative path: sha256} of every file under root."""
    out = {}
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            out[os.path.relpath(p, root).replace(os.sep, '/')] = sha256_file(p)
    return out


def copy_verified(old, new):
    """Copy `old` to `new` (which must not exist), check the file count and every SHA-256, then put it in place.
    `old` is only read. -> number of files. Raises OSError when the copy does not match (only `old` remains)."""
    part = new + '.copying'
    shutil.rmtree(part, ignore_errors=True)           # our own unfinished copy from an interrupted earlier start
    shutil.copytree(old, part)
    want, got = tree_hashes(old), tree_hashes(part)
    if want != got:
        shutil.rmtree(part, ignore_errors=True)
        raise OSError(f'copy of {old} did not verify ({len(got)} of {len(want)} files match)')
    os.replace(part, new)
    return len(want)


def migrate_legacy(app_dir, env=None, pairs=None):
    """Copy every pre-28.7 folder that has no Stark twin yet (verified), mark the copy, never touch the original.
    -> [{'from', 'to', 'files', 'ok', 'error'}]."""
    env = os.environ if env is None else env
    if env.get('GS_NO_MIGRATE') == '1':
        return []
    out = []
    for old, new in (legacy_pairs(app_dir, env) if pairs is None else pairs):
        if not os.path.isdir(old) or os.path.exists(new):
            continue
        rec = {'from': old, 'to': new, 'files': 0, 'ok': False, 'error': ''}
        try:
            rec['files'] = copy_verified(old, new)
            rec['ok'] = True
            with open(os.path.join(new, MIGRATED), 'w', encoding='utf-8') as f:
                json.dump({'from': old, 'files': rec['files'], 'verified': 'count + sha256',
                           'at': datetime.datetime.now().isoformat(timespec='seconds'), 'told': False}, f, indent=1)
        except OSError as e:
            rec['error'] = str(e)
        out.append(rec)
    return out


def migration_notes(pairs):
    """One-time message per migrated folder: the old one is no longer used and may be deleted. -> lines."""
    lines = []
    for _, new in pairs:
        p = os.path.join(new, MIGRATED)
        try:
            with open(p, encoding='utf-8') as f:
                rec = json.load(f)
            if rec.get('told'):
                continue
            rec['told'] = True
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(rec, f, indent=1)
        except (OSError, ValueError):
            continue
        lines.append(f'Your files were copied to {new} and checked ({rec.get("files")} files, all identical). '
                     f'Stark World Engine uses that folder from now on. The old folder {rec.get("from")} was not '
                     'changed; once you are happy, you can delete it yourself.')
    return lines


def legacy_home_target(app_dir, env=None):
    """The Stark folder to start from when this app runs from a pre-28.7 home that has been copied; else None."""
    for old, new in legacy_pairs(app_dir, env):
        if norm(old) == norm(app_dir) and os.path.isfile(os.path.join(new, MIGRATED)):
            return new
    return None


# ---------------- 3. missing / corrupt files ----------------
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(files, built=None):
    """For build_release.py: {archive name: bytes} -> files_manifest.json bytes (name -> sha256 + size)."""
    entries = {}
    for name in sorted(files):
        n = name.replace('\\', '/')
        if MANIFEST_SKIP.search(n):
            continue
        data = files[name]
        entries[n] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
    if isinstance(built, tuple):
        built = datetime.datetime(*built).isoformat()
    doc = {'format': 1, 'product': 'Stark World Engine', 'built': built, 'files': entries}
    return (json.dumps(doc, indent=1, sort_keys=True) + '\n').encode('utf-8')


def write_manifest(root, built=None):
    """Write files_manifest.json for an unpacked folder (tests / manual builds). -> path."""
    files = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in ('__pycache__', 'My_Games')]
        for fn in fns:
            p = os.path.join(dp, fn)
            with open(p, 'rb') as f:
                files[os.path.relpath(p, root).replace('\\', '/')] = f.read()
    path = os.path.join(root, MANIFEST)
    with open(path, 'wb') as f:
        f.write(build_manifest(files, built))
    return path


def load_manifest(app_dir):
    try:
        with open(os.path.join(app_dir, MANIFEST), encoding='utf-8') as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) and isinstance(doc.get('files'), dict) else None
    except (OSError, ValueError):
        return None


def folder_age_s(app_dir):
    try:
        return time.time() - os.stat(app_dir).st_ctime
    except OSError:
        return None


def check_files(app_dir, quick=True, critical_only=False):
    man = load_manifest(app_dir)
    if man is None:
        return result('files', True, 'info', 'File check skipped (no files_manifest.json in this build)')
    missing, corrupt = [], []
    for name, meta in man['files'].items():
        if MANIFEST_SKIP.search(name):
            continue
        p = os.path.join(app_dir, *name.split('/'))
        try:
            st = os.stat(p)
        except OSError:
            missing.append(name)
            continue
        size = meta.get('size') if isinstance(meta, dict) else None
        digest = meta.get('sha256') if isinstance(meta, dict) else meta
        if size is not None and st.st_size != size:
            corrupt.append(name)
        elif digest and (not quick or name in CRITICAL or (not critical_only and name.endswith(('.exe', '.dll')))):
            try:
                if sha256_file(p) != digest:
                    corrupt.append(name)
            except OSError:
                missing.append(name)
    if not missing and not corrupt:
        return result('files', True, 'info', f'All {len(man["files"])} program files are present and intact')
    bad = missing + corrupt
    severity = 'blocker' if any(n in CRITICAL for n in bad) else 'warn'
    parts = []
    if missing:
        parts.append('missing: ' + ', '.join(missing[:8]) + (' ...' if len(missing) > 8 else ''))
    if corrupt:
        parts.append('damaged: ' + ', '.join(corrupt[:8]) + (' ...' if len(corrupt) > 8 else ''))
    fix = ('Some Stark World Engine files are ' + '; '.join(parts) + '. Download the zip again from your purchase page '
           '(same link, free), extract it into a NEW folder with "Extract All...", and start from there. Your games '
           'in My_Games can be copied across.')
    age = folder_age_s(app_dir)
    risky = any(n.lower().endswith(('.exe', '.dll', '.pyd', '.bat', '.ps1', '.cmd', '.command', '.sh', '.py'))
                for n in missing)
    if missing and (risky or (age is not None and age < 3 * 3600)):
        fix += ' ' + AV_FIX
    title = ('Stark World Engine files are missing or damaged' if severity == 'blocker'
             else 'A few Stark World Engine files are missing or damaged')
    return result('files', False, severity, title, fix, detail='; '.join(parts))


# ---------------- 4. bundled runtime ----------------
def check_runtime(app_dir, quick=True, fix=True, probe=True):
    """Windows bundled python.org runtime: _pth has the app folder (auto-repaired), python.exe starts.
    probe=False skips starting python.exe (the launcher has just proved it starts when it runs the doctor)."""
    rt = os.path.join(app_dir, 'runtime', 'python')
    exe = os.path.join(rt, 'python.exe')
    if not IS_WIN:
        return result('runtime', True, 'info', 'Uses the Python installed on this computer')
    if not os.path.isfile(exe):
        getter = os.path.join(app_dir, 'runtime', 'get_python.ps1')
        fix = ('No bundled Python here; the launcher uses an installed Python 3.10+ or offers to fetch the official '
               'one (runtime\\get_python.ps1, about 11 MB, kept inside this folder).') if os.path.isfile(getter) else \
              'No bundled Python here; the launcher uses an installed Python 3.10+.'
        return result('runtime', True, 'info', 'Bundled Python not present (that is fine)', fix)
    pths = sorted(glob.glob(os.path.join(rt, 'python3*._pth')))
    fixed = False
    if not pths:
        return result('runtime', False, 'warn', 'The bundled Python is missing its settings file (._pth)',
                      'Extract the zip again into a new folder. ' + AV_FIX, detail=rt)
    pth = pths[0]
    try:
        with open(pth, encoding='utf-8-sig', errors='replace') as f:
            text = f.read()
        lines = [ln.strip() for ln in text.splitlines()]
        need = []
        if PTH_LINE not in lines and '../..' not in lines:
            need.append(PTH_LINE)  # the app folder (studio_app.py, licensing.py, ...)
        if os.path.isdir(os.path.join(rt, 'Lib', 'site-packages')) and not any(
                ln.replace('/', '\\').lower() == 'lib\\site-packages' for ln in lines):
            need.append('Lib\\site-packages')  # bundled numpy: without it the engines quietly fall back to slow mode
        if fix and need:
            bak = pth + '.bak'
            if not os.path.exists(bak):
                shutil.copy2(pth, bak)
            with open(pth, 'a', encoding='ascii', newline='') as f:
                f.write(('' if text.endswith(('\n', '\r')) or not text else '\r\n') + '\r\n'.join(need) + '\r\n')
            fixed = True
    except OSError as e:
        return result('runtime', False, 'warn', 'Could not check the bundled Python settings file',
                      'Make sure the folder is not read-only, or extract the zip again.', detail=str(e))
    if norm(sys.executable) == norm(exe) or not probe:
        ok_run = True
    else:
        rc, out = run([exe, '-I', '-c', 'import sys;print(sys.version_info>=(3,10))'], timeout=8)
        ok_run = rc == 0 and 'True' in out
    if not ok_run:
        return result('runtime', False, 'warn', 'The bundled Python could not start',
                      'The launcher will try an installed Python instead. To fix the bundled one, extract the zip '
                      'again into a new folder. ' + AV_FIX, auto_fixed=fixed, detail=exe)
    title = 'Bundled Python works' + (' (its app-folder line was missing and has been put back; backup kept '
                                      'as .bak)' if fixed else '')
    return result('runtime', True, 'info', title, auto_fixed=fixed, detail=pth)


# ---------------- 5. ports / already running ----------------
def can_bind(port, host='127.0.0.1'):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def probe_studio(port, timeout=0.6):
    """-> /api/info dict when a Stark World Engine server answers on 127.0.0.1:port, else None."""
    try:
        req = urllib.request.Request(f'http://127.0.0.1:{port}/api/info', headers={'Host': f'127.0.0.1:{port}'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as r:
            info = json.loads(r.read(1 << 20).decode('utf-8', 'replace'))
        return info if isinstance(info, dict) and 'version' in info and 'moods' in info else None
    except Exception:  # noqa: BLE001 - anything that is not our studio is "not our studio"
        return None


def listen_pids(ports):
    """-> {port: pid string} for listening TCP sockets (Windows netstat; elsewhere lsof)."""
    ports, pids = set(ports), {}
    if IS_WIN:
        rc, out = run(['netstat', '-ano', '-p', 'TCP'], timeout=6)
        for line in out.splitlines():
            f = line.split()
            if len(f) >= 5 and f[0].upper() == 'TCP':
                m = re.search(r':(\d+)$', f[1])
                if m and int(m.group(1)) in ports and re.search(r':0$|\*:\*$', f[2]):
                    pids.setdefault(int(m.group(1)), f[-1])
    else:
        rc, out = run(['lsof', '-nP', '-iTCP', '-sTCP:LISTEN', '-Fpn'], timeout=6)
        pid = None
        for line in out.splitlines():
            if line.startswith('p'):
                pid = line[1:]
            elif line.startswith('n') and pid:
                m = re.search(r':(\d+)$', line)
                if m and int(m.group(1)) in ports:
                    pids.setdefault(int(m.group(1)), pid)
    return pids


def process_cmdline(pid):
    """Best effort: the command line of a process; '' when unknown."""
    if IS_WIN:
        try:  # fast path: NtQueryInformationProcess(ProcessCommandLineInformation=60), Windows 8.1+
            import ctypes
            from ctypes import wintypes

            class UNICODE_STRING(ctypes.Structure):
                _fields_ = [('Length', wintypes.USHORT), ('MaximumLength', wintypes.USHORT),
                            ('Buffer', ctypes.c_void_p)]
            k32, ntdll = ctypes.WinDLL('kernel32', use_last_error=True), ctypes.WinDLL('ntdll')
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                        wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
            h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
            if h:
                try:
                    size = wintypes.ULONG(0)
                    ntdll.NtQueryInformationProcess(h, 60, None, 0, ctypes.byref(size))
                    if size.value:
                        buf = ctypes.create_string_buffer(size.value)
                        if ntdll.NtQueryInformationProcess(h, 60, buf, size, ctypes.byref(size)) == 0:
                            us = UNICODE_STRING.from_buffer(buf)
                            if us.Buffer and us.Length:
                                return ctypes.wstring_at(us.Buffer, us.Length // 2)
                finally:
                    k32.CloseHandle(h)
        except Exception:  # noqa: BLE001 - fall back to PowerShell below
            pass
        rc, out = run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
                       f'(Get-CimInstance Win32_Process -Filter "ProcessId={int(pid)}").CommandLine'], timeout=6)
        return out.strip() if rc == 0 else ''
    rc, out = run(['ps', '-o', 'command=', '-p', str(int(pid))], timeout=3)
    return out.strip() if rc == 0 else ''


def same_folder_instance(app_dir, port):
    """True when the studio listening on `port` was started from `app_dir` (its command line names it)."""
    pid = listen_pids([port]).get(port)
    if not pid:
        return False
    raw = process_cmdline(pid)
    cmd = os.path.normcase(raw.replace('/', os.sep)) if IS_WIN else raw
    root = os.path.normcase(os.path.abspath(app_dir))
    if 'studio_app.py' not in cmd:
        return False
    if os.path.join(root, 'studio_app.py') in cmd:
        return True
    m = re.search(r'"([^"]*studio_app\.py)"|(\S*studio_app\.py)', raw, re.I)  # short 8.3 vs long names
    script = (m.group(1) or m.group(2)) if m else ''
    if script and os.path.isabs(script):
        try:
            return os.path.normcase(os.path.realpath(script)) == os.path.normcase(
                os.path.realpath(os.path.join(app_dir, 'studio_app.py')))
        except OSError:
            return False
    if not IS_WIN:  # macOS/Linux launchers start "studio_app.py" relative to the app folder: compare the cwd
        cwd = ''
        try:
            cwd = os.readlink(f'/proc/{int(pid)}/cwd')
        except OSError:
            rc, out = run(['lsof', '-a', '-p', str(int(pid)), '-d', 'cwd', '-Fn'], timeout=3)
            cwd = next((ln[1:] for ln in out.splitlines() if ln.startswith('n')), '')
        return bool(cwd) and os.path.realpath(cwd) == os.path.realpath(app_dir)
    return False


def port_owners(ports):
    """-> {port: 'name (PID n)'} for listening sockets. Windows: netstat + tasklist; macOS/Linux: lsof."""
    owners = {}
    ports = set(ports)
    if IS_WIN:
        pids = listen_pids(ports)
        names = {}
        for port, pid in pids.items():
            if pid not in names:
                rc, t = run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'], timeout=5)
                m = re.match(r'\s*"([^"]+)"', t or '')
                names[pid] = m.group(1) if m else 'unknown program'
            owners[port] = f'{names[pid]} (PID {pid})'
    else:
        rc, out = run(['lsof', '-nP', '-iTCP', '-sTCP:LISTEN', '-Fpcn'], timeout=6)
        pid = cmd = None
        for line in out.splitlines():
            if line.startswith('p'):
                pid = line[1:]
            elif line.startswith('c'):
                cmd = line[1:]
            elif line.startswith('n'):
                m = re.search(r':(\d+)$', line)
                if m and int(m.group(1)) in ports:
                    owners.setdefault(int(m.group(1)), f'{cmd or "unknown program"} (PID {pid})')
    return owners


def check_ports(app_dir, ports=PORTS, running_port=None, find_owners=True, is_same_folder=None):
    """Free port in 8505-8520? This Stark World Engine already running? Who holds the ports when all are taken?

    A studio started from THIS folder -> open the browser to it (no second copy on the same My_Games).
    A studio from another folder -> start this one on a free port; with no free port, open the running one."""
    is_same_folder = same_folder_instance if is_same_folder is None else is_same_folder
    ports = list(ports)
    busy = [p for p in ports if p != running_port and not can_bind(p)]
    free = [p for p in ports if p not in busy]
    if running_port is not None:
        return result('ports', True, 'info', f'Studio running on port {running_port}')
    found = {}
    if busy:
        def look(p):
            info = probe_studio(p)
            if info:
                found[p] = info
        ts = [threading.Thread(target=look, args=(p,), daemon=True) for p in busy]
        for t in ts:
            t.start()
        for t in ts:
            t.join(2.0)
    here = [p for p in sorted(found) if is_same_folder(app_dir, p)]
    if here or (found and not free):
        p = (here or sorted(found))[0]
        url = f'http://127.0.0.1:{p}/'
        return result('already_running', False, 'info', 'Stark World Engine is already running - opening it in your '
                      'browser', f'It is already open at {url}. To restart it, close the other Stark World Engine window '
                      'first.', detail=url, action={'type': 'open_browser', 'url': url})
    if free:
        detail = f'{len(free)} of {len(ports)} ports free'
        if found:
            p = sorted(found)[0]
            return result('ports', True, 'info', f'Another Stark World Engine copy ({found[p].get("version")}) is '
                          f'running on port {p}; this one will use another port', detail=detail)
        return result('ports', True, 'info', 'A free port is available', detail=detail)
    owners = port_owners(ports) if find_owners else {}
    progs = sorted(set(owners.values()))
    who = ', '.join(progs) if progs else 'other programs'
    return result('ports', False, 'blocker', f'All ports {ports[0]}-{ports[-1]} are in use by {who}',
                  f'Stark World Engine needs one free port between {ports[0]} and {ports[-1]}. Close {who} (or restart '
                  'the PC), then start Stark World Engine again.', detail=json.dumps(owners))


# ---------------- 6. browser ----------------
def find_browsers(env=None):
    env = os.environ if env is None else env
    found = []
    if IS_WIN:
        local = env.get('LOCALAPPDATA', '')
        rel = {'Chrome': r'Google\Chrome\Application\chrome.exe', 'Edge': r'Microsoft\Edge\Application\msedge.exe',
               'Firefox': r'Mozilla Firefox\firefox.exe'}
        for name, r in rel.items():
            cands = [os.path.join(pf, r) for pf in program_files_dirs(env)] + ([os.path.join(local, r)] if local else [])
            if any(os.path.isfile(c) for c in cands):
                found.append(name)
        if len(found) < 3:
            try:
                import winreg
                for name, exe in (('Chrome', 'chrome.exe'), ('Edge', 'msedge.exe'), ('Firefox', 'firefox.exe')):
                    if name in found:
                        continue
                    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                        try:
                            with winreg.OpenKey(hive, r'Software\Microsoft\Windows\CurrentVersion\App Paths\\' + exe) as k:
                                if os.path.isfile(winreg.QueryValueEx(k, '')[0].strip('"')):
                                    found.append(name)
                                    break
                        except OSError:
                            continue
            except ImportError:
                pass
    elif IS_MAC:
        apps = {'Chrome': 'Google Chrome.app', 'Edge': 'Microsoft Edge.app', 'Firefox': 'Firefox.app',
                'Safari': 'Safari.app'}
        for name, a in apps.items():
            if any(os.path.isdir(os.path.join(d, a)) for d in ('/Applications', os.path.expanduser('~/Applications'),
                                                               '/System/Applications', '/System/Cryptexes/App/System/Applications')):
                found.append(name)
    else:
        for name, cmds in (('Chrome', ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser')),
                           ('Edge', ('microsoft-edge', 'microsoft-edge-stable')), ('Firefox', ('firefox',))):
            if any(shutil.which(c) for c in cmds):
                found.append(name)
    return found


def check_browser(env=None):
    found = find_browsers(env)
    if found:
        return result('browser', True, 'info', 'Browser found: ' + ', '.join(found), WEBGL2_FIX)
    return result('browser', False, 'warn', 'No Chrome, Edge or Firefox found',
                  'Stark World Engine opens in your web browser and works best in Chrome, Edge or Firefox (free). Install '
                  'one of them, then start again. ' + WEBGL2_FIX)


# ---------------- 7. disk + clock ----------------
def check_disk(app_dir, free_bytes=None):
    try:
        free = shutil.disk_usage(app_dir).free if free_bytes is None else free_bytes
    except OSError:
        return result('disk', True, 'info', 'Disk space could not be checked')
    gb = free / 1024 ** 3
    if free < LOW_DISK:
        return result('disk', False, 'warn', f'Low disk space ({gb:.2f} GB free)',
                      'Saving games and exports needs room. Free up at least 1 GB (empty the Recycle Bin, remove '
                      'old downloads), or move Stark World Engine to a drive with more space.')
    return result('disk', True, 'info', f'{gb:.1f} GB free')


def build_time(app_dir):
    man = load_manifest(app_dir)
    if man and man.get('built'):
        try:
            return datetime.datetime.fromisoformat(str(man['built'])).timestamp()
        except (ValueError, OverflowError, OSError):
            pass
    try:
        return os.path.getmtime(os.path.join(app_dir, 'studio_app.py'))
    except OSError:
        return None


def check_clock(app_dir, now=None):
    now = time.time() if now is None else now
    built = build_time(app_dir)
    when = datetime.datetime.fromtimestamp(now).strftime('%Y-%m-%d')
    wrong = datetime.datetime.fromtimestamp(now).year < 2024 or (built is not None and (
        now < built - 2 * 86400 or now > built + 20 * 365 * 86400))
    if wrong:
        return result('clock', False, 'warn', f"This computer's clock looks wrong (it says {when})",
                      'Nothing in Stark World Engine depends on it, but save dates and backups will look odd. Set the '
                      'clock automatically: Windows Settings > Time & language > Date & time > "Set time '
                      'automatically" (Mac: System Settings > General > Date & Time).')
    return result('clock', True, 'info', 'Clock looks right')


# ---------------- 8. Studio tier extras ----------------
def wizard_hint(app_dir):
    for bat in ('SETUP_STARK_WORLD_ENGINE.bat', 'SETUP_STARK_WORLD_ENGINE.bat'):   # the second: copies made before 28.7
        if os.path.isfile(os.path.join(app_dir, bat)):
            return f'Run the Setup Wizard (double-click {bat}); it walks you through it.'
    return 'Run the Setup Wizard (setup_wizard.py in this folder); it walks you through it.'


def find_ffmpeg(app_dir):
    cands = []
    try:
        with open(os.path.join(app_dir, 'config.json'), encoding='utf-8-sig') as f:
            v = json.load(f).get('ffmpeg')
        if isinstance(v, str):
            cands.append(v)
    except (OSError, ValueError, AttributeError):
        pass
    cands.append(shutil.which('ffmpeg') or '')
    local = os.environ.get('LOCALAPPDATA', '')
    if local:
        cands += sorted(glob.glob(os.path.join(local, 'Microsoft', 'WinGet', 'Packages', 'Gyan.FFmpeg*', '*', 'bin',
                                               'ffmpeg.exe')), reverse=True)
    cands += ['/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg', '/usr/bin/ffmpeg']
    return next((c for c in cands if c and os.path.isfile(c)), None)


def find_obs():
    cands = [os.path.join(pf, 'obs-studio', 'bin', '64bit', 'obs64.exe') for pf in program_files_dirs()]
    cands += ['/Applications/OBS.app/Contents/MacOS/OBS', shutil.which('obs') or '', shutil.which('obs64') or '']
    return next((c for c in cands if c and os.path.exists(c)), None)


def firewall_rule_present():
    """Read-only. True/False, or None when it cannot be told (not Windows / netsh unavailable)."""
    if not IS_WIN:
        return None
    rc, out = run(['netsh', 'advfirewall', 'firewall', 'show', 'rule', f'name={HUB_RULE}'], timeout=5)
    if rc is None:
        return None
    return HUB_RULE.lower() in out.lower()


def check_studio_tier(app_dir):
    if not os.path.isfile(os.path.join(app_dir, 'obs_bridge.py')):
        return []
    hint = wizard_hint(app_dir)
    out = []
    ff = find_ffmpeg(app_dir)
    out.append(result('ffmpeg', True, 'info', 'FFmpeg found', detail=ff) if ff else
               result('ffmpeg', False, 'warn', 'FFmpeg is not installed yet (needed by the creator tools: auto-editor, '
                      'captions, beat sync)', hint))
    obs = find_obs()
    out.append(result('obs', True, 'info', 'OBS Studio found', detail=obs) if obs else
               result('obs', False, 'info', 'OBS Studio is not installed yet (only needed for recording/streaming)',
                      hint))
    fw = firewall_rule_present()
    if fw is True:
        out.append(result('firewall', True, 'info', 'Phone hub is allowed through the firewall'))
    elif fw is False:
        out.append(result('firewall', False, 'info', 'Your phone cannot reach the Creator Hub yet (firewall)',
                          hint + ' It asks once before allowing port 8510 on your home network only.'))
    return out


# ---------------- 9. license key ----------------
def check_key(app_dir):
    key = os.path.join(app_dir, 'key.txt')
    lic_py = os.path.join(app_dir, 'licensing.py')
    if not os.path.isfile(key):
        return result('license', True, 'info', 'No key installed: running as the Free edition')
    if not os.path.isfile(lic_py):
        return result('license', True, 'info', 'Key found (licensing.py missing, cannot check it)')
    try:
        spec = importlib.util.spec_from_file_location('gs_doctor_licensing', lic_py)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        lic = mod.load_license(app_dir)
    except Exception as e:  # noqa: BLE001 - a broken licensing module must not stop the doctor
        return result('license', False, 'warn', 'Your key could not be checked', 'The studio still runs as the '
                      'Free edition. Extract the zip again into a new folder.', detail=repr(e))
    if lic.get('registered'):
        return result('license', True, 'info', f"Key valid: {str(lic.get('tier', '')).title()} edition")
    return result('license', False, 'warn', 'Your key (key.txt) is not valid, so the studio runs as Free',
                  'Nothing is lost: everything in the Free edition works. To unlock your edition, open the '
                  'studio, click Activate and paste the WHOLE key you were given (it starts with GS1- '
                  'and is one long line), or unzip your Pro / Studio download again: it already contains key.txt.',
                  detail=str(lic.get('reason', '')))


# ---------------- run ----------------
LAUNCH_BUDGET = 3.0      # seconds: the launcher never waits longer than this for the doctor


def run_checks(app_dir=None, quick=True, running_port=None, env=None, fix=True, launch=False, out=None):
    """-> list of {id, ok, severity, title, fix_text, auto_fixed, detail[, action]}. Never raises.
    launch=True (the double-click start): only the quick, local checks, in blocker-first order: no netstat/tasklist
    owner lookup, no PowerShell, no firewall/browser/key/FFmpeg scan, no runtime self-test and only the critical
    files hashed. The in-app "Fix my setup" panel runs the full set. `out` (a list) is filled as checks finish,
    so a caller with a deadline can use what is done so far."""
    app_dir = os.path.abspath(app_dir or APP_DIR)
    if launch:
        steps = [
            lambda: [check_zip_temp(app_dir, env)],
            lambda: check_folder(app_dir, env),
            lambda: [check_files(app_dir, quick, critical_only=True)],
            lambda: [check_runtime(app_dir, quick, fix, probe=False)],
            lambda: [check_ports(app_dir, running_port=running_port, find_owners=False)],
            lambda: [check_disk(app_dir)],
            lambda: [check_clock(app_dir)],
        ]
    else:
        steps = [
            lambda: [check_zip_temp(app_dir, env)],
            lambda: check_folder(app_dir, env),
            lambda: [check_files(app_dir, quick)],
            lambda: [check_runtime(app_dir, quick, fix)],
            lambda: [check_ports(app_dir, running_port=running_port)],
            lambda: [check_browser(env)],
            lambda: [check_disk(app_dir)],
            lambda: [check_clock(app_dir)],
            lambda: check_studio_tier(app_dir),
            lambda: [check_key(app_dir)],
        ]
    out = [] if out is None else out
    for i, step in enumerate(steps):
        try:
            out.extend(step())
        except Exception as e:  # noqa: BLE001 - one broken check never hides the others
            out.append(result(f'check_{i}', True, 'info', 'A check could not run', detail=repr(e)))
    return out


def run_launch_checks(app_dir, env=None, budget=None):
    """The launcher's checks under a hard time budget. -> (results, finished). Past the budget the results so far
    are used (blocker checks run first) and the studio starts anyway; the rest keeps running in a daemon thread."""
    env = os.environ if env is None else env
    try:
        budget = float(env.get('GS_DOCTOR_BUDGET') or (LAUNCH_BUDGET if budget is None else budget))
    except ValueError:
        budget = LAUNCH_BUDGET
    if budget != budget:  # NaN
        budget = LAUNCH_BUDGET
    budget = min(30.0, max(0.5, budget))  # 'inf' / 1e9 / negatives would crash or hang the launch
    out, done = [], threading.Event()

    def work():
        try:
            r = run_checks(app_dir, quick=True, env=env, launch=True, out=out)
            if r is not out:                   # a replaced run_checks (tests) returns its own list
                out.extend(r or [])
        finally:
            done.set()
    threading.Thread(target=work, daemon=True).start()
    finished = done.wait(budget)
    res = list(out)
    if not finished:
        res.append(result('budget', True, 'info', f'Setup check stopped after {budget:g} s (this PC is busy); '
                          'starting anyway', detail='the full check is in the studio: Fix my setup'))
    return res, finished


def has_blocker(results):
    return any(not r['ok'] and r['severity'] == 'blocker' for r in results)


# ---------------- console + log ----------------
def wrap(text, first, rest, width=78):
    return textwrap.fill(text, width=width, initial_indent=first, subsequent_indent=rest)


def ascii_text(s):
    return str(s).encode('ascii', 'replace').decode('ascii')


def report_lines(results):
    lines = []
    tag = {'blocker': '[STOP]', 'warn': '[TIP ]', 'info': '[NOTE]'}
    shown = [r for r in results if not r['ok'] or r['auto_fixed']]
    ok_n = sum(1 for r in results if r['ok'])
    for r in sorted(shown, key=lambda r: {'blocker': 0, 'warn': 1, 'info': 2}.get(r['severity'], 3)):
        t = '[FIXED]' if r['ok'] and r['auto_fixed'] else tag.get(r['severity'], '[NOTE]')
        lines.append(wrap(ascii_text(r['title']), f'  {t:<7} ', ' ' * 10))
        if r['fix_text'] and not r['ok']:
            lines.append(wrap(ascii_text(r['fix_text']), ' ' * 10, ' ' * 10))
    lines.insert(0, f'  Setup check: {ok_n} of {len(results)} OK.')
    return lines


def log_path(app_dir):
    p = os.path.join(app_dir, LOG_NAME)
    if writable(app_dir):
        return p
    return os.path.join(tempfile.gettempdir(), 'stark_world_engine_' + LOG_NAME)


def write_log(app_dir, results, elapsed, extra=''):
    p = log_path(app_dir)
    try:
        if os.path.isfile(p) and os.path.getsize(p) > LOG_MAX:
            with open(p, 'rb') as f:
                f.seek(-LOG_MAX // 2, 2)
                tail = f.read()
            with open(p, 'wb') as f:
                f.write(tail)
        with open(p, 'a', encoding='utf-8') as f:
            f.write(f'==== {datetime.datetime.now().isoformat(timespec="seconds")} doctor ({elapsed:.2f} s) '
                    f'Python {sys.version.split()[0]} {sys.executable}\n    folder: {app_dir}\n')
            for r in results:
                state = 'OK   ' if r['ok'] else r['severity'].upper().ljust(5)
                f.write(f'  {state} {r["id"]:<16} {r["title"]}' + (' [auto-fixed]' if r['auto_fixed'] else '')
                        + (f' | {r["detail"]}' if r.get('detail') else '') + '\n')
                if not r['ok'] and r['fix_text']:
                    f.write(f'        fix: {r["fix_text"]}\n')
            if extra:
                f.write(f'  {extra}\n')
        return p
    except OSError:
        return None


def ask(question, env=None):
    """Y/N in the console. GS_AUTO_YES=1 answers yes; no console (EOF) answers no."""
    env = os.environ if env is None else env
    if env.get('GS_AUTO_YES') == '1':
        print(f'  {question} (Y/N): Y  (GS_AUTO_YES)')
        return True
    try:
        a = input(f'  {question} (Y/N): ')
    except (EOFError, KeyboardInterrupt, OSError):
        return False
    return a.strip().lower().startswith('y')


def open_explorer(path):
    try:
        if IS_WIN:
            if os.path.isfile(path):
                subprocess.Popen(['explorer', '/select,', path])
            else:
                os.startfile(path)
        elif IS_MAC:
            subprocess.Popen(['open', '-R', path] if os.path.isfile(path) else ['open', path])
        else:
            subprocess.Popen(['xdg-open', os.path.dirname(path) if os.path.isfile(path) else path])
    except OSError:
        pass


def launch(app_dir, env=None):
    """The launcher flow. -> exit code (0 start, 2 blocker, 3 already running, 4 relocated)."""
    env = os.environ if env is None else env
    pairs = [] if env.get('GS_NO_MIGRATE') == '1' else legacy_pairs(app_dir, env)
    for r in migrate_legacy(app_dir, env, pairs):
        if not r['ok']:
            print(wrap(f'Could not copy {r["from"]} to {r["to"]} ({ascii_text(r["error"])}); the old folder stays in '
                       'use and nothing was changed. It is tried again at the next start.', '  ', '  '))
    for line in migration_notes(pairs):
        print(wrap(ascii_text(line), '  ', '  '))
    target = legacy_home_target(app_dir, env)
    if target:
        print(wrap(f'Starting Stark World Engine from {target}. A new window opens.', '  ', '  '))
        if env.get('GS_DOCTOR_NO_RELAUNCH') != '1':
            start_from(target)
        write_log(target, [], 0.0, f'started from the pre-28.7 folder {app_dir}')   # the old folder stays as it was
        return 4
    t0 = time.time()
    results, finished = run_launch_checks(app_dir, env)
    elapsed = time.time() - t0
    print('\n'.join(report_lines(results)))
    by = {r['id']: r for r in results}
    extra, code = '', 0
    ar = by.get('already_running')
    zt = by.get('zip_temp')
    fw = by.get('folder_writable')
    if zt and not zt['ok']:
        print()
        if ask('Open the folder with the .zip now so you can extract it?', env):
            open_explorer(zt['action']['path'])
        extra, code = 'blocker: zip_temp', 2
    elif fw and not fw['ok']:
        dest = pick_dest(fw['action']['dest'])
        print()
        print(wrap(f'I can copy Stark World Engine to {dest} and start it from there. The original folder is not '
                   'changed.', '  ', '  '))
        if ask('Copy it there now?', env):
            try:
                made = relocate(app_dir, fw['action']['dest'], relaunch=env.get('GS_DOCTOR_NO_RELAUNCH') != '1')
                print(f'  Done. Stark World Engine now lives in {made}. A new window starts it from there.')
                extra, code = f'relocated to {made}', 4
            except OSError as e:
                print(f'  The copy did not work ({ascii_text(e)}). Please copy the folder by hand.')
                extra, code = f'relocate failed: {e!r}', 2
        else:
            extra, code = 'blocker: folder_writable (user said no)', 2
    elif has_blocker(results):
        extra, code = 'blocker: ' + ','.join(r['id'] for r in results if not r['ok'] and r['severity'] == 'blocker'), 2
    elif ar:
        if env.get('GS_NO_BROWSER') != '1':
            try:
                import webbrowser
                webbrowser.open(ar['action']['url'])
            except Exception:  # noqa: BLE001
                pass
        extra, code = f'already running at {ar["action"]["url"]}', 3
    if not finished:
        extra = (extra + '; ' if extra else '') + f'time budget exceeded after {elapsed:.1f} s: started anyway'
    p = write_log(app_dir, results, elapsed, extra)
    if code == 2:
        print()
        print('  Stark World Engine did not start because of the [STOP] item above.')
        print('  Fix it, then start Stark World Engine again.' + (f'  (Details: {p})' if p else ''))
    return code


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='Stark World Engine setup doctor')
    ap.add_argument('--app-dir', default=APP_DIR)
    ap.add_argument('--launch', action='store_true')
    ap.add_argument('--full', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    app_dir = os.path.abspath(a.app_dir)
    if a.launch:
        return launch(app_dir)
    t0 = time.time()
    results = run_checks(app_dir, quick=not a.full)
    elapsed = time.time() - t0
    write_log(app_dir, results, elapsed)
    if a.json:
        print(json.dumps(results, indent=1))
    else:
        print('\n'.join(report_lines(results)))
        print(f'  ({elapsed:.2f} s)')
    return 2 if has_blocker(results) else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001 - the doctor must never stop the studio from starting
        try:
            with open(os.path.join(tempfile.gettempdir(), 'stark_world_engine_' + LOG_NAME), 'a', encoding='utf-8') as f:
                f.write(f'{datetime.datetime.now().isoformat()} doctor crashed: {e!r}\n')
        except OSError:
            pass
        sys.exit(1)
