/* sfx.js - STARK FLOW. One self-contained layer: a slow WebGL2 liquid behind the page, sparse depth particles behind and in front of it.
   Everything is prefixed sfx. Never on checkout/prices/legal pages. No library, no network. Canvas 2D fallback without WebGL2;
   nothing at all under reduced motion, Save-Data, or when the tab is hidden. All layers are pointer-events:none and position:fixed (no layout shift).
   Brightness is capped so body text keeps at least 7:1 against the liquid. */
(function () {
  'use strict';
  var d = document, w = window, nav = navigator, cn = nav.connection;
  var CALM = /(^|\/)(prices|checkout|thank_you|black-friday|why-one-time|terms|refund|privacy|acceptable-use|studios-eula|tenancy|vital|caregiver|ledger|family-vault)\.html$/;
  if (w.sfxOn || CALM.test(location.pathname)) return;
  var mq = w.matchMedia && w.matchMedia('(prefers-reduced-motion: reduce)');
  if ((mq && mq.matches) || (cn && cn.saveData) || (nav.webdriver && location.hash.indexOf('sfx-force') < 0)) return;
  w.sfxOn = 1;
  var HOME = !!d.querySelector('.apx-hero,[data-mist]');
  var K = HOME ? 1 : 0.55;                                   // strength: full on heroes, lighter site-wide
  var mobile = Math.min(w.innerWidth, w.innerHeight) < 700 || (nav.hardwareConcurrency || 8) <= 4;
  var N_BACK = Math.round((HOME ? 46 : 22) * (mobile ? 0.6 : 1)), N_FRONT = Math.round((HOME ? 9 : 5) * (mobile ? 0.6 : 1));
  var PAL = [[150, 190, 255], [190, 206, 228], [120, 160, 220], [170, 200, 240], [255, 228, 196]];   // cool silver-blue; the last is a rare warm highlight
  var cvB, cvP, cvF, gl, prog, uni = {}, ctxB, ctxP, ctxF, W = 0, H = 0, S = 0.5, DPR = 1;
  var tx = 0, ty = 0, px = 0, py = 0, sy = 0, ssy = 0, born = 0, vis = true, run = false, last = 0, t0 = 0, acc = 0, fr = 0, lowCount = 0, dead = false;
  var back = [], front = [], sprites = {};

  function mk(z, id) {
    var c = d.createElement('canvas');
    c.id = id; c.setAttribute('aria-hidden', 'true');
    c.style.cssText = 'position:fixed;left:0;top:0;width:100vw;height:100vh;height:100lvh;pointer-events:none;z-index:' + z + ';opacity:0;transition:opacity 1.6s ease';
    return c;
  }
  // ---- sprites: pre-rendered soft discs, drawn with drawImage (cheap; blur costs nothing at runtime)
  function sprite(c, soft) {
    var k = c.join() + soft;
    if (sprites[k]) return sprites[k];
    var s = d.createElement('canvas'), n = 96, x, g, i, e;
    s.width = s.height = n; x = s.getContext('2d');
    g = x.createRadialGradient(n / 2, n / 2, 0, n / 2, n / 2, n / 2);
    for (i = 0; i <= 10; i++) { e = i / 10; g.addColorStop(e, 'rgba(' + c + ',' + (Math.exp(-e * e * (3 + 18 * soft)) * (1 - e * e * e)).toFixed(4) + ')'); }   // gaussian: soft core, wide faint halo, no edge
    x.fillStyle = g; x.fillRect(0, 0, n, n);
    return (sprites[k] = s);
  }
  function rnd(a, b) { return a + Math.random() * (b - a); }
  function seed() {
    var i, z, c;
    back.length = front.length = 0;
    // far = tiny and dim, mid = small, near = few large soft bokeh; all are carried by the flow field
    for (i = 0; i < N_BACK; i++) { z = i % 4 === 0 ? rnd(0.7, 1) : rnd(0.15, 0.6); c = i % 17 === 5 ? PAL[4] : PAL[i % 4]; back.push({ x: Math.random(), y: Math.random(), z: z, r: 5 + z * 13, c: c, ph: rnd(0, 6.28), sp: rnd(0.6, 1.2), vx: 0, vy: 0 }); }
    for (i = 0; i < N_FRONT; i++) front.push({ x: Math.random(), y: Math.random(), z: rnd(1.4, 2.6), r: rnd(40, 110), c: PAL[i % 3], ph: rnd(0, 6.28), sp: rnd(0.6, 1.1), a: rnd(0.04, 0.07), vx: 0, vy: 0 });
  }
  function flow(p, ts) {
    var X = p.x * 6.0, Y = p.y * 4.0;
    return [Math.sin(Y * 1.3 + ts * 0.11 + p.ph) * 0.6 + Math.sin(Y * 2.9 - ts * 0.07) * 0.25 + 0.15, Math.cos(X * 1.1 + ts * 0.09) * 0.5 + Math.sin(X * 2.3 + Y - ts * 0.05) * 0.2 - 0.18];
  }
  var rects = [], rectT = -9999;
  function readRects() {
    var els = d.querySelectorAll('h1,h2,h3,h4,p,li,a,button,label,input,figcaption,.apx-hero-in>*'), i, r, o = [], hh = w.innerHeight, ww = w.innerWidth;
    for (i = 0; i < els.length && o.length < 640; i++) {
      r = els[i].getBoundingClientRect();
      if (r.bottom < -40 || r.top > hh + 40 || r.width < 8 || r.height < 6 || (r.width > ww * 0.98 && r.height > hh * 0.5)) continue;
      o.push(r.left, r.top, r.right, r.bottom);
    }
    rects = o; rectT = performance.now();
  }
  function textMask(x, y, pad) {
    var i, k = 1, dx, dy, r = rects;
    for (i = 0; i < r.length; i += 4) {
      dx = Math.max(r[i] - x, 0, x - r[i + 2]); dy = Math.max(r[i + 1] - y, 0, y - r[i + 3]);
      dx = Math.sqrt(dx * dx + dy * dy) / pad;
      if (dx < k) { k = dx; if (k <= 0) return 0; }
    }
    return k;
  }
  // ---- WebGL2 liquid: domain-warped flow noise, two stacked depths, brightness-capped
  var VS = '#version 300 es\nin vec2 a;void main(){gl_Position=vec4(a,0.,1.);}';
  var FS = '#version 300 es\nprecision mediump float;uniform vec2 R;uniform float T,K,Sc;uniform vec2 P;out vec4 o;\n' +
    'float h(vec2 p){p=fract(p*vec2(123.34,456.21));p+=dot(p,p+45.32);return fract(p.x*p.y);}\n' +
    'float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x),f.y);}\n' +
    'float fb(vec2 p){float v=0.,a=.5;for(int i=0;i<3;i++){v+=a*n(p);p=p*1.97+vec2(1.7,9.2);a*=.5;}return v*1.14;}\n' +
    'void main(){vec2 u=gl_FragCoord.xy/R;float as=R.x/R.y;vec2 p=vec2(u.x*as,u.y)*1.15;p.y+=Sc*.35;p+=P*vec2(.09,.05);\n' +
    'float t=T*.045;vec2 q=vec2(fb(p+vec2(0,t)),fb(p+vec2(5.2,1.3)-t));\n' +
    'vec2 r=vec2(fb(p+3.*q+vec2(1.7,9.2)+t*.7),fb(p+3.*q+vec2(8.3,2.8)-t*.5));\n' +
    'float f=fb(p+3.*r);\n' +
    // second, slower, larger layer for depth, shifted by the pointer (parallax)
    'float g=fb(p*.55+q*1.5+vec2(t*.4,-t*.3));\n' +
    'float ridge=smoothstep(.55,.95,1.-abs(f*2.-1.));\n' +
    'vec3 deep=vec3(.008,.018,.042),mid=vec3(.055,.100,.175),sil=vec3(.125,.160,.215);\n' +
    'vec3 c=mix(deep,mid,smoothstep(.25,.8,f));c=mix(c,sil,ridge*.55*smoothstep(.3,.9,g));\n' +
    'c+=vec3(.03,.05,.08)*smoothstep(.45,.9,g)*(1.-u.y*.6);\n' +
    'vec2 m=vec2((u.x-P.x*.5-.5)*as,u.y-(.5-P.y*.5));c+=vec3(.025,.04,.065)*exp(-dot(m,m)*5.);\n' +   // pointer swell
    'float vg=smoothstep(1.25,.25,length((u-.5)*vec2(as*.7,1.)));c*=.55+.45*vg;\n' +
    'float b1=smoothstep(.0,.5,sin((p.y*1.7+f*2.4+t*1.3)*3.1416)*.5+.5)*smoothstep(.35,.8,g);\n' +
    'float b2=smoothstep(.2,.6,sin((p.x*1.2-p.y*.9+r.x*2.2-t*.9)*3.1416)*.5+.5)*smoothstep(.3,.75,f);\n' +
    'c+=vec3(.03,.05,.08)*b1+vec3(.04,.055,.075)*b2;\n' +
    'c=min(c*K,vec3(.125,.170,.255));\n' +                                                               // hard brightness cap (text contrast)
    'o=vec4(c,1.);}';
  function sh(t, s) { var o = gl.createShader(t); gl.shaderSource(o, s); gl.compileShader(o); return gl.getShaderParameter(o, gl.COMPILE_STATUS) ? o : null; }
  function initGL() {
    try { gl = cvB.getContext('webgl2', { alpha: false, antialias: false, depth: false, stencil: false, powerPreference: 'low-power' }); } catch (e) { gl = null; }
    if (!gl) return false;
    var v = sh(gl.VERTEX_SHADER, VS), f = sh(gl.FRAGMENT_SHADER, FS);
    if (!v || !f) return false;
    prog = gl.createProgram(); gl.attachShader(prog, v); gl.attachShader(prog, f); gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return false;
    gl.useProgram(prog);
    var b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    var l = gl.getAttribLocation(prog, 'a'); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, 2, gl.FLOAT, false, 0, 0);
    ['R', 'T', 'K', 'Sc', 'P'].forEach(function (k) { uni[k] = gl.getUniformLocation(prog, k); });
    d.addEventListener('webglcontextlost', function (e) { e.preventDefault(); }, true);
    cvB.addEventListener('webglcontextlost', function (e) { e.preventDefault(); dead = true; stop(); cvB.style.opacity = 0; });
    return true;
  }
  // ---- 2D fallback: a few big drifting radial blobs at low resolution
  function drawFallback(t) {
    var x = ctxB, i, b, cx, cy, r, g;
    x.globalCompositeOperation = 'source-over';
    x.fillStyle = '#02050b'; x.fillRect(0, 0, cvB.width, cvB.height);
    x.globalCompositeOperation = 'lighter';
    for (i = 0; i < 4; i++) {
      cx = cvB.width * (0.5 + 0.42 * Math.sin(t * 0.00005 * (i + 1) + i * 1.9 + px * 0.2));
      cy = cvB.height * (0.5 + 0.38 * Math.cos(t * 0.00004 * (i + 2) + i * 2.7 + py * 0.2));
      r = Math.max(cvB.width, cvB.height) * (0.45 + 0.1 * i);
      g = x.createRadialGradient(cx, cy, 0, cx, cy, r);
      g.addColorStop(0, 'rgba(34,56,92,' + (0.5 * K) + ')'); g.addColorStop(1, 'rgba(34,56,92,0)');
      x.fillStyle = g; x.fillRect(0, 0, cvB.width, cvB.height);
    }
  }
  function drawParticles(ctx, arr, t, front, dtS) {
    var i, p, x, y, a, r, sp, f, k = w.innerWidth, hh = w.innerHeight, ts = t * 0.001, m, band, ang, st;
    ctx.clearRect(0, 0, k, hh);
    ctx.globalCompositeOperation = 'lighter';
    if (front && t - rectT > 500) readRects();
    for (i = 0; i < arr.length; i++) {
      p = arr[i]; f = flow(p, ts);
      var kf = 1 - Math.exp(-dtS / 40); p.vx += (f[0] - p.vx) * kf; p.vy += (f[1] - p.vy) * kf;                       // inertia: curved, trailing paths
      p.x += p.vx * 0.005 * p.z * p.sp * dtS; p.y += p.vy * 0.005 * p.z * p.sp * dtS;
      if (p.x > 1.08) p.x = -0.08; else if (p.x < -0.08) p.x = 1.08;
      if (p.y > 1.08) p.y = -0.08; else if (p.y < -0.08) p.y = 1.08;
      x = (p.x + px * 0.014 * p.z) * k; y = (p.y + py * 0.008 * p.z - ssy * 0.04 * p.z) * hh;
      r = p.r;
      band = 0.5 + 0.5 * Math.sin((p.y * 1.7 + p.x * 0.6) * 5.4 + ts * 0.2);                // crossing a liquid band: swells and brightens
      if (front) {
        a = p.a * K * (0.7 + 0.3 * Math.sin(ts * 0.5 + p.ph)) * (0.8 + 0.4 * band);
        m = textMask(x, y, r * 0.9 + 60); a *= m * m * m;
        ctx.globalAlpha = Math.min(a, 0.07);
      } else {
        a = (0.10 + 0.30 * p.z) * K * (0.55 + 0.45 * Math.sin(ts * 0.9 * p.sp + p.ph)) * (0.7 + 0.6 * band);
        r *= 0.9 + 0.35 * band;
        ctx.globalAlpha = Math.min(a, 0.40);
      }
      if (ctx.globalAlpha < 0.004) continue;
      sp = Math.sqrt(p.vx * p.vx + p.vy * p.vy); ang = Math.atan2(p.vy, p.vx); p.st = (p.st || 1) + ((1 + Math.min(sp * 0.8, 0.9)) - (p.st || 1)) * 0.05; st = p.st;   // motion-stretch along the path
      ctx.save(); ctx.translate(x, y); ctx.rotate(ang); ctx.scale(st, 1 / Math.sqrt(st));
      ctx.drawImage(sprite(p.c, front ? 0.2 : 0.5), -r, -r, r * 2, r * 2);
      ctx.restore();
    }
    ctx.globalAlpha = 1; ctx.globalCompositeOperation = 'source-over';
  }
  function size() {
    DPR = Math.min(w.devicePixelRatio || 1, 1.5);
    W = w.innerWidth; H = w.innerHeight;
    var gs = S * DPR;
    cvB.width = Math.max(2, Math.round(W * gs)); cvB.height = Math.max(2, Math.round(H * gs));
    [cvP, cvF].forEach(function (c) { c.width = Math.round(W * DPR * 0.75); c.height = Math.round(H * DPR * 0.75); });
    ctxP.setTransform(DPR * 0.75, 0, 0, DPR * 0.75, 0, 0); ctxF.setTransform(DPR * 0.75, 0, 0, DPR * 0.75, 0, 0);
    if (gl) gl.viewport(0, 0, cvB.width, cvB.height);
  }
  function frame(now) {
    if (!run) return;
    requestAnimationFrame(frame);
    var dt = now - last;
    if (mobile && dt < 30) return;                          // 30 fps on phones
    last = now;
    var ke = 1 - Math.exp(-Math.min(dt, 100) / 700); px += (tx - px) * ke; py += (ty - py) * ke; ssy += (sy / Math.max(H, 1) - ssy) * (1 - Math.exp(-Math.min(dt, 100) / 350));
    if (gl) {
      gl.uniform2f(uni.R, cvB.width, cvB.height); gl.uniform1f(uni.T, (now - t0) * 0.001 + 20.0); gl.uniform1f(uni.K, K);
      gl.uniform1f(uni.Sc, ssy); gl.uniform2f(uni.P, px, py);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    } else drawFallback(now);
    var dS = Math.min(dt, 60) * 0.06; drawParticles(ctxP, back, now, false, dS); drawParticles(ctxF, front, now, true, dS);
    // adaptive quality: if the average frame is slow for ~1 s, shrink the liquid; if it stays slow, stop
    acc += dt; fr++;
    if (fr === 45) {
      var avg = acc / fr; acc = 0; fr = 0;
      if (avg > (mobile ? 40 : 21)) { lowCount++; if (S > 0.28 && now - born < 2500) { S *= 0.75; size(); } else if (lowCount > 8) { stop(); fade(0); } }
    }
  }
  function start() { if (run || dead || !vis) return; run = true; last = performance.now(); requestAnimationFrame(frame); }
  function stop() { run = false; }
  function fade(o) { cvB.style.opacity = cvP.style.opacity = cvF.style.opacity = o; }
  function init() {
    if (dead) return;
    cvB = mk(-2, 'sfx-liquid'); cvB.style.filter = 'blur(6px)'; cvB.style.transform = 'scale(1.03)'; cvP = mk(-1, 'sfx-back'); cvF = mk(60, 'sfx-front');
    if (mobile) S = 0.38;
    ctxP = cvP.getContext('2d'); ctxF = cvF.getContext('2d');
    if (!initGL()) { gl = null; ctxB = cvB.getContext('2d'); S = 0.25; if (!ctxB) return; }
    // the page background must be transparent so the fixed liquid shows through; <html> stays true black
    var st = d.createElement('style');
    st.textContent = 'html{background:#000}html.sfx-on body{background:transparent}html.sfx-on .apx-hero,html.sfx-on [data-mist],html.sfx-on main>section,html.sfx-on main,html.sfx-on .hero,html.sfx-on .highlights,html.sfx-on .about{background-color:transparent!important}#sfx-liquid,#sfx-back,#sfx-front{contain:strict}' +
      '@media print{#sfx-liquid,#sfx-back,#sfx-front{display:none}}';
    d.head.appendChild(st);
    d.documentElement.classList.add('sfx-on');
    d.body.insertBefore(cvF, d.body.firstChild); d.body.insertBefore(cvP, d.body.firstChild); d.body.insertBefore(cvB, d.body.firstChild);
    seed(); size(); t0 = born = performance.now();
    w.addEventListener('resize', function () { size(); }, { passive: true });
    w.addEventListener('scroll', function () { sy = w.pageYOffset || 0; }, { passive: true });
    w.addEventListener('pointermove', function (e) { tx = e.clientX / W - 0.5; ty = e.clientY / H - 0.5; }, { passive: true });
    d.addEventListener('visibilitychange', function () { vis = !d.hidden; vis ? start() : stop(); });
    if (mq && mq.addEventListener) mq.addEventListener('change', function (q) { if (q.matches) { stop(); dead = true; fade(0); } });
    start();
    requestAnimationFrame(function () { requestAnimationFrame(function () { fade(1); }); });
  }
  function boot() { (w.requestIdleCallback || function (f) { setTimeout(f, 200); })(init, { timeout: 1200 }); }
  if (d.readyState === 'complete') boot(); else w.addEventListener('load', boot);
})();
