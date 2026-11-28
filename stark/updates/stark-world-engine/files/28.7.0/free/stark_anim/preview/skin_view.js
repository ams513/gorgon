/* skin_view.js - small WebGL2 viewer for skinned glTF characters driven by StarkAnim (studio previews, mocap
 * panel, headless GIF/MP4 renders). Not the cartridge renderer: engine3d draws games; this draws one character.
 *   var v = await StarkSkinView.create(canvas, {model: 'path/Character.gltf'});
 *   v.anim.addClip(gclipJson); v.anim.play('Walk_Loop', {loop:true}); v.render(dt);   // or v.pose(t)
 */
(function (root) {
  'use strict';
  var GA = root.StarkAnim;
  var N = {SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16}, CT = {5120: Int8Array, 5121: Uint8Array, 5122: Int16Array, 5123: Uint16Array, 5125: Uint32Array, 5126: Float32Array};

  function fetchBuf(url) { return fetch(url).then(function (r) { if (!r.ok) throw new Error(url + ': ' + r.status); return r.arrayBuffer(); }); }
  function resolve(base, uri) { return base.replace(/[^/]*$/, '') + uri.split('/').map(encodeURIComponent).join('/'); }

  function loadGLTF(url) {
    return fetchBuf(url).then(function (buf) {
      var dv = new DataView(buf), json, bin = null;
      if (dv.getUint32(0, true) === 0x46546C67) {
        var off = 12;
        while (off < buf.byteLength) { var n = dv.getUint32(off, true), t = dv.getUint32(off + 4, true);
          if (t === 0x4E4F534A) json = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, off + 8, n))); else if (t === 0x004E4942) bin = buf.slice(off + 8, off + 8 + n); off += 8 + n; }
      } else json = JSON.parse(new TextDecoder().decode(buf));
      return Promise.all((json.buffers || []).map(function (b) { return b.uri == null ? bin : /^data:/.test(b.uri) ? fetchBuf(b.uri) : fetchBuf(resolve(url, b.uri)); }))
        .then(function (bufs) { return {json: json, bufs: bufs, url: url}; });
    });
  }
  function accessor(g, i) {
    var j = g.json, a = j.accessors[i], n = N[a.type], C = CT[a.componentType], out = new C(a.count * n);
    if (a.bufferView == null) return out;
    var bv = j.bufferViews[a.bufferView], b = g.bufs[bv.buffer], o = (bv.byteOffset || 0) + (a.byteOffset || 0), es = C.BYTES_PER_ELEMENT, st = bv.byteStride || es * n;
    if (st === es * n && o % es === 0) return new C(b, o, a.count * n).slice();
    var d = new DataView(b), get = {1: 'getUint8', 2: 'getUint16', 4: 'getFloat32'}[es];
    if (C === Int8Array) get = 'getInt8'; if (C === Int16Array) get = 'getInt16'; if (C === Uint32Array) get = 'getUint32';
    for (var k = 0; k < a.count; k++) for (var c = 0; c < n; c++) out[k * n + c] = d[get](o + k * st + c * es, true);
    return out;
  }
  function toFloat(arr, norm) { if (arr instanceof Float32Array) return arr; var f = new Float32Array(arr.length), d = arr instanceof Uint8Array ? 255 : arr instanceof Uint16Array ? 65535 : arr instanceof Int16Array ? 32767 : 127; for (var i = 0; i < arr.length; i++) f[i] = norm ? arr[i] / d : arr[i]; return f; }

  var VS = '#version 300 es\nprecision highp float;precision highp int;\n' +
    'layout(location=0) in vec3 aPos;layout(location=1) in vec3 aNor;layout(location=2) in vec2 aUV;layout(location=3) in vec4 aJ;layout(location=4) in vec4 aW;\n' +
    'uniform mat4 uVP;uniform mat4 uModel;uniform highp sampler2D uJT;uniform int uSkin;out vec3 vN;out vec2 vUV;out vec3 vW;\n' +
    'mat4 jm(int j){return mat4(texelFetch(uJT,ivec2(j*4,0),0),texelFetch(uJT,ivec2(j*4+1,0),0),texelFetch(uJT,ivec2(j*4+2,0),0),texelFetch(uJT,ivec2(j*4+3,0),0));}\n' +
    'void main(){vec4 p=vec4(aPos,1.0);vec3 n=aNor;if(uSkin==1){mat4 s=aW.x*jm(int(aJ.x))+aW.y*jm(int(aJ.y))+aW.z*jm(int(aJ.z))+aW.w*jm(int(aJ.w));p=s*p;n=mat3(s)*n;}\n' +
    'vec4 w=uModel*p;vW=w.xyz;vN=normalize(mat3(uModel)*n);vUV=aUV;gl_Position=uVP*w;}';
  var FS = '#version 300 es\nprecision highp float;in vec3 vN;in vec2 vUV;in vec3 vW;uniform sampler2D uTex;uniform vec4 uCol;uniform int uHasTex;uniform vec3 uEye;out vec4 o;\n' +
    'void main(){vec4 b=uCol;if(uHasTex==1)b*=texture(uTex,vUV);if(b.a<0.4)discard;vec3 N=normalize(vN);vec3 L=normalize(vec3(0.45,0.85,0.35));vec3 V=normalize(uEye-vW);\n' +
    'float d=max(dot(N,L),0.0);float hemi=0.5+0.5*N.y;float rim=pow(1.0-max(dot(N,V),0.0),3.0);\n' +
    'vec3 c=b.rgb*(vec3(0.32,0.34,0.42)*hemi+vec3(1.05,0.98,0.9)*d)+rim*vec3(0.25,0.3,0.4);o=vec4(pow(c,vec3(1.0/2.2)),1.0);}';
  var VSG = '#version 300 es\nlayout(location=0) in vec3 aPos;uniform mat4 uVP;out vec3 vW;void main(){vW=aPos;gl_Position=uVP*vec4(aPos,1.0);}';
  var FSG = '#version 300 es\nprecision highp float;in vec3 vW;uniform vec3 uC;uniform vec2 uShadow;out vec4 o;void main(){vec2 q=floor(vW.xz*2.0);float ch=mod(q.x+q.y,2.0);\n' +
    'vec3 c=mix(vec3(0.21,0.22,0.25),vec3(0.26,0.27,0.31),ch);float r=length(vW.xz-uShadow);c*=mix(0.45,1.0,smoothstep(0.05,0.55,r));\n' +
    'float f=smoothstep(4.0,16.0,length(vW.xz-uC.xz));c=mix(c,vec3(0.13,0.14,0.18),f);o=vec4(pow(c,vec3(1.0/2.2)),1.0);}';

  function prog(gl, vs, fs) {
    function sh(t, s) { var x = gl.createShader(t); gl.shaderSource(x, s); gl.compileShader(x); if (!gl.getShaderParameter(x, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(x)); return x; }
    var p = gl.createProgram(); gl.attachShader(p, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    var U = {p: p}, n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
    for (var i = 0; i < n; i++) { var u = gl.getActiveUniform(p, i); U[u.name] = gl.getUniformLocation(p, u.name); }
    return U;
  }
  function persp(f, a, n, fa) { var t = 1 / Math.tan(f / 2); return [t / a, 0, 0, 0, 0, t, 0, 0, 0, 0, (fa + n) / (n - fa), -1, 0, 0, 2 * fa * n / (n - fa), 0]; }
  function look(e, c, u) { var z = norm([e[0] - c[0], e[1] - c[1], e[2] - c[2]]), x = norm(cross(u, z)), y = cross(z, x);
    return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot(x, e), -dot(y, e), -dot(z, e), 1]; }
  function norm(v) { var l = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / l, v[1] / l, v[2] / l]; }
  function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
  function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
  function mul(a, b) { var o = new Array(16); for (var c = 0; c < 4; c++) for (var r = 0; r < 4; r++) { var s = 0; for (var k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k]; o[c * 4 + r] = s; } return o; }

  function create(canvas, opt) {
    opt = opt || {};
    var gl = canvas.getContext('webgl2', {antialias: true, preserveDrawingBuffer: true, premultipliedAlpha: false});
    if (!gl) throw new Error('WebGL2 not available');
    if (!gl.getExtension('EXT_color_buffer_float')) { /* only needed for float render targets; the joint texture is sampled, not rendered */ }
    return loadGLTF(opt.model).then(function (g) {
      var j = g.json, prims = [], texCache = {};
      var model = {nodes: [], skins: [], anims: []};
      (j.nodes || []).forEach(function (n, i) { model.nodes.push({name: n.name || 'node' + i, mesh: n.mesh == null ? -1 : n.mesh, skin: n.skin == null ? -1 : n.skin, children: n.children || [], parent: -1,
        t: (n.translation || [0, 0, 0]).slice(), r: (n.rotation || [0, 0, 0, 1]).slice(), s: (n.scale || [1, 1, 1]).slice()}); });
      model.nodes.forEach(function (n, i) { n.children.forEach(function (c) { model.nodes[c].parent = i; }); });
      (j.skins || []).forEach(function (s) { model.skins.push({joints: s.joints.slice(), ibm: s.inverseBindMatrices != null ? accessor(g, s.inverseBindMatrices) : null}); });
      (j.animations || []).forEach(function (a, ai) {
        var ch = [], dur = 0;
        (a.channels || []).forEach(function (c) { var s = a.samplers[c.sampler]; if (!c.target || c.target.node == null) return; var tm = accessor(g, s.input), vl = toFloat(accessor(g, s.output), true);
          dur = Math.max(dur, tm[tm.length - 1] || 0); ch.push({node: c.target.node, name: model.nodes[c.target.node].name, path: c.target.path, t: tm, v: vl, interp: s.interpolation || 'LINEAR'}); });
        model.anims.push({name: a.name || 'anim' + ai, dur: dur, ch: ch});
      });
      function texture(mi) {
        var m = (j.materials || [])[mi]; if (!m) return Promise.resolve(null);
        var pb = m.pbrMetallicRoughness || {}, bt = pb.baseColorTexture; if (!bt) return Promise.resolve({col: pb.baseColorFactor || [1, 1, 1, 1], tex: null});
        var src = j.textures[bt.index].source, im = j.images[src];
        if (texCache[src]) return texCache[src].then(function (t) { return {col: pb.baseColorFactor || [1, 1, 1, 1], tex: t}; });
        var url = im.uri ? resolve(g.url, im.uri) : null;
        texCache[src] = (url ? fetchBuf(url) : Promise.resolve(g.bufs[j.bufferViews[im.bufferView].buffer].slice(j.bufferViews[im.bufferView].byteOffset || 0, (j.bufferViews[im.bufferView].byteOffset || 0) + j.bufferViews[im.bufferView].byteLength)))
          .then(function (b) { return createImageBitmap(new Blob([b]), {premultiplyAlpha: 'none', colorSpaceConversion: 'none'}); })
          .then(function (bmp) { var t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t); gl.texImage2D(gl.TEXTURE_2D, 0, gl.SRGB8_ALPHA8, gl.RGBA, gl.UNSIGNED_BYTE, bmp);
            gl.generateMipmap(gl.TEXTURE_2D); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR); return t; })
          .catch(function () { return null; });
        return texCache[src].then(function (t) { return {col: pb.baseColorFactor || [1, 1, 1, 1], tex: t}; });
      }
      var jobs = [];
      (j.nodes || []).forEach(function (n) {
        if (n.mesh == null) return;
        j.meshes[n.mesh].primitives.forEach(function (p) {
          var at = p.attributes; if (at.POSITION == null) return;
          var vao = gl.createVertexArray(); gl.bindVertexArray(vao);
          function attr(loc, data, size, isInt) { var b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0); }
          var pos = toFloat(accessor(g, at.POSITION)), cnt = pos.length / 3;
          attr(0, pos, 3); attr(1, at.NORMAL != null ? toFloat(accessor(g, at.NORMAL), true) : new Float32Array(cnt * 3), 3);
          attr(2, at.TEXCOORD_0 != null ? toFloat(accessor(g, at.TEXCOORD_0), true) : new Float32Array(cnt * 2), 2);
          attr(3, at.JOINTS_0 != null ? Float32Array.from(accessor(g, at.JOINTS_0)) : new Float32Array(cnt * 4), 4);
          attr(4, at.WEIGHTS_0 != null ? toFloat(accessor(g, at.WEIGHTS_0), true) : new Float32Array(cnt * 4), 4);
          var pr = {vao: vao, skin: n.skin != null && at.JOINTS_0 != null, count: cnt, idx: null, type: 0};
          if (p.indices != null) { var ix = accessor(g, p.indices); if (ix instanceof Uint8Array) ix = Uint16Array.from(ix); var eb = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, eb); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, ix, gl.STATIC_DRAW);
            pr.idx = eb; pr.count = ix.length; pr.type = ix instanceof Uint32Array ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT; }
          gl.bindVertexArray(null); prims.push(pr);
          jobs.push(texture(p.material).then(function (m) { pr.mat = m; }));
        });
      });
      return Promise.all(jobs).then(function () {
        var anim = GA.fromR2Model(model, {id: 'preview'});
        var P = prog(gl, VS, FS), PG = prog(gl, VSG, FSG);
        var jt = gl.createTexture(), nj = model.skins.length ? model.skins[0].joints.length : 1;
        gl.bindTexture(gl.TEXTURE_2D, jt); gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, nj * 4, 1, 0, gl.RGBA, gl.FLOAT, null);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        var gvao = gl.createVertexArray(), gb = gl.createBuffer(); gl.bindVertexArray(gvao); gl.bindBuffer(gl.ARRAY_BUFFER, gb);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-60, 0, -60, 60, 0, -60, 60, 0, 60, -60, 0, -60, 60, 0, 60, -60, 0, 60]), gl.STATIC_DRAW);
        gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0); gl.bindVertexArray(null);
        var V = {gl: gl, canvas: canvas, anim: anim, model: model, prims: prims, world: {pos: [0, 0, 0], yaw: 0, scale: 1}, follow: true,
          cam: {dist: opt.dist || 3.4, yaw: opt.camYaw == null ? 0.55 : opt.camYaw, pitch: opt.camPitch == null ? 0.18 : opt.camPitch, target: [0, 0.95, 0], fov: 0.75}, bg: [0.11, 0.12, 0.16]};
        V.applyRootMotion = function () { var r = anim.rootMotion, W = V.world, c = Math.cos(W.yaw), s = Math.sin(W.yaw); W.pos[0] += c * r.dx + s * r.dz; W.pos[2] += -s * r.dx + c * r.dz; W.yaw += r.dyaw; };
        V.draw = function () {
          var w = canvas.width, h = canvas.height; gl.viewport(0, 0, w, h); gl.clearColor(V.bg[0], V.bg[1], V.bg[2], 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT); gl.enable(gl.DEPTH_TEST);
          var W = V.world, hip = anim.sk.canon.pelvis, hp = [0, 0.95, 0];
          if (anim.globals && hip != null) hp = anim._toWorld([anim.globals.p[hip * 3], anim.globals.p[hip * 3 + 1], anim.globals.p[hip * 3 + 2]]);
          if (V.follow) V.cam.target = [hp[0], Math.max(0.8, hp[1]), hp[2]];
          var C = V.cam, e = [C.target[0] + Math.sin(C.yaw) * Math.cos(C.pitch) * C.dist, C.target[1] + Math.sin(C.pitch) * C.dist, C.target[2] + Math.cos(C.yaw) * Math.cos(C.pitch) * C.dist];
          var VP = mul(persp(C.fov, w / h, 0.05, 200), look(e, C.target, [0, 1, 0]));
          V.vp = VP; V.eye = e;
          gl.useProgram(PG.p); gl.uniformMatrix4fv(PG.uVP, false, VP); gl.uniform3fv(PG.uC, C.target); gl.uniform2f(PG.uShadow, hp[0], hp[2]); gl.bindVertexArray(gvao); gl.drawArrays(gl.TRIANGLES, 0, 6);
          var cy = Math.cos(W.yaw), sy = Math.sin(W.yaw), k = W.scale, M = [cy * k, 0, -sy * k, 0, 0, k, 0, 0, sy * k, 0, cy * k, 0, W.pos[0], W.pos[1], W.pos[2], 1];
          gl.useProgram(P.p); gl.uniformMatrix4fv(P.uVP, false, VP); gl.uniformMatrix4fv(P.uModel, false, M); gl.uniform3fv(P.uEye, e);
          if (anim.jointMatrices) { gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, jt); gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, nj * 4, 1, gl.RGBA, gl.FLOAT, anim.jointMatrices); }
          gl.uniform1i(P.uJT, 1); gl.uniform1i(P.uTex, 0);
          prims.forEach(function (pr) {
            var m = pr.mat || {col: [0.7, 0.7, 0.75, 1], tex: null}; gl.uniform4fv(P.uCol, m.col); gl.uniform1i(P.uHasTex, m.tex ? 1 : 0);
            gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, m.tex); gl.uniform1i(P.uSkin, pr.skin ? 1 : 0);
            gl.bindVertexArray(pr.vao); if (pr.idx) { gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, pr.idx); gl.drawElements(gl.TRIANGLES, pr.count, pr.type, 0); } else gl.drawArrays(gl.TRIANGLES, 0, pr.count);
          });
          gl.bindVertexArray(null);
        };
        V.render = function (dt) { anim.update(dt, V.world); if (V.rootMotion) V.applyRootMotion(); V.draw(); };
        V.project = function (p) { var v = V.vp, x = v[0] * p[0] + v[4] * p[1] + v[8] * p[2] + v[12], y = v[1] * p[0] + v[5] * p[1] + v[9] * p[2] + v[13], w = v[3] * p[0] + v[7] * p[1] + v[11] * p[2] + v[15];
          return [(x / w * 0.5 + 0.5), (1 - (y / w * 0.5 + 0.5))]; };
        V.jointWorld = function (name) { var jn = anim.sk.find(name); if (jn < 0 || !anim.globals) return null; return anim._toWorld([anim.globals.p[jn * 3], anim.globals.p[jn * 3 + 1], anim.globals.p[jn * 3 + 2]]); };
        V.pixels = function () { var w = canvas.width, h = canvas.height, px = new Uint8Array(w * h * 4); gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
          var row = w * 4, flip = new Uint8Array(px.length); for (var y = 0; y < h; y++) flip.set(px.subarray((h - 1 - y) * row, (h - y) * row), y * row); return flip; };
        return V;
      });
    });
  }
  root.StarkSkinView = {create: create, loadGLTF: loadGLTF};
})(typeof self !== 'undefined' ? self : this);
