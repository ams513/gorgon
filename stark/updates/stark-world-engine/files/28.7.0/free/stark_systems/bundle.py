"""stark_systems/bundle.py: build the one-file Stark World Engine Systems runtime (js/*.js + the child-safety lexicon) that
cartridges, the studio and the reference player inline. No network, no build tools: plain concatenation.

  python -m stark_systems.bundle [--out FILE] [--check]     write the bundle (default: print size); --check runs
                                                              node --check on it when node is installed
"""
import glob, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
JS_DIR = os.path.join(HERE, 'js')
LEXICON = os.path.join(HERE, 'safety', 'lexicon.json')


def js_files():
    return sorted(glob.glob(os.path.join(JS_DIR, '[0-9][0-9]_*.js')))


NODES = os.path.join(HERE, 'schema', 'node_catalog.json')


def _json_js(path):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    js = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    return js.replace('<', '\\u003c').replace('>', '\\u003e').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


def lexicon_js():
    """The child-safety word list and the node-graph catalog, embedded as data (no fetch, no network)."""
    return ("(function(r){r.GSYS_LEXICON=" + _json_js(LEXICON) + ";r.GSYS_NODES=" + _json_js(NODES) +
            ";})(typeof window!=='undefined'?window:globalThis);\n")


def bundle(names=None):
    """-> the runtime as one JavaScript string. names: only these module files (e.g. ['00_core.js', '05_safety.js'])."""
    from . import __version__
    parts = [f'/* Stark World Engine Systems runtime {__version__} - offline, no network. Child-safety rules: CHILD_SAFETY.md */\n',
             lexicon_js()]
    for p in js_files():
        if names and os.path.basename(p) not in names:
            continue
        with open(p, encoding='utf-8') as f:
            parts.append(f.read().rstrip() + '\n')
    js = ''.join(parts)
    if '</script' in js.lower() or '<!--' in js:
        raise ValueError('a runtime module contains "</script" or "<!--"; it cannot be inlined in a page')
    return js


def node():
    return shutil.which('node') or (r'C:\Program Files\nodejs\node.exe' if os.path.isfile(r'C:\Program Files\nodejs\node.exe') else None)


def run_node(code, names=None, timeout=120):
    """Run `code` after the bundle in node; -> (returncode, stdout, stderr). Tests use it; buyers never need node."""
    exe = node()
    if not exe:
        raise RuntimeError('node is not installed')
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'gsys_test.js')
        with open(p, 'w', encoding='utf-8') as f:
            f.write(bundle(names) + '\n;(function(){\n' + code + '\n})();\n')
        r = subprocess.run([exe, p], capture_output=True, text=True, encoding='utf-8', timeout=timeout)
        return r.returncode, r.stdout, r.stderr


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out')
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args(argv)
    js = bundle()
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            f.write(js)
    print(f'{len(js) / 1024:.0f} KB, {len(js_files())} modules')
    if a.check:
        exe = node()
        if not exe:
            print('node not installed: skipped')
            return 0
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'b.js')
            with open(p, 'w', encoding='utf-8') as f:
                f.write(js)
            r = subprocess.run([exe, '--check', p], capture_output=True, text=True)
            print('node --check:', 'ok' if r.returncode == 0 else r.stderr[-600:])
            return r.returncode
    return 0


if __name__ == '__main__':
    sys.exit(main())
