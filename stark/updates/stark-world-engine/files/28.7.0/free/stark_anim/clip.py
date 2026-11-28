"""clip.py - the Stark World Engine animation clip (.gclip.json): what every importer produces, every cleanup tool edits, the
cartridge runtime plays and the exporters translate for UE5 / Godot.

A clip is uniformly sampled: rot [F, J, 4] local joint rotations (xyzw, relative to the parent, glTF space) and
pos [F, J, 3] local joint translations, for the joint names in `joints`. Root motion, when extracted, lives in
`motion` [F, 4] = world x, y, z and heading (radians about +Y) of the character root; the body tracks are then in
place. Events are [{'t': seconds, 'type': 'footstep'|'hit'|..., ...}].

On disk the tracks are compressed: joints whose value never changes are stored once ("const"); moving rotations are
int16-quantised (error < 0.004 degrees) and moving translations are float32, both base64. enc='json' writes plain
lists instead (handy for fixtures and diffs).
"""
import base64, json, math

import numpy as np

from . import qmath as Q

FORMAT = 'stark-clip'
LEGACY_FORMAT = 'gorgon-clip'   # clips saved before 28.7 load unchanged
VERSION = 1
SAFETY_CLASSES = ('neutral', 'harm', 'intimacy')


class Clip:
    def __init__(self, name, joints, rot, pos=None, fps=30.0, loop=False, motion=None, events=None, tags=None,
                 meta=None, rig=None, rest_pos=None):
        self.name = str(name)
        self.joints = list(joints)
        self.rot = Q.q_normalize(np.asarray(rot, np.float64))
        if self.rot.ndim != 3 or self.rot.shape[1:] != (len(self.joints), 4):
            raise ValueError(f'rot must be [frames, {len(self.joints)}, 4], got {self.rot.shape}')
        F = self.rot.shape[0]
        if pos is None:
            rp = np.zeros((len(self.joints), 3)) if rest_pos is None else np.asarray(rest_pos, np.float64)
            pos = np.broadcast_to(rp, (F, len(self.joints), 3)).copy()
        self.pos = np.asarray(pos, np.float64).reshape(F, len(self.joints), 3)
        self.fps = float(fps)
        self.loop = bool(loop)
        self.motion = None if motion is None else np.asarray(motion, np.float64).reshape(F, 4)
        self.events = sorted([dict(e) for e in (events or [])], key=lambda e: e.get('t', 0))
        self.tags = list(tags or [])
        self.meta = dict(meta or {})
        self.rig = rig

    # ------------------------------------------------------------ basics
    @property
    def frames(self):
        return self.rot.shape[0]

    @property
    def duration(self):
        return (self.frames - 1) / self.fps if self.frames > 1 else 0.0

    def jindex(self, name):
        try:
            return self.joints.index(name)
        except ValueError:
            return None

    def copy(self, **over):
        c = Clip(self.name, self.joints, self.rot.copy(), self.pos.copy(), self.fps, self.loop,
                 None if self.motion is None else self.motion.copy(), [dict(e) for e in self.events], list(self.tags),
                 json.loads(json.dumps(self.meta)), self.rig)
        for k, v in over.items():
            setattr(c, k, v)
        return c

    @property
    def safety(self):
        return self.meta.get('safety', 'neutral')

    def sample(self, t):
        """Interpolated local pose at time t (seconds): (rot [J,4], pos [J,3])."""
        F = self.frames
        if F == 1:
            return self.rot[0].copy(), self.pos[0].copy()
        x = t * self.fps
        if self.loop:
            x = x % (F - 1)
        x = min(max(x, 0.0), F - 1.0)
        i = int(math.floor(x))
        j = min(i + 1, F - 1)
        a = x - i
        return Q.q_slerp(self.rot[i], self.rot[j], np.full(len(self.joints), a)), \
            self.pos[i] * (1 - a) + self.pos[j] * a

    def resample(self, fps):
        """New clip at `fps` covering the same duration (slerp between source frames)."""
        fps = float(fps)
        if abs(fps - self.fps) < 1e-9:
            return self.copy()
        n = max(1, int(round(self.duration * fps)) + 1)
        ts = np.arange(n) / fps
        x = np.clip(ts * self.fps, 0, self.frames - 1)
        i = np.floor(x).astype(int)
        j = np.minimum(i + 1, self.frames - 1)
        a = (x - i)
        rot = Q.q_slerp(self.rot[i], self.rot[j], np.repeat(a[:, None], len(self.joints), 1))
        pos = self.pos[i] * (1 - a)[:, None, None] + self.pos[j] * a[:, None, None]
        mot = None
        if self.motion is not None:
            m0, m1 = self.motion[i], self.motion[j]
            yaw = np.unwrap(self.motion[:, 3])
            mot = np.concatenate([m0[:, :3] * (1 - a)[:, None] + m1[:, :3] * a[:, None],
                                  (yaw[i] * (1 - a) + yaw[j] * a)[:, None]], axis=1)
        c = self.copy(rot=rot, pos=pos, motion=mot)
        c.fps = fps
        return c

    def remap(self, joints, rest_rot=None, rest_pos=None):
        """Same motion on a different joint list (missing joints get rest values or identity)."""
        F = self.frames
        rot = Q.quat_identity((F, len(joints)))
        pos = np.zeros((F, len(joints), 3))
        for k, n in enumerate(joints):
            i = self.jindex(n)
            if i is not None:
                rot[:, k] = self.rot[:, i]
                pos[:, k] = self.pos[:, i]
            else:
                if rest_rot is not None:
                    rot[:, k] = rest_rot[k]
                if rest_pos is not None:
                    pos[:, k] = rest_pos[k]
        return self.copy(joints=list(joints), rot=rot, pos=pos)

    # ------------------------------------------------------------ serialisation
    def to_dict(self, enc='i16b64', precision=6):
        F, J = self.frames, len(self.joints)
        q = Q.q_continuity(self.rot, axis=0)
        rconst, ranim = {}, []
        pconst, panim = {}, []
        for j in range(J):
            if np.max(Q.q_angle_between(q[:, j], q[:1, j])) < 1e-5:
                rconst[j] = q[0, j]
            else:
                ranim.append(j)
            if np.max(np.abs(self.pos[:, j] - self.pos[:1, j])) < 1e-6:
                pconst[j] = self.pos[0, j]
            else:
                panim.append(j)

        def r6(v):
            return [round(float(x), precision) for x in v]

        d = {'format': FORMAT, 'version': VERSION, 'name': self.name, 'rig': self.rig, 'fps': self.fps, 'frames': F,
             'loop': self.loop, 'duration': round(self.duration, 6), 'joints': self.joints,
             'rot': {'const': {str(j): r6(v) for j, v in rconst.items()}, 'anim': ranim},
             'pos': {'const': {str(j): r6(v) for j, v in pconst.items()}, 'anim': panim},
             'events': self.events, 'tags': self.tags, 'meta': self.meta}
        ra = q[:, ranim, :] if ranim else np.zeros((F, 0, 4))
        pa = self.pos[:, panim, :] if panim else np.zeros((F, 0, 3))
        if enc == 'json':
            d['rot']['enc'] = d['pos']['enc'] = 'json'
            d['rot']['data'] = [[r6(v) for v in fr] for fr in ra]
            d['pos']['data'] = [[r6(v) for v in fr] for fr in pa]
        else:
            d['rot']['enc'] = 'i16b64'
            d['rot']['data'] = base64.b64encode(np.round(np.clip(ra, -1, 1) * 32767).astype('<i2').tobytes()).decode()
            d['pos']['enc'] = 'f32b64'
            d['pos']['data'] = base64.b64encode(pa.astype('<f4').tobytes()).decode()
        if self.motion is not None:
            if enc == 'json':
                d['motion'] = {'enc': 'json', 'data': [r6(v) for v in self.motion]}
            else:
                d['motion'] = {'enc': 'f32b64', 'data': base64.b64encode(self.motion.astype('<f4').tobytes()).decode()}
        return d

    @classmethod
    def from_dict(cls, d):
        if d.get('format') not in (FORMAT, LEGACY_FORMAT):
            raise ValueError('not a stark-clip document')
        if int(d.get('version', 1)) > VERSION:
            raise ValueError(f"stark-clip version {d.get('version')} is newer than this tool ({VERSION})")
        F, joints = int(d['frames']), d['joints']
        J = len(joints)

        def block(b, width, ident):
            out = np.zeros((F, J, width))
            if ident:
                out[..., 3] = 1.0
            for j, v in b.get('const', {}).items():
                out[:, int(j)] = v
            anim = b.get('anim', [])
            if anim:
                enc = b.get('enc', 'json')
                if enc == 'json':
                    arr = np.asarray(b['data'], np.float64).reshape(F, len(anim), width)
                elif enc == 'i16b64':
                    arr = np.frombuffer(base64.b64decode(b['data']), '<i2').astype(np.float64).reshape(
                        F, len(anim), width) / 32767.0
                elif enc == 'f32b64':
                    arr = np.frombuffer(base64.b64decode(b['data']), '<f4').astype(np.float64).reshape(
                        F, len(anim), width)
                else:
                    raise ValueError('unknown track encoding ' + enc)
                out[:, anim] = arr
            return out

        rot = block(d['rot'], 4, True)
        pos = block(d['pos'], 3, False)
        motion = None
        if d.get('motion'):
            m = d['motion']
            if m.get('enc') == 'f32b64':
                motion = np.frombuffer(base64.b64decode(m['data']), '<f4').astype(np.float64).reshape(F, 4)
            else:
                motion = np.asarray(m['data'], np.float64).reshape(F, 4)
        return cls(d.get('name', 'clip'), joints, rot, pos, d.get('fps', 30), d.get('loop', False), motion,
                   d.get('events'), d.get('tags'), d.get('meta'), d.get('rig'))

    def save(self, path, enc='i16b64'):
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(self.to_dict(enc), f, separators=(',', ':'))
        return path

    @classmethod
    def load(cls, path):
        with open(path, encoding='utf-8') as f:
            return cls.from_dict(json.load(f))

    def summary(self):
        return {'name': self.name, 'frames': self.frames, 'fps': self.fps, 'duration': round(self.duration, 3),
                'loop': self.loop, 'joints': len(self.joints), 'events': len(self.events), 'tags': self.tags,
                'root_motion': self.motion is not None, 'safety': self.safety}


def ensure_safety_class(c):
    if c not in SAFETY_CLASSES:
        raise ValueError(f'safety class must be one of {SAFETY_CLASSES}')
    return c
