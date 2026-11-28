"""gltf_io.py - glTF 2.0 / GLB skeletons and animations in, GLB out.

Import: the skin's joints become a Skeleton (non-joint ancestors such as Blender's 'Armature' node, including its
rotation and 0.01 scale, are baked in, so every skeleton comes out rigid, in metres, +Y up). Every animation is
sampled at a fixed fps (LINEAR / STEP / CUBICSPLINE supported) into a Clip.
Export: a GLB with the joint hierarchy, a skin, a tiny proxy mesh (so engines create a skeleton asset) and one glTF
animation per clip; clip events and meta ride along in animation.extras.
"""
import base64, json, os, struct

import numpy as np

from . import qmath as Q
from .clip import Clip
from .rig import Skeleton

CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NC = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT2': 4, 'MAT3': 9, 'MAT4': 16}
NORM = {np.int8: 127.0, np.uint8: 255.0, np.int16: 32767.0, np.uint16: 65535.0}


class GltfDoc:
    def __init__(self, path):
        self.path = path
        self.base = os.path.dirname(os.path.abspath(path))
        with open(path, 'rb') as f:
            data = f.read()
        self.glb_bin = None
        if data[:4] == b'glTF':
            off = 12
            while off + 8 <= len(data):
                n, t = struct.unpack('<I4s', data[off:off + 8])
                chunk = data[off + 8:off + 8 + n]
                if t == b'JSON':
                    self.j = json.loads(chunk.decode('utf-8'))
                elif t == b'BIN\x00':
                    self.glb_bin = chunk
                off += 8 + n
        else:
            self.j = json.loads(data.decode('utf-8'))
        if not isinstance(getattr(self, 'j', None), dict) or 'asset' not in self.j:
            raise ValueError(f'{os.path.basename(path)} is not a glTF 2.0 file')
        self._bufs = {}

    def buffer(self, i):
        if i not in self._bufs:
            b = self.j['buffers'][i]
            uri = b.get('uri')
            if uri is None:
                self._bufs[i] = self.glb_bin or b''
            elif uri.startswith('data:'):
                self._bufs[i] = base64.b64decode(uri.split(',', 1)[1])
            else:
                from urllib.parse import unquote
                p = os.path.normpath(os.path.join(self.base, unquote(uri)))
                if os.path.commonpath([p, self.base]) != self.base:
                    raise ValueError('glTF buffer URI points outside its folder: ' + uri)
                with open(p, 'rb') as f:
                    self._bufs[i] = f.read()
        return self._bufs[i]

    def accessor(self, i, as_float=True):
        a = self.j['accessors'][i]
        dt = np.dtype(CT[a['componentType']])
        n = NC[a['type']]
        cnt = a['count']
        if 'bufferView' not in a:
            out = np.zeros((cnt, n), dt)
        else:
            bv = self.j['bufferViews'][a['bufferView']]
            buf = self.buffer(bv['buffer'])
            off = bv.get('byteOffset', 0) + a.get('byteOffset', 0)
            stride = bv.get('byteStride') or dt.itemsize * n
            if stride == dt.itemsize * n:
                out = np.frombuffer(buf, dt, cnt * n, off).reshape(cnt, n).copy()
            else:
                out = np.empty((cnt, n), dt)
                for k in range(cnt):
                    out[k] = np.frombuffer(buf, dt, n, off + k * stride)
        if 'sparse' in a:
            sp = a['sparse']
            ib = self.j['bufferViews'][sp['indices']['bufferView']]
            idx = np.frombuffer(self.buffer(ib['buffer']), CT[sp['indices']['componentType']], sp['count'],
                                ib.get('byteOffset', 0) + sp['indices'].get('byteOffset', 0))
            vb = self.j['bufferViews'][sp['values']['bufferView']]
            val = np.frombuffer(self.buffer(vb['buffer']), dt, sp['count'] * n,
                                vb.get('byteOffset', 0) + sp['values'].get('byteOffset', 0)).reshape(-1, n)
            out = out.copy()
            out[idx] = val
        if as_float:
            f = out.astype(np.float64)
            if a.get('normalized') and dt.type in NORM:
                f = np.maximum(f / NORM[dt.type], -1.0)
            return f
        return out


# ---------------------------------------------------------------- node transforms
def _node_trs(n):
    if 'matrix' in n:
        m = np.asarray(n['matrix'], np.float64).reshape(4, 4).T
        t = m[:3, 3].copy()
        sc = np.linalg.norm(m[:3, :3], axis=0)
        r = m[:3, :3] / np.where(sc > 1e-12, sc, 1.0)
        if np.linalg.det(r) < 0:
            sc[0] *= -1
            r[:, 0] *= -1
        return t, Q.q_from_matrix(r), sc
    return (np.asarray(n.get('translation', [0, 0, 0]), np.float64),
            Q.q_normalize(np.asarray(n.get('rotation', [0, 0, 0, 1]), np.float64)),
            np.asarray(n.get('scale', [1, 1, 1]), np.float64))


def _mats(t, r, s):
    """TRS arrays [..., 3/4/3] -> 4x4 matrices [..., 4, 4]."""
    m = np.zeros(np.shape(t)[:-1] + (4, 4))
    m[..., :3, :3] = Q.q_to_matrix(r) * np.asarray(s)[..., None, :]
    m[..., :3, 3] = t
    m[..., 3, 3] = 1.0
    return m


def _rigid(m):
    """Split 4x4 matrices [..., 4, 4] into (rotation quaternion, translation), dropping scale (mirroring folded)."""
    m = np.asarray(m, np.float64)
    shp = m.shape[:-2]
    mm = m.reshape(-1, 4, 4)
    a = mm[:, :3, :3]
    sc = np.linalg.norm(a, axis=-2)
    r = a / np.where(sc > 1e-12, sc, 1.0)[:, None, :]
    neg = np.linalg.det(r) < 0
    if np.any(neg):
        r = r.copy()
        r[neg, :, 0] *= -1
    return Q.q_from_matrix(r).reshape(shp + (4,)), mm[:, :3, 3].reshape(shp + (3,)).copy()


def _parents(j):
    par = {}
    for i, n in enumerate(j.get('nodes', [])):
        for c in n.get('children', []):
            par[c] = i
    return par


def _scene_roots(j):
    sc = j.get('scenes', [{}])[j.get('scene', 0)] if j.get('scenes') else None
    if sc and sc.get('nodes'):
        return list(sc['nodes'])
    par = _parents(j)
    return [i for i in range(len(j.get('nodes', []))) if i not in par]


def _joint_set(doc, skin):
    j = doc.j
    if j.get('skins'):
        return list(j['skins'][min(skin, len(j['skins']) - 1)]['joints'])
    # no skin: every animated node plus its ancestors, minus pure mesh leaves
    par = _parents(j)
    anim_nodes = {c['target']['node'] for a in j.get('animations', []) for c in a.get('channels', [])
                  if c.get('target', {}).get('node') is not None}
    out = set()
    for i in anim_nodes:
        while i is not None:
            out.add(i)
            i = par.get(i)
    return sorted(out)


def _skeleton_from(doc, joints, node_t, node_r, node_s):
    """Rigid skeleton for `joints` given per-node local TRS arrays [..., N, *] (rest or one frame)."""
    j = doc.j
    N = len(j['nodes'])
    par = _parents(j)
    G = [None] * N

    def glob(i):
        if G[i] is None:
            m = _mats(node_t[..., i, :], node_r[..., i, :], node_s[..., i, :])
            p = par.get(i)
            G[i] = m if p is None else glob(p) @ m
        return G[i]

    jset = set(joints)
    gr, gp = [], []
    for i in joints:
        r, t = _rigid(glob(i))
        gr.append(r)
        gp.append(t)
    gr = np.stack(gr, axis=-2)
    gp = np.stack(gp, axis=-2)
    parents = []
    for i in joints:
        p = par.get(i)
        while p is not None and p not in jset:
            p = par.get(p)
        parents.append(joints.index(p) if p is not None else -1)
    return parents, gr, gp


def _local_from_global(parents, gr, gp):
    lr = np.empty_like(gr)
    lt = np.empty_like(gp)
    for k, p in enumerate(parents):
        if p < 0:
            lr[..., k, :] = gr[..., k, :]
            lt[..., k, :] = gp[..., k, :]
        else:
            ip = Q.q_conj(gr[..., p, :])
            lr[..., k, :] = Q.q_mul(ip, gr[..., k, :])
            lt[..., k, :] = Q.q_rotate(ip, gp[..., k, :] - gp[..., p, :])
    return Q.q_normalize(lr), lt


def _names(doc, joints):
    seen, out = {}, []
    for i in joints:
        nm = doc.j['nodes'][i].get('name') or f'node{i}'
        if nm in seen:
            seen[nm] += 1
            nm = f'{nm}.{seen[nm]:03d}'
        else:
            seen[nm] = 0
        out.append(nm)
    return out


def _rest_arrays(doc):
    nodes = doc.j['nodes']
    t = np.zeros((len(nodes), 3))
    r = np.zeros((len(nodes), 4))
    s = np.ones((len(nodes), 3))
    for i, n in enumerate(nodes):
        t[i], r[i], s[i] = _node_trs(n)
    return t, r, s


def load_gltf_skeleton(path, skin=0, doc=None):
    doc = doc or GltfDoc(path)
    joints = _joint_set(doc, skin)
    if not joints:
        raise ValueError(f'{os.path.basename(path)} has no skin and no animated nodes')
    t, r, s = _rest_arrays(doc)
    parents, gr, gp = _skeleton_from(doc, joints, t, r, s)
    lr, lt = _local_from_global(parents, gr, gp)
    names = _names(doc, joints)
    skel = Skeleton(names, parents, lt, lr, meta={'source': os.path.basename(path), 'format': 'gltf'})
    info = {'skin_joints': names, 'inverse_bind': None, 'node_index': joints}
    skins = doc.j.get('skins') or []
    sk = skins[min(skin, len(skins) - 1)] if skins else None
    if sk and sk.get('inverseBindMatrices') is not None:
        info['inverse_bind'] = np.round(doc.accessor(sk['inverseBindMatrices']), 7).tolist()
    par = _parents(doc.j)
    p = par.get(joints[0])
    if p is not None:
        n = doc.j['nodes'][p]
        info['armature'] = {'name': n.get('name'), 'translation': n.get('translation'),
                            'rotation': n.get('rotation'), 'scale': n.get('scale')}
    return skel, info


def _sample(times, values, interp, path, ts):
    times = np.asarray(times, np.float64).reshape(-1)
    K = len(times)
    w = values.shape[-1]
    if interp == 'CUBICSPLINE':
        v = values.reshape(K, 3, w)
        vin, val, vout = v[:, 0], v[:, 1], v[:, 2]
    else:
        val = values.reshape(K, w)
    if K == 1:
        out = np.repeat(val[:1], len(ts), 0)
        return Q.q_normalize(out) if path == 'rotation' else out
    i = np.clip(np.searchsorted(times, ts, side='right') - 1, 0, K - 2)
    t0, t1 = times[i], times[i + 1]
    dt = np.where(t1 - t0 > 1e-12, t1 - t0, 1.0)
    a = np.clip((ts - t0) / dt, 0.0, 1.0)
    before = ts <= times[0]
    after = ts >= times[-1]
    if interp == 'STEP':
        out = val[i].copy()
        out[after] = val[-1]
    elif interp == 'CUBICSPLINE':
        a2, a3 = a * a, a * a * a
        h00 = 2 * a3 - 3 * a2 + 1
        h10 = a3 - 2 * a2 + a
        h01 = -2 * a3 + 3 * a2
        h11 = a3 - a2
        out = (h00[:, None] * val[i] + h10[:, None] * dt[:, None] * vout[i] + h01[:, None] * val[i + 1] +
               h11[:, None] * dt[:, None] * vin[i + 1])
        out[after] = val[-1]
        out[before] = val[0]
    elif path == 'rotation':
        out = Q.q_slerp(val[i], val[i + 1], a)
        out[after] = val[-1]
        out[before] = val[0]
    else:
        out = val[i] * (1 - a)[:, None] + val[i + 1] * a[:, None]
        out[after] = val[-1]
        out[before] = val[0]
    return Q.q_normalize(out) if path == 'rotation' else out


def load_gltf_clips(path, fps=30.0, skin=0, only=None, loop_hint=True):
    """-> (Skeleton, [Clip]) with one clip per glTF animation (names in `only` if given)."""
    doc = GltfDoc(path)
    skel, info = load_gltf_skeleton(path, skin, doc)
    joints = info['node_index']
    rt, rr, rs = _rest_arrays(doc)
    clips = []
    for ai, a in enumerate(doc.j.get('animations', [])):
        name = a.get('name') or f'anim{ai}'
        if only and name not in only:
            continue
        chans = []
        tmax = 0.0
        for c in a.get('channels', []):
            tg = c.get('target', {})
            if tg.get('node') is None or tg.get('path') not in ('translation', 'rotation', 'scale'):
                continue
            smp = a['samplers'][c['sampler']]
            tin = doc.accessor(smp['input'])[:, 0]
            vout = doc.accessor(smp['output'])
            chans.append((tg['node'], tg['path'], tin, vout, smp.get('interpolation', 'LINEAR')))
            if len(tin):
                tmax = max(tmax, float(tin[-1]))
        F = max(1, int(round(tmax * fps)) + 1)
        ts = np.arange(F) / fps
        T = np.broadcast_to(rt, (F,) + rt.shape).copy()
        R = np.broadcast_to(rr, (F,) + rr.shape).copy()
        S = np.broadcast_to(rs, (F,) + rs.shape).copy()
        for node, pth, tin, vout, interp in chans:
            v = _sample(tin, vout, interp, pth, ts)
            {'translation': T, 'rotation': R, 'scale': S}[pth][:, node] = v
        parents, gr, gp = _skeleton_from(doc, joints, T, R, S)
        lr, lt = _local_from_global(parents, gr, gp)
        lr = Q.q_continuity(lr, axis=0)
        loop = bool(loop_hint and ('loop' in name.lower() or 'cycle' in name.lower()))
        clips.append(Clip(name, skel.names, lr, lt, fps=fps, loop=loop,
                          meta={'source': os.path.basename(path), 'source_format': 'gltf', 'source_anim': name},
                          events=(a.get('extras') or {}).get('events')))
    return skel, clips


# ---------------------------------------------------------------- GLB writer
class _Bin:
    def __init__(self):
        self.bin = bytearray()
        self.views, self.accs = [], []

    def add(self, arr, ctype, typ, target=None, minmax=False, normalized=False):
        arr = np.ascontiguousarray(arr)
        while len(self.bin) % 4:
            self.bin.append(0)
        bv = {'buffer': 0, 'byteOffset': len(self.bin), 'byteLength': arr.nbytes}
        if target:
            bv['target'] = target
        self.bin += arr.tobytes()
        self.views.append(bv)
        a = {'bufferView': len(self.views) - 1, 'componentType': ctype, 'count': int(arr.shape[0]), 'type': typ}
        if normalized:
            a['normalized'] = True
        if minmax:
            flat = arr.reshape(arr.shape[0], -1)
            a['min'] = [float(x) for x in flat.min(0)]
            a['max'] = [float(x) for x in flat.max(0)]
        self.accs.append(a)
        return len(self.accs) - 1


def write_glb(path, skel, clips=(), rename=None, proxy_mesh=True, all_translations=False, generator=None):
    """Write skeleton + clips as GLB. rename: {engine joint name: exported name}. Clips must use the same joint
    names as `skel` (missing joints keep their rest pose). Returns bytes written."""
    rename = rename or {}
    b = _Bin()
    names = [rename.get(n, n) for n in skel.names]
    nodes = []
    for i, n in enumerate(skel.names):
        nd = {'name': names[i], 'translation': [float(x) for x in skel.rest_t[i]],
              'rotation': [float(x) for x in Q.q_normalize(skel.rest_r[i])]}
        if skel.children[i]:
            nd['children'] = [int(c) for c in skel.children[i]]
        nodes.append(nd)
    gr, gp = skel.rest_globals()
    ibm = np.stack([np.linalg.inv(_mats(gp[i], gr[i], np.ones(3))).T.reshape(16) for i in range(len(skel))])
    j = {'asset': {'version': '2.0', 'generator': generator or 'Stark World Engine stark_anim'}, 'scene': 0,
         'nodes': nodes, 'scenes': [{'nodes': [int(r) for r in skel.roots]}]}
    if proxy_mesh:
        root = skel.index('pelvis') if skel.has('pelvis') else skel.roots[0]
        c = gp[root]
        pos = np.array([c + [-0.02, 0, 0], c + [0.02, 0, 0], c + [0, 0.02, 0]], np.float32)
        nrm = np.array([[0, 0, 1]] * 3, np.float32)
        jn = np.array([[root, 0, 0, 0]] * 3, np.uint16)
        wt = np.array([[1, 0, 0, 0]] * 3, np.float32)
        j['meshes'] = [{'name': 'proxy', 'primitives': [{'attributes': {
            'POSITION': b.add(pos, 5126, 'VEC3', 34962, minmax=True), 'NORMAL': b.add(nrm, 5126, 'VEC3', 34962),
            'JOINTS_0': b.add(jn, 5123, 'VEC4', 34962), 'WEIGHTS_0': b.add(wt, 5126, 'VEC4', 34962)}}]}]
        j['skins'] = [{'joints': list(range(len(skel))), 'inverseBindMatrices': b.add(ibm.astype(np.float32), 5126,
                                                                                         'MAT4'),
                       'skeleton': int(skel.roots[0])}]
        j['nodes'].append({'name': 'proxy_mesh', 'mesh': 0, 'skin': 0})
        j['scenes'][0]['nodes'].append(len(j['nodes']) - 1)
    anims = []
    for clip in clips:
        idx = {n: k for k, n in enumerate(clip.joints)}
        ts = (np.arange(clip.frames) / clip.fps).astype(np.float32)
        tin = b.add(ts, 5126, 'SCALAR', minmax=True)
        samplers, channels = [], []
        rot = Q.q_continuity(clip.rot, axis=0)
        for i, n in enumerate(skel.names):
            k = idx.get(n)
            if k is None:
                continue
            samplers.append({'input': tin, 'output': b.add(rot[:, k].astype(np.float32), 5126, 'VEC4'),
                             'interpolation': 'LINEAR'})
            channels.append({'sampler': len(samplers) - 1, 'target': {'node': i, 'path': 'rotation'}})
            moving = np.max(np.abs(clip.pos[:, k] - skel.rest_t[i])) > 1e-6
            if all_translations or (moving and (skel.parents[i] < 0 or n in ('pelvis',) or
                                                skel.parents[i] == skel.roots[0])):
                samplers.append({'input': tin, 'output': b.add(clip.pos[:, k].astype(np.float32), 5126, 'VEC3'),
                                 'interpolation': 'LINEAR'})
                channels.append({'sampler': len(samplers) - 1, 'target': {'node': i, 'path': 'translation'}})
        extras = {'stark': {'loop': clip.loop, 'fps': clip.fps, 'tags': clip.tags, 'meta': clip.meta}}
        if clip.events:
            extras['events'] = clip.events
        anims.append({'name': clip.name, 'samplers': samplers, 'channels': channels, 'extras': extras})
    if anims:
        j['animations'] = anims
    while len(b.bin) % 4:
        b.bin.append(0)
    if b.bin:
        j['buffers'] = [{'byteLength': len(b.bin)}]
        j['bufferViews'] = b.views
        j['accessors'] = b.accs
    js = json.dumps(j, separators=(',', ':')).encode('utf-8')
    while len(js) % 4:
        js += b' '
    total = 12 + 8 + len(js) + (8 + len(b.bin) if b.bin else 0)
    out = bytearray(b'glTF' + struct.pack('<II', 2, total))
    out += struct.pack('<I4s', len(js), b'JSON') + js
    if b.bin:
        out += struct.pack('<I4s', len(b.bin), b'BIN\x00') + bytes(b.bin)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(out)
    return len(out)
