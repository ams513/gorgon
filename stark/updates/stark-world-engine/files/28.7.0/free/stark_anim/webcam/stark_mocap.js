/* Stark World Engine Mocap 1.0 - record yourself with the webcam; nothing leaves the PC.
 * Browser pipeline: camera -> pose estimator (MediaPipe Pose Landmarker, local files only; or ReplayEstimator for
 * recorded landmark JSON) -> Stark World Engine space -> live One Euro smoothing -> solver (same maths as stark_anim/mocap.py)
 * -> StarkAnim pose for the live preview -> Recorder keeps landmark NUMBERS only (never images or video).
 *
 *   var mc = new StarkMocap.Session({rig: rigJson, modelBase: '/anim/models/'});
 *   await mc.useMediaPipe({face: true});         // or mc.useReplay(takeJson)
 *   await mc.startCamera(videoElement);           // getUserMedia, video only
 *   mc.onPose = function (pose, frame) { ... };   // pose: StarkAnim.Pose on the Stark World Engine rig
 *   mc.record(); ... var take = mc.stop();        // stark-mocap-take JSON -> studio /api/anim/take
 */
(function (root, factory) {
  var M = factory(root.StarkAnim || (typeof require === 'function' ? require('../runtime/stark_anim.js') : null));
  if (typeof module === 'object' && module.exports) module.exports = M; else root.StarkMocap = M;
})(typeof self !== 'undefined' ? self : this, function (GA) {
  'use strict';
  var Mo = {version: '1.0.0'};
  var LM = ['nose', 'left_eye_inner', 'left_eye', 'left_eye_outer', 'right_eye_inner', 'right_eye', 'right_eye_outer', 'left_ear', 'right_ear',
    'mouth_left', 'mouth_right', 'left_shoulder', 'right_shoulder', 'left_elbow', 'right_elbow', 'left_wrist', 'right_wrist', 'left_pinky', 'right_pinky',
    'left_index', 'right_index', 'left_thumb', 'right_thumb', 'left_hip', 'right_hip', 'left_knee', 'right_knee', 'left_ankle', 'right_ankle',
    'left_heel', 'right_heel', 'left_foot_index', 'right_foot_index'];
  var I = {}; LM.forEach(function (n, k) { I[n] = k; });
  var MIRROR = [0, 4, 5, 6, 1, 2, 3, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15, 18, 17, 20, 19, 22, 21, 24, 23, 26, 25, 28, 27, 30, 29, 32, 31];
  Mo.LANDMARKS = LM; Mo.CONNECTIONS = [[11, 12], [11, 13], [13, 15], [12, 14], [14, 16], [11, 23], [12, 24], [23, 24], [23, 25], [25, 27], [27, 29], [29, 31], [27, 31],
    [24, 26], [26, 28], [28, 30], [30, 32], [28, 32], [15, 19], [15, 17], [17, 19], [16, 20], [16, 18], [18, 20], [0, 7], [0, 8]];

  // ---------------------------------------------------------------- vector / quaternion helpers (Float64 arrays)
  var V = {
    sub: function (a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }, add: function (a, b) { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; },
    mid: function (a, b) { return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2]; }, scale: function (a, k) { return [a[0] * k, a[1] * k, a[2] * k]; },
    dot: function (a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }, len: function (a) { return Math.hypot(a[0], a[1], a[2]); },
    cross: function (a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; },
    norm: function (a) { var l = Math.hypot(a[0], a[1], a[2]) || 1e-12; return [a[0] / l, a[1] / l, a[2] / l]; }
  };
  var m = GA.math;
  function q(x, y, z, w) { return new Float64Array([x, y, z, w]); }
  function qmul(a, b) { var o = new Float64Array(4); m.qmul(o, 0, a, 0, b, 0); return o; }
  function qconj(a) { return q(-a[0], -a[1], -a[2], a[3]); }
  function qrot(a, v) { var o = [0, 0, 0]; m.qrot(a, 0, v, 0, o, 0); return o; }
  function qslerp(a, b, t) { var o = new Float64Array(4); m.qslerp(o, 0, a, 0, b, 0, t); return o; }
  function qbetween(a, b) { return Float64Array.from(m.qbetween(a, b, new Float64Array(4))); }
  function qFromMat(R) { // columns
    var m00 = R[0][0], m11 = R[1][1], m22 = R[2][2], tr = m00 + m11 + m22, s;
    if (tr > 0) { s = Math.sqrt(tr + 1) * 2; return norm4([(R[2][1] - R[1][2]) / s, (R[0][2] - R[2][0]) / s, (R[1][0] - R[0][1]) / s, 0.25 * s]); }
    if (m00 > m11 && m00 > m22) { s = Math.sqrt(1 + m00 - m11 - m22) * 2; return norm4([0.25 * s, (R[0][1] + R[1][0]) / s, (R[0][2] + R[2][0]) / s, (R[2][1] - R[1][2]) / s]); }
    if (m11 > m22) { s = Math.sqrt(1 + m11 - m00 - m22) * 2; return norm4([(R[0][1] + R[1][0]) / s, 0.25 * s, (R[1][2] + R[2][1]) / s, (R[0][2] - R[2][0]) / s]); }
    s = Math.sqrt(1 + m22 - m00 - m11) * 2; return norm4([(R[0][2] + R[2][0]) / s, (R[1][2] + R[2][1]) / s, 0.25 * s, (R[1][0] - R[0][1]) / s]);
  }
  function norm4(a) { var l = Math.hypot(a[0], a[1], a[2], a[3]) || 1; return q(a[0] / l, a[1] / l, a[2] / l, a[3] / l); }
  function frame(p, s) { // columns x=p, y in p/s plane, z = x cross y  -> row-major R[r][c]
    var x = V.norm(p), y = V.sub(s, V.scale(x, V.dot(s, x)));
    if (V.len(y) < 1e-8) y = Math.abs(x[2]) < 0.9 ? V.cross(x, [0, 0, 1]) : V.cross(x, [0, 1, 0]);
    y = V.norm(y); var z = V.cross(x, y);
    return [[x[0], y[0], z[0]], [x[1], y[1], z[1]], [x[2], y[2], z[2]]];
  }
  function alignTwo(sp, ss, dp, ds) { // R * sp = dp, R * ss ~ ds  (R = Fd * Fs^T)
    var Fs = frame(sp, ss), Fd = frame(dp, ds), R = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
    for (var r = 0; r < 3; r++) for (var c = 0; c < 3; c++) R[r][c] = Fd[r][0] * Fs[c][0] + Fd[r][1] * Fs[c][1] + Fd[r][2] * Fs[c][2];
    return qFromMat(R);
  }
  Mo._alignTwo = alignTwo;

  // ---------------------------------------------------------------- landmark conversions
  Mo.worldToStark = function (w) { return w.map(function (p) { return [p.x, -p.y, -p.z]; }); };
  Mo.mirrorPoints = function (P) { return MIRROR.map(function (k) { return [-P[k][0], P[k][1], P[k][2]]; }); };

  // 33 landmarks for one posed rig frame (same anatomy model as mocap.landmarks_from_pose): rest references
  function landmarksFromGlobals(sk, g) {
    var c = sk.canon, P = new Array(33), rest = sk.restGlobals();
    function gp(n) { var j = sk.find(n); return [g.p[j * 3], g.p[j * 3 + 1], g.p[j * 3 + 2]]; }
    function gq(n) { var j = sk.find(n); return [g.r[j * 4], g.r[j * 4 + 1], g.r[j * 4 + 2], g.r[j * 4 + 3]]; }
    function rq(n) { var j = sk.find(n); return [rest.r[j * 4], rest.r[j * 4 + 1], rest.r[j * 4 + 2], rest.r[j * 4 + 3]]; }
    var D = qmul(gq('Head'), qconj(rq('Head'))), hp = gp('Head');
    function head(o) { return V.add(hp, qrot(D, o)); }
    P[I.nose] = head([0, 0.07, 0.11]);
    [['left', 1, 'l'], ['right', -1, 'r']].forEach(function (S) {
      var side = S[0], sx = S[1], s = S[2];
      P[I[side + '_eye_inner']] = head([sx * 0.015, 0.10, 0.095]); P[I[side + '_eye']] = head([sx * 0.032, 0.10, 0.09]);
      P[I[side + '_eye_outer']] = head([sx * 0.048, 0.10, 0.08]); P[I[side + '_ear']] = head([sx * 0.075, 0.08, 0.0]);
      P[I['mouth_' + side]] = head([sx * 0.025, 0.03, 0.10]);
      P[I[side + '_shoulder']] = gp('upperarm_' + s); P[I[side + '_elbow']] = gp('lowerarm_' + s); P[I[side + '_wrist']] = gp('hand_' + s);
      P[I[side + '_pinky']] = gp('pinky_01_' + s); P[I[side + '_index']] = gp('index_01_' + s); P[I[side + '_thumb']] = gp('thumb_02_' + s);
      P[I[side + '_hip']] = gp('thigh_' + s); P[I[side + '_knee']] = gp('calf_' + s); P[I[side + '_ankle']] = gp('foot_' + s);
      var fd = qmul(gq('foot_' + s), qconj(rq('foot_' + s)));
      P[I[side + '_heel']] = V.add(gp('foot_' + s), qrot(fd, [0, -0.06, -0.05])); P[I[side + '_foot_index']] = gp('ball_leaf_' + s);
    });
    return P;
  }
  Mo.landmarksFromGlobals = landmarksFromGlobals;

  function frameVectors(P, bend, straight) {
    var L = function (n) { return P[I[n]]; }, mid = function (a, b) { return V.mid(L(a), L(b)); };
    var hips = mid('left_hip', 'right_hip'), sh = mid('left_shoulder', 'right_shoulder'), ears = mid('left_ear', 'right_ear');
    var latH = V.sub(L('left_hip'), L('right_hip')), latS = V.sub(L('left_shoulder'), L('right_shoulder')), latE = V.sub(L('left_ear'), L('right_ear'));
    var fwd = V.cross(latS, V.sub(sh, hips));
    var out = {pelvis: [V.sub(sh, hips), latH], chest: [V.sub(ears, sh), latS], head: [V.cross(V.sub(L('nose'), ears), latE), latE]};
    function bendN(key, a, b, c, fb) {
      var n = V.cross(V.sub(b, a), V.sub(c, b));
      if (!straight && V.len(n) > 0.15 * V.len(V.sub(b, a)) * V.len(V.sub(c, b))) {
        n = V.norm(n);
        if (bend && bend[key] && V.dot(n, bend[key]) < -0.5 && V.dot(n, fb) < 0) n = V.scale(n, -1);
        if (bend) bend[key] = n; return n;
      }
      var f = V.norm(fb); return bend && bend[key] ? bend[key] : f;
    }
    [['l', 'left'], ['r', 'right']].forEach(function (S) {
      var s = S[0], side = S[1], shp = L(side + '_shoulder'), el = L(side + '_elbow'), wr = L(side + '_wrist');
      var na = bendN('arm_' + s, shp, el, wr, V.cross(V.sub(el, shp), fwd));
      out['upperarm_' + s] = [V.sub(el, shp), na]; out['lowerarm_' + s] = [V.sub(wr, el), na];
      out['hand_' + s] = [V.sub(V.mid(L(side + '_index'), L(side + '_pinky')), wr), V.sub(L(side + '_index'), L(side + '_pinky'))];
      var hp = L(side + '_hip'), kn = L(side + '_knee'), an = L(side + '_ankle');
      var nl = bendN('leg_' + s, hp, kn, an, V.cross(V.sub(kn, hp), V.scale(fwd, -1)));
      out['thigh_' + s] = [V.sub(kn, hp), nl]; out['calf_' + s] = [V.sub(an, kn), nl];
      var toe = L(side + '_foot_index'), heel = L(side + '_heel'), lat = V.cross(V.sub(toe, heel), V.sub(an, heel));
      out['foot_' + s] = [V.sub(toe, an), V.cross(lat, V.sub(toe, heel))];
    });
    return out;
  }

  // ---------------------------------------------------------------- solver (mirrors mocap.Solver)
  function Solver(sk) {
    this.sk = sk; this.rest = sk.restGlobals(); this.ref = frameVectors(landmarksFromGlobals(sk, this.rest), null); this.bend = {}; this.calibrated = false;
  }
  // performer holds the rig's rest pose (T-pose): their own landmarks become the rest reference (removes the
  // constant offset between the model's anatomical landmarks and the rig's joints)
  Solver.prototype.calibrate = function (frames) { // frames: [[33 x [x,y,z]], ...] Stark World Engine space
    var P = [], k, c, vals;
    for (k = 0; k < 33; k++) {
      P.push([0, 0, 0]);
      for (c = 0; c < 3; c++) {
        vals = frames.map(function (f) { return f[k][c]; }).sort(function (a, b) { return a - b; });
        P[k][c] = vals[Math.floor(vals.length / 2)];
      }
    }
    var fv = frameVectors(P, null, true), self = this; // T-pose: straight limbs
    Object.keys(fv).forEach(function (k) { if (/^(pelvis|thigh_|calf_|foot_)/.test(k)) self.ref[k] = fv[k]; }); // arms: see mocap.py
    this.calibrated = true; return this;
  };
  Solver.prototype.solve = function (P, pose) { // P: 33 x [x,y,z] Stark World Engine space -> writes pose.r (local rotations)
    var sk = this.sk, cur = frameVectors(P, this.bend), D = {}, self = this, k;
    Object.keys(cur).forEach(function (key) { D[key] = alignTwo(self.ref[key][0], self.ref[key][1], cur[key][0], cur[key][1]); });
    var W = {pelvis: D.pelvis, spine_01: qslerp(D.pelvis, D.chest, 1 / 3), spine_02: qslerp(D.pelvis, D.chest, 2 / 3), spine_03: D.chest,
      neck_01: qslerp(D.chest, D.head, 0.5), Head: D.head};
    this._fitSpine(W, P);
    ['l', 'r'].forEach(function (s) {
      ['upperarm', 'lowerarm', 'hand', 'thigh', 'calf', 'foot'].forEach(function (b) { W[b + '_' + s] = D[b + '_' + s]; });
      W['clavicle_' + s] = qslerp(D.chest, D['upperarm_' + s], 0.15); W['ball_' + s] = D['foot_' + s];
    });
    var n = sk.n, G = new Array(n), rr = this.rest.r;
    for (k = 0; k < n; k++) {
      var i = sk.order[k], nm = sk.names[i], rest = q(rr[i * 4], rr[i * 4 + 1], rr[i * 4 + 2], rr[i * 4 + 3]);
      if (W[nm]) G[i] = qmul(W[nm], rest);
      else if (sk.parents[i] >= 0) G[i] = qmul(G[sk.parents[i]], q(sk.restR[i * 4], sk.restR[i * 4 + 1], sk.restR[i * 4 + 2], sk.restR[i * 4 + 3]));
      else G[i] = q(sk.restR[i * 4], sk.restR[i * 4 + 1], sk.restR[i * 4 + 2], sk.restR[i * 4 + 3]);
    }
    for (i = 0; i < n; i++) {
      var p = sk.parents[i], l = p < 0 ? G[i] : qmul(qconj(G[p]), G[i]);
      if (p < 0) l = q(sk.restR[i * 4], sk.restR[i * 4 + 1], sk.restR[i * 4 + 2], sk.restR[i * 4 + 3]);
      pose.r[i * 4] = l[0]; pose.r[i * 4 + 1] = l[1]; pose.r[i * 4 + 2] = l[2]; pose.r[i * 4 + 3] = l[3];
    }
    return pose;
  };
  Solver.prototype._fitSpine = function (W, P) {
    var sk = this.sk, g = this.rest, idx = function (n) { return sk.find(n); };
    function gp(n) { var j = idx(n); return [g.p[j * 3], g.p[j * 3 + 1], g.p[j * 3 + 2]]; }
    var msh = V.mid(gp('upperarm_l'), gp('upperarm_r')), mhp = V.mid(gp('thigh_l'), gp('thigh_r'));
    var want = V.sub(V.mid(P[I.left_shoulder], P[I.right_shoulder]), V.mid(P[I.left_hip], P[I.right_hip]));
    for (var it = 0; it < 3; it++) {
      var cur = V.add(V.add(qrot(W.pelvis, V.sub(gp('spine_01'), mhp)), qrot(W.spine_01, V.sub(gp('spine_02'), gp('spine_01')))),
        V.add(qrot(W.spine_02, V.sub(gp('spine_03'), gp('spine_02'))), qrot(W.spine_03, V.sub(msh, gp('spine_03')))));
      var S = qbetween(cur, want); if (Math.abs(S[3]) > 0.99999999) break;
      W.spine_01 = qmul(S, W.spine_01); W.spine_02 = qmul(S, W.spine_02);
    }
  };
  Mo.Solver = Solver;

  // hips height: the lower foot rests on the ground (live, smoothed)
  function groundPose(sk, pose, state) {
    var g = GA.computeGlobals(sk, pose), rest = sk.restGlobals(), y = 1e9, ry = 1e9;
    ['foot_l', 'foot_r', 'ball_l', 'ball_r'].forEach(function (n) { var j = sk.find(n); y = Math.min(y, g.p[j * 3 + 1]); ry = Math.min(ry, rest.p[j * 3 + 1]); });
    var dy = ry - y; state.dy = state.dy == null ? dy : state.dy + (dy - state.dy) * 0.35;
    var pi = sk.canon.pelvis, pr = sk.parents[pi], d = [0, state.dy, 0], o = [0, 0, 0];
    if (pr >= 0) { var qi = new Float32Array(4); m.qconj(qi, 0, rest.r, pr * 4); m.qrot(qi, 0, d, 0, o, 0); } else o = d;
    pose.t[pi * 3] = sk.restT[pi * 3] + o[0]; pose.t[pi * 3 + 1] = sk.restT[pi * 3 + 1] + o[1]; pose.t[pi * 3 + 2] = sk.restT[pi * 3 + 2] + o[2];
    return pose;
  }

  // ---------------------------------------------------------------- One Euro (live smoothing)
  function OneEuro(minCutoff, beta, dCutoff) { this.mc = minCutoff == null ? 1.6 : minCutoff; this.beta = beta == null ? 0.6 : beta; this.dc = dCutoff || 1; this.x = null; this.dx = 0; }
  OneEuro.prototype.alpha = function (c, dt) { var tau = 1 / (2 * Math.PI * c); return 1 / (1 + tau / dt); };
  OneEuro.prototype.f = function (x, dt) {
    if (this.x === null) { this.x = x; this.dx = 0; return x; }
    var dx = (x - this.x) / dt, ad = this.alpha(this.dc, dt); this.dx = ad * dx + (1 - ad) * this.dx;
    var a = this.alpha(this.mc + this.beta * Math.abs(this.dx), dt); this.x = a * x + (1 - a) * this.x; return this.x;
  };
  Mo.OneEuro = OneEuro;
  function PointFilter(n, opt) { opt = opt || {}; this.f = []; for (var i = 0; i < n * 3; i++) this.f.push(new OneEuro(opt.minCutoff, opt.beta)); this.last = null; this.visMin = opt.visMin || 0.5; }
  PointFilter.prototype.apply = function (P, vis, dt) {
    var out = [], last = this.last;
    for (var k = 0; k < P.length; k++) {
      var src = (vis && vis[k] < this.visMin && last) ? last[k] : P[k];
      out.push([this.f[k * 3].f(src[0], dt), this.f[k * 3 + 1].f(src[1], dt), this.f[k * 3 + 2].f(src[2], dt)]);
    }
    this.last = out; return out;
  };
  Mo.PointFilter = PointFilter;

  // ---------------------------------------------------------------- 2D -> 3D lifting (fallback when a model gives image points only)
  var BONES = [['left_hip', 'left_knee', 'thigh_l', 'calf_l'], ['left_knee', 'left_ankle', 'calf_l', 'foot_l'], ['right_hip', 'right_knee', 'thigh_r', 'calf_r'],
    ['right_knee', 'right_ankle', 'calf_r', 'foot_r'], ['left_shoulder', 'left_elbow', 'upperarm_l', 'lowerarm_l'], ['left_elbow', 'left_wrist', 'lowerarm_l', 'hand_l'],
    ['right_shoulder', 'right_elbow', 'upperarm_r', 'lowerarm_r'], ['right_elbow', 'right_wrist', 'lowerarm_r', 'hand_r'],
    ['left_ankle', 'left_foot_index', 'foot_l', 'ball_leaf_l'], ['right_ankle', 'right_foot_index', 'foot_r', 'ball_leaf_r']];
  function Lifter(sk, aspect) {
    var g = sk.restGlobals(); function gp(n) { var j = sk.find(n); return [g.p[j * 3], g.p[j * 3 + 1], g.p[j * 3 + 2]]; }
    this.len = BONES.map(function (b) { return V.len(V.sub(gp(b[3]), gp(b[2]))); });
    this.torso = V.len(V.sub(V.mid(gp('upperarm_l'), gp('upperarm_r')), V.mid(gp('thigh_l'), gp('thigh_r'))));
    this.aspect = aspect || 4 / 3; this.scale = null; this.prev = null;
  }
  Lifter.prototype.lift = function (lms) {
    var a = this.aspect, p2 = lms.map(function (l) { return [l.x * a, l.y]; });
    var t = V.len(V.sub(V.mid(p2[11].concat([0]), p2[12].concat([0])), V.mid(p2[23].concat([0]), p2[24].concat([0]))));
    this.scale = this.scale == null ? this.torso / Math.max(t, 1e-6) : Math.min(this.scale, this.torso / Math.max(t, 1e-6)) * 0.98 + 0.02 * this.torso / Math.max(t, 1e-6);
    var s = this.scale, P = lms.map(function (l) { return [l.x * a * s, -l.y * s, -(l.z || 0) * a * s]; }), Z = P.map(function (p) { return p[2]; });
    var hc = V.mid(P[23], P[24]); P = P.map(function (p) { return V.sub(p, hc); }); Z = P.map(function (p) { return p[2]; });
    for (var k = 0; k < BONES.length; k++) {
      var ia = I[BONES[k][0]], ib = I[BONES[k][1]], ln = this.len[k], dx = P[ib][0] - P[ia][0], dy = P[ib][1] - P[ia][1], dz2 = ln * ln - dx * dx - dy * dy;
      var dz = dz2 > 0 ? Math.sqrt(dz2) : 0, sign = Z[ib] - Z[ia] >= 0 ? 1 : -1;
      if (this.prev && dz > 1e-4) { var c0 = P[ia][2] + sign * dz, c1 = P[ia][2] - sign * dz, pz = this.prev[ib][2];
        if (Math.abs(c1 - pz) + 0.02 < Math.abs(c0 - pz) && Math.abs(Z[ib] - Z[ia]) < 0.3 * dz) sign = -sign; }
      P[ib][2] = P[ia][2] + sign * dz;
    }
    this.prev = P; return P;
  };
  Mo.Lifter = Lifter;

  // ---------------------------------------------------------------- face blendshapes -> visemes / expressions
  Mo.faceToMouth = function (bs) { // bs: {categoryName: score} (ARKit names) -> {viseme, jaw, weights}
    var g = function (k) { return bs[k] || 0; };
    var w = {aa: g('jawOpen') * (1 - g('mouthFunnel')), oh: g('mouthFunnel'), ou: g('mouthPucker'), PP: g('mouthClose') + g('mouthPressLeft') * 0.5 + g('mouthPressRight') * 0.5,
      E: (g('mouthSmileLeft') + g('mouthSmileRight')) / 2 * g('jawOpen') + g('mouthStretchLeft') * 0.5, ih: (g('mouthSmileLeft') + g('mouthSmileRight')) / 2 * (1 - g('jawOpen')),
      FF: g('mouthRollLower') * 0.8, sil: 0};
    var best = 'sil', bw = 0.15; Object.keys(w).forEach(function (k) { if (w[k] > bw) { bw = w[k]; best = k; } });
    return {viseme: best, jaw: g('jawOpen'), weights: w, expressions: bs};
  };

  // ---------------------------------------------------------------- estimators
  function ReplayEstimator(take) { this.take = take; this.k = 0; this.kind = 'replay'; this.t0 = take.frames.length ? take.frames[0].t : 0; }
  ReplayEstimator.prototype.detect = function (src, tMs) { // returns the recorded frame for the elapsed time
    var fr = this.take.frames, e = this.k;
    if (tMs == null) { this.k = Math.min(fr.length - 1, this.k + 1); return fr[e]; }
    while (e + 1 < fr.length && fr[e + 1].t - this.t0 <= tMs) e++; this.k = e; return fr[e];
  };
  ReplayEstimator.prototype.done = function () { return this.k >= this.take.frames.length - 1; };
  Mo.ReplayEstimator = ReplayEstimator;

  // MediaPipe Tasks Vision, loaded from the studio's own files (stark_anim/models): never a CDN
  function MediaPipeEstimator(base, opt) { this.base = base.replace(/\/?$/, '/'); this.opt = opt || {}; this.kind = 'mediapipe'; this.pose = null; this.face = null; }
  MediaPipeEstimator.prototype.load = function () {
    var self = this, b = this.base, fileset = {wasmLoaderPath: b + 'tasks-vision/wasm/vision_wasm_internal.js', wasmBinaryPath: b + 'tasks-vision/wasm/vision_wasm_internal.wasm'};
    return import(b + 'tasks-vision/vision_bundle.mjs').then(function (vision) {
      self.vision = vision;
      function mk(cls, model, extra, delegate) {
        var o = Object.assign({baseOptions: {modelAssetPath: b + model, delegate: delegate}, runningMode: 'VIDEO'}, extra);
        return cls.createFromOptions(fileset, o);
      }
      function withFallback(cls, model, extra) {
        var d = self.opt.delegate || 'GPU';
        return mk(cls, model, extra, d).catch(function () { return mk(cls, model, extra, 'CPU'); });
      }
      var jobs = [withFallback(vision.PoseLandmarker, 'pose_landmarker_lite.task', {numPoses: 1, minPoseDetectionConfidence: 0.5, minPosePresenceConfidence: 0.5, minTrackingConfidence: 0.5})
        .then(function (p) { self.pose = p; })];
      if (self.opt.face) jobs.push(withFallback(vision.FaceLandmarker, 'face_landmarker.task', {numFaces: 1, outputFaceBlendshapes: true})
        .then(function (f) { self.face = f; }).catch(function (e) { self.faceError = String(e); }));
      return Promise.all(jobs).then(function () { return self; });
    });
  };
  MediaPipeEstimator.prototype.detect = function (src, tMs) {
    var r = this.pose.detectForVideo(src, tMs), out = {t: tMs, landmarks: null, worldLandmarks: null, face: null};
    if (r && r.landmarks && r.landmarks.length) {
      out.landmarks = r.landmarks[0].map(function (p) { return {x: p.x, y: p.y, z: p.z, visibility: p.visibility == null ? 1 : p.visibility}; });
      if (r.worldLandmarks && r.worldLandmarks.length) out.worldLandmarks = r.worldLandmarks[0].map(function (p) { return {x: p.x, y: p.y, z: p.z}; });
    }
    if (this.face) {
      var f = this.face.detectForVideo(src, tMs);
      if (f && f.faceBlendshapes && f.faceBlendshapes.length) { var bs = {}; f.faceBlendshapes[0].categories.forEach(function (c) { bs[c.categoryName] = +c.score.toFixed(4); }); out.face = {blendshapes: bs}; }
    }
    return out;
  };
  Mo.MediaPipeEstimator = MediaPipeEstimator;

  // ---------------------------------------------------------------- session: estimator -> smoothing -> solver -> pose -> recorder
  function Session(opt) {
    opt = opt || {};
    this.sk = opt.skeleton || GA.Skeleton.fromRig(opt.rig);
    this.solver = new Solver(this.sk); this.pose = new GA.Pose(this.sk); this.filter = new PointFilter(33, opt.smoothing);
    this.modelBase = opt.modelBase || '/anim/models/'; this.mirrored = !!opt.mirrored; this.ground = {};
    this.lifter = null; this.estimator = null; this.recording = null; this.lastT = null; this.onPose = null; this.onFrame = null; this.stats = {frames: 0, detected: 0};
    this.image = {width: 640, height: 480, mirrored: this.mirrored};
  }
  Session.prototype.useMediaPipe = function (o) { var e = new MediaPipeEstimator(this.modelBase, o); this.estimator = e; return e.load(); };
  Session.prototype.useReplay = function (take) { this.estimator = new ReplayEstimator(take); if (take.image) this.image = take.image; return Promise.resolve(this.estimator); };
  Session.prototype.startCamera = function (video, o) {
    var self = this; o = o || {};
    return navigator.mediaDevices.getUserMedia({audio: false, video: {width: o.width || 640, height: o.height || 480, facingMode: 'user', frameRate: o.fps || 30}}).then(function (st) {
      self.stream = st; video.srcObject = st; video.muted = true; video.playsInline = true; return video.play();
    }).then(function () { self.video = video; self.image = {width: video.videoWidth, height: video.videoHeight, mirrored: false}; self._loop(); return video; });
  };
  Session.prototype.stopCamera = function () { this.running = false; if (this.stream) this.stream.getTracks().forEach(function (t) { t.stop(); }); this.stream = null; };
  Session.prototype._loop = function () {
    var self = this; this.running = true;
    var step = function () { if (!self.running) return; if (self.video.readyState >= 2) self.process(self.estimator.detect(self.video, performance.now())); requestAnimationFrame(step); };
    requestAnimationFrame(step);
  };
  // one estimator result -> smoothed engine-space points -> solved pose (+ recorded numbers)
  Session.prototype.process = function (fr) {
    this.stats.frames++;
    if (!fr || !fr.landmarks) { if (this.onFrame) this.onFrame(null, fr); return null; }
    this.stats.detected++;
    var t = fr.t != null ? fr.t : (this.lastT == null ? 0 : this.lastT + 1000 / 30), dt = this.lastT == null ? 1 / 30 : Math.max(1e-3, (t - this.lastT) / 1000); this.lastT = t;
    var P;
    if (fr.worldLandmarks) P = Mo.worldToStark(fr.worldLandmarks);
    else { this.lifter = this.lifter || new Lifter(this.sk, this.image.width / this.image.height); P = this.lifter.lift(fr.landmarks); }
    if (this.image.mirrored) P = Mo.mirrorPoints(P);
    if (this._calib && fr.worldLandmarks) { this._calib.push(P); this._calibFrames.push({t: +t.toFixed(2), worldLandmarks: fr.worldLandmarks.map(rnd5), landmarks: fr.landmarks.map(rnd5)}); }
    var vis = fr.landmarks.map(function (l) { return l.visibility == null ? 1 : l.visibility; });
    P = this.filter.apply(P, vis, dt);
    this.pose.rest(); this.solver.solve(P, this.pose); groundPose(this.sk, this.pose, this.ground);
    if (fr.face) this.mouth = Mo.faceToMouth(fr.face.blendshapes);
    if (this.recording) this.recording.frames.push({t: +t.toFixed(2), landmarks: fr.landmarks.map(rnd5), worldLandmarks: fr.worldLandmarks ? fr.worldLandmarks.map(rnd5) : null, face: fr.face || null});
    if (this.onPose) this.onPose(this.pose, fr, P);
    return this.pose;
  };
  function rnd5(p) { var o = {x: +p.x.toFixed(5), y: +p.y.toFixed(5), z: +(p.z || 0).toFixed(5)}; if (p.visibility != null) o.visibility = +p.visibility.toFixed(3); return o; }
  // collect `seconds` of T-pose frames (live), calibrate the solver, keep them for the next take
  Session.prototype.calibrate = function (seconds) {
    var self = this; this._calib = []; this._calibFrames = [];
    return new Promise(function (ok) {
      setTimeout(function () {
        var pts = self._calib; self._calib = null;
        if (pts.length) self.solver.calibrate(pts);
        ok({frames: pts.length, calibrated: self.solver.calibrated});
      }, (seconds || 1.5) * 1000);
    });
  };
  Session.prototype.record = function (name) {
    this.recording = {format: 'stark-mocap-take', version: 1, name: name || ('take_' + new Date().toISOString().replace(/[:.]/g, '-')), fps: 30,
      source: this.estimator ? this.estimator.kind : 'webcam', image: this.image, started: new Date().toISOString(), frames: [],
      calibration: this._calibFrames || []};
    return this.recording;
  };
  Session.prototype.stop = function () {
    var r = this.recording; this.recording = null; if (!r) return null;
    if (r.frames.length > 1) r.fps = +((r.frames.length - 1) / ((r.frames[r.frames.length - 1].t - r.frames[0].t) / 1000)).toFixed(3);
    return r; // landmark numbers only - no image or video data is ever part of a take
  };
  // solve a whole take in the browser (quick look); the studio's Python path adds zero-phase smoothing + cleanup
  Mo.solveTake = function (sk, take) {
    var s = new Session({skeleton: sk}); s.image = take.image || s.image; s.estimator = new ReplayEstimator(take);
    var rots = [], hips = [], pi = sk.canon.pelvis;
    take.frames.forEach(function (fr) { var p = s.process(fr); if (!p) return; rots.push(Array.prototype.slice.call(p.r)); hips.push([p.t[pi * 3], p.t[pi * 3 + 1], p.t[pi * 3 + 2]]); });
    return {rotations: rots, hips: hips, joints: sk.names.slice(), fps: take.fps};
  };
  Mo.Session = Session;
  Mo.solveFrame = function (sk, P, solver) { solver = solver || new Solver(sk); var pose = new GA.Pose(sk); solver.solve(P, pose); return pose; };
  return Mo;
});
