"""fbx.py - FBX import through a converter that is ALREADY installed (Blender or FBX2glTF). Nothing is ever
downloaded: without a converter the creator is told how to get one or to export glTF / BVH instead.

Detection order: GS_FBX2GLTF / GS_BLENDER environment variables, PATH (FBX2glTF, fbx2gltf, blender), then the usual
install folders (Blender Foundation, Steam, Microsoft Store, macOS /Applications, Linux /usr/bin, /snap/bin).
"""
import glob, os, shutil, subprocess, sys, tempfile

BLENDER_SCRIPT = r'''
import bpy, sys
argv = sys.argv[sys.argv.index("--") + 1:]
src, dst = argv[0], argv[1]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=src, automatic_bone_orientation=False, use_anim=True)
bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", export_animations=True, export_skins=True,
                          export_yup=True, export_apply=False)
'''


class FbxUnavailable(RuntimeError):
    pass


def _candidates():
    env = [os.environ.get('GS_FBX2GLTF'), os.environ.get('GS_BLENDER')]
    yield from ((p, 'fbx2gltf' if i == 0 else 'blender') for i, p in enumerate(env) if p)
    for n in ('FBX2glTF', 'fbx2gltf', 'FBX2glTF-windows-x64'):
        p = shutil.which(n)
        if p:
            yield p, 'fbx2gltf'
    p = shutil.which('blender')
    if p:
        yield p, 'blender'
    pats = []
    for pf in (os.environ.get('ProgramFiles', r'C:\Program Files'), os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
               os.path.expandvars(r'%LOCALAPPDATA%\Programs')):
        pats += [os.path.join(pf, 'Blender Foundation', 'Blender*', 'blender.exe'),
                 os.path.join(pf, 'Steam', 'steamapps', 'common', 'Blender', 'blender.exe'),
                 os.path.join(pf, 'FBX2glTF', 'FBX2glTF*.exe')]
    pats += [os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\WindowsApps\blender*.exe'),
             '/Applications/Blender.app/Contents/MacOS/Blender', '/usr/bin/blender', '/usr/local/bin/blender',
             '/snap/bin/blender', '/usr/bin/FBX2glTF', '/usr/local/bin/FBX2glTF']
    for pat in pats:
        for p in sorted(glob.glob(pat), reverse=True):
            yield p, 'fbx2gltf' if 'fbx2gltf' in os.path.basename(p).lower() else 'blender'


def find_converter():
    """-> {'kind': 'blender'|'fbx2gltf', 'path': ...} or None."""
    for p, kind in _candidates():
        if p and os.path.isfile(p):
            return {'kind': kind, 'path': p}
    return None


def fbx_to_glb(path, timeout=300):
    """Convert an FBX to a GLB in a fresh temp folder -> (glb path, converter kind)."""
    conv = find_converter()
    if not conv:
        raise FbxUnavailable('FBX needs a converter and none is installed. Install Blender (free, blender.org) or '
                             'FBX2glTF, or export your animation as glTF/GLB or BVH from your tool and import that.')
    out_dir = tempfile.mkdtemp(prefix='gs_anim_fbx_')
    dst = os.path.join(out_dir, 'converted.glb')
    if conv['kind'] == 'fbx2gltf':
        cmd = [conv['path'], '--binary', '--input', os.path.abspath(path), '--output', os.path.join(out_dir, 'converted')]
    else:
        cmd = [conv['path'], '-b', '--factory-startup', '--python-expr', BLENDER_SCRIPT, '--', os.path.abspath(path), dst]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0)
    if not os.path.isfile(dst):
        raise FbxUnavailable(f"{conv['kind']} could not convert {os.path.basename(path)}: "
                             f"{(r.stderr or r.stdout or '').strip()[-400:]}")
    return dst, conv['kind']
