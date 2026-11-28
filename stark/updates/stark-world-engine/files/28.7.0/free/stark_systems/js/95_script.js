/* Stark World Engine Systems: StarkScript - a sandboxed JavaScript subset, interpreted (no eval / new Function, so it runs under
   strict Content-Security-Policy too). Scripts see ONLY the game API below: no window, document, network, storage
   or timers of their own. Each call runs under a step budget (no endless loops), a call-depth limit and size limits;
   prototype tricks (__proto__, constructor, prototype) are refused; host functions are opaque.
   Supported: var/let/const, functions + closures + arrows + default/rest params, if/else, for, for-of, for-in, while,
   do-while, switch, break/continue, return, try/catch/finally, throw, objects, arrays, spread, template literals,
   ?. and ??, all operators except instanceof/new/regex literals/classes/generators/async.
   Events: on(name, fn) or top-level onStart / onUpdate / onInteract / onQuestStep / onQuestDone / onDamage /
   onDeath / onEnterZone / onExitZone / onDialogueChoice / onCollect / onHour / onDawn / onDusk / onLevelUp / onCraft.
   CHILD SAFETY applies to the API: damage / kill / setTarget / capture / setHostile / romance / undress on a child
   or teen throw a SafetyError with a clear message, ages are read-only, and every displayed string is checked.
   systems.scripts = [{id, entity?, source}]   systems.graphs = [{id, entity?, nodes, links}] (schema/node_catalog.json) */
(function (G) {
  'use strict';
  var U = G.util, S = G.safety;
  class ScriptError extends Error { constructor(m, info) { super(m); this.name = 'ScriptError'; this.scriptError = true; this.info = info || null; } }
  G.ScriptError = ScriptError;
  function set(a) { var o = Object.create(null); a.forEach(function (k) { o[k] = 1; }); return o; }
  var KW = set('var let const function return if else for while do break continue new delete typeof instanceof in of true false null undefined this throw try catch finally switch case default void class'.split(' '));
  var OPS = ['>>>=', '...', '===', '!==', '**=', '<<=', '>>=', '>>>', '&&=', '||=', '??=', '=>', '==', '!=', '<=', '>=', '&&', '||', '??', '?.', '++', '--', '+=', '-=', '*=', '/=', '%=', '&=', '|=', '^=', '**', '<<', '>>', '{', '}', '(', ')', '[', ']', ';', ',', '<', '>', '+', '-', '*', '/', '%', '&', '|', '^', '!', '~', '?', ':', '=', '.'];
  var BLOCKED = set(['__proto__', 'prototype', 'constructor', '__defineGetter__', '__defineSetter__', '__lookupGetter__', '__lookupSetter__', 'caller', 'callee', 'arguments', 'valueOf', 'toJSON']);
  var MAX_LEN = 1000000;

  // ---------------------------------------------------------------- lexer
  function lex(src, name) {
    var toks = [], i = 0, n = src.length, line = 1, nl = false;
    function err(m) { return new ScriptError((name || 'script') + ' line ' + line + ': ' + m); }
    function push(t, v, x) { var tk = { t: t, v: v, line: line, nl: nl }; if (x) for (var k in x) tk[k] = x[k]; toks.push(tk); nl = false; }
    function esc() {
      var c = src[i + 1]; i += 2;
      if (c === 'n') return '\n'; if (c === 't') return '\t'; if (c === 'r') return '\r'; if (c === 'b') return '\b'; if (c === 'f') return '\f'; if (c === 'v') return '\v'; if (c === '0') return '\0';
      if (c === 'x') { var h = src.substr(i, 2); i += 2; return String.fromCharCode(parseInt(h, 16)); }
      if (c === 'u') { if (src[i] === '{') { var e = src.indexOf('}', i); var cp = parseInt(src.slice(i + 1, e), 16); i = e + 1; return String.fromCodePoint(cp); } var h4 = src.substr(i, 4); i += 4; return String.fromCharCode(parseInt(h4, 16)); }
      if (c === '\n') { line++; return ''; }
      return c;
    }
    while (i < n) {
      var c = src[i];
      if (c === '\n') { line++; nl = true; i++; continue; }
      if (c === ' ' || c === '\t' || c === '\r' || c === '﻿' || c === ' ') { i++; continue; }
      if (c === '/' && src[i + 1] === '/') { while (i < n && src[i] !== '\n') i++; continue; }
      if (c === '/' && src[i + 1] === '*') { var e = src.indexOf('*/', i + 2); if (e < 0) throw err('unterminated comment'); for (var j = i; j < e; j++) if (src[j] === '\n') { line++; nl = true; } i = e + 2; continue; }
      if ((c >= '0' && c <= '9') || (c === '.' && src[i + 1] >= '0' && src[i + 1] <= '9')) { var m = /^(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)/.exec(src.slice(i, i + 64)); push('num', Number(m[0])); i += m[0].length; continue; }
      if (/[A-Za-z_$]/.test(c)) { var w = /^[A-Za-z_$][\w$]*/.exec(src.slice(i, i + 256))[0]; push(KW[w] ? 'kw' : 'name', w); i += w.length; continue; }
      if (c === '"' || c === "'") { var s = ''; i++; while (i < n && src[i] !== c) { if (src[i] === '\n') throw err('unterminated string'); if (src[i] === '\\') s += esc(); else s += src[i++]; } if (i >= n) throw err('unterminated string'); i++; push('str', s); continue; }
      if (c === '`') {
        var strs = [], exprs = [], cur = ''; i++;
        while (i < n && src[i] !== '`') {
          if (src[i] === '\\') { cur += esc(); continue; }
          if (src[i] === '$' && src[i + 1] === '{') {
            var depth = 1, j2 = i + 2, q = null;
            while (j2 < n && depth) { var ch = src[j2]; if (q) { if (ch === '\\') j2++; else if (ch === q) q = null; } else if (ch === '"' || ch === "'" || ch === '`') q = ch; else if (ch === '{') depth++; else if (ch === '}') depth--; if (ch === '\n') line++; j2++; }
            strs.push(cur); cur = ''; exprs.push(src.slice(i + 2, j2 - 1)); i = j2; continue;
          }
          if (src[i] === '\n') line++;
          cur += src[i++];
        }
        if (i >= n) throw err('unterminated template'); i++;
        strs.push(cur); push('tpl', null, { strs: strs, exprs: exprs }); continue;
      }
      var op = null;
      for (var k = 0; k < OPS.length; k++) if (src.substr(i, OPS[k].length) === OPS[k]) { op = OPS[k]; break; }
      if (!op) throw err('unexpected character "' + c + '"');
      if (op === '?.' && src[i + 2] >= '0' && src[i + 2] <= '9') op = '?';
      push('op', op); i += op.length;
    }
    push('eof', null);
    return toks;
  }

  // ---------------------------------------------------------------- parser
  function parse(src, name) {
    var T = lex(src, name), p = 0;
    function err(m, tk) { tk = tk || T[p]; return new ScriptError((name || 'script') + ' line ' + tk.line + ': ' + m); }
    function peek(o) { return T[p + (o || 0)]; }
    function is(t, v) { var tk = T[p]; return tk.t === t && (v === undefined || tk.v === v); }
    function isOp(v) { return is('op', v); }
    function next() { return T[p++]; }
    function eat(t, v) { if (is(t, v)) return next(); return null; }
    function expect(t, v) { if (is(t, v)) return next(); throw err('expected ' + (v || t) + ' but found ' + (T[p].v != null ? '"' + T[p].v + '"' : T[p].t)); }
    function semi() { if (eat('op', ';')) return; if (isOp('}') || is('eof') || T[p].nl) return; throw err('expected ;'); }
    function program() { var body = []; while (!is('eof')) body.push(statement()); return { type: 'Program', body: body }; }
    function block() { expect('op', '{'); var body = []; while (!isOp('}')) { if (is('eof')) throw err('missing }'); body.push(statement()); } next(); return { type: 'Block', body: body }; }
    function varDecl(kind) {
      var decls = [];
      do { var nm = expect('name').v; var init = eat('op', '=') ? assign() : null; decls.push({ name: nm, init: init }); } while (eat('op', ','));
      return { type: 'Var', kind: kind, decls: decls };
    }
    function statement() {
      var tk = peek();
      if (tk.t === 'op' && tk.v === '{') return block();
      if (tk.t === 'op' && tk.v === ';') { next(); return { type: 'Empty' }; }
      if (tk.t === 'kw') {
        switch (tk.v) {
          case 'var': case 'let': case 'const': { next(); var d = varDecl(tk.v); semi(); return d; }
          case 'function': { next(); var fn = func(true); return { type: 'FnDecl', name: fn.name, fn: fn }; }
          case 'return': { next(); var a = (isOp(';') || isOp('}') || is('eof') || T[p].nl) ? null : expr(); semi(); return { type: 'Return', a: a }; }
          case 'if': { next(); expect('op', '('); var t = expr(); expect('op', ')'); var c = statement(), alt = null; if (eat('kw', 'else')) alt = statement(); return { type: 'If', t: t, c: c, alt: alt }; }
          case 'while': { next(); expect('op', '('); var wt = expr(); expect('op', ')'); return { type: 'While', t: wt, body: statement() }; }
          case 'do': { next(); var db = statement(); expect('kw', 'while'); expect('op', '('); var dt = expr(); expect('op', ')'); eat('op', ';'); return { type: 'DoWhile', t: dt, body: db }; }
          case 'for': return forStmt();
          case 'break': { next(); semi(); return { type: 'Break' }; }
          case 'continue': { next(); semi(); return { type: 'Continue' }; }
          case 'throw': { next(); var ta = expr(); semi(); return { type: 'Throw', a: ta }; }
          case 'try': {
            next(); var b = block(), param = null, handler = null, fin = null;
            if (eat('kw', 'catch')) { if (eat('op', '(')) { param = expect('name').v; expect('op', ')'); } handler = block(); }
            if (eat('kw', 'finally')) fin = block();
            if (!handler && !fin) throw err('try needs catch or finally');
            return { type: 'Try', b: b, param: param, handler: handler, fin: fin };
          }
          case 'switch': {
            next(); expect('op', '('); var disc = expr(); expect('op', ')'); expect('op', '{');
            var cases = [];
            while (!isOp('}')) { var test = null; if (eat('kw', 'case')) test = expr(); else expect('kw', 'default'); expect('op', ':'); var body = []; while (!is('kw', 'case') && !is('kw', 'default') && !isOp('}')) body.push(statement()); cases.push({ test: test, body: body }); }
            next(); return { type: 'Switch', d: disc, cases: cases };
          }
          case 'class': throw err('classes are not available in scripts; use plain objects');
          default: break;
        }
      }
      var e = expr(); semi(); return { type: 'Expr', a: e };
    }
    function forStmt() {
      next(); expect('op', '(');
      var init = null;
      if (is('kw', 'var') || is('kw', 'let') || is('kw', 'const')) {
        var kind = next().v;
        if (peek().t === 'name' && (peek(1).t === 'kw' && (peek(1).v === 'of' || peek(1).v === 'in'))) {
          var nm = next().v, isIn = next().v === 'in', it = expr(); expect('op', ')');
          return { type: 'ForOf', kind: kind, name: nm, isIn: isIn, iter: it, body: statement() };
        }
        init = varDecl(kind);
      } else if (!isOp(';')) {
        if (peek().t === 'name' && peek(1).t === 'kw' && (peek(1).v === 'of' || peek(1).v === 'in')) { var nm2 = next().v, isIn2 = next().v === 'in', it2 = expr(); expect('op', ')'); return { type: 'ForOf', kind: null, name: nm2, isIn: isIn2, iter: it2, body: statement() }; }
        init = { type: 'Expr', a: expr() };
      }
      expect('op', ';');
      var test = isOp(';') ? null : expr(); expect('op', ';');
      var upd = isOp(')') ? null : expr(); expect('op', ')');
      return { type: 'For', init: init, test: test, upd: upd, body: statement() };
    }
    function params() {
      var ps = []; expect('op', '(');
      while (!isOp(')')) { var rest = !!eat('op', '...'); var nm = expect('name').v; var def = eat('op', '=') ? assign() : null; ps.push({ name: nm, def: def, rest: rest }); if (!eat('op', ',')) break; }
      expect('op', ')'); return ps;
    }
    function func(decl) {
      var nm = null; if (is('name')) nm = next().v; else if (decl) throw err('function needs a name');
      var ps = params(), body = block();
      return { type: 'Fn', name: nm, params: ps, body: body, arrow: false };
    }
    function isArrowAhead() {
      if (is('name') && peek(1).t === 'op' && peek(1).v === '=>') return true;
      if (!isOp('(')) return false;
      var depth = 0, j = p;
      for (; j < T.length; j++) { var tk = T[j]; if (tk.t === 'op' && (tk.v === '(' || tk.v === '[' || tk.v === '{')) depth++; else if (tk.t === 'op' && (tk.v === ')' || tk.v === ']' || tk.v === '}')) { depth--; if (depth === 0) break; } else if (tk.t === 'eof') return false; }
      return T[j + 1] && T[j + 1].t === 'op' && T[j + 1].v === '=>';
    }
    function arrow() {
      var ps;
      if (is('name')) ps = [{ name: next().v, def: null, rest: false }]; else ps = params();
      expect('op', '=>');
      if (isOp('{')) return { type: 'Fn', name: null, params: ps, body: block(), arrow: true };
      return { type: 'Fn', name: null, params: ps, body: assign(), arrow: true, exprBody: true };
    }
    function expr() { return assign(); }
    var ASSIGN = set(['=', '+=', '-=', '*=', '/=', '%=', '**=', '<<=', '>>=', '>>>=', '&=', '|=', '^=', '&&=', '||=', '??=']);
    function assign() {
      if (isArrowAhead()) return arrow();
      var left = cond();
      if (is('op') && ASSIGN[peek().v]) {
        var op = next().v;
        if (left.type !== 'Id' && left.type !== 'Mem') throw err('cannot assign to that');
        return { type: 'Assign', op: op, l: left, r: assign() };
      }
      return left;
    }
    function cond() { var t = binary(0); if (eat('op', '?')) { var a = assign(); expect('op', ':'); return { type: 'Cond', t: t, a: a, b: assign() }; } return t; }
    var PREC = [['??'], ['||'], ['&&'], ['|'], ['^'], ['&'], ['==', '!=', '===', '!=='], ['<', '>', '<=', '>=', 'in', 'instanceof'], ['<<', '>>', '>>>'], ['+', '-'], ['*', '/', '%']];
    function binary(lvl) {
      if (lvl >= PREC.length) return power();
      var left = binary(lvl + 1);
      for (;;) {
        var tk = peek(), op = tk.t === 'op' || (tk.t === 'kw' && (tk.v === 'in' || tk.v === 'instanceof')) ? tk.v : null;
        if (!op || PREC[lvl].indexOf(op) < 0) return left;
        if (op === 'instanceof') throw err('instanceof is not available in scripts');
        next();
        var right = binary(lvl + 1);
        left = { type: (op === '&&' || op === '||' || op === '??') ? 'Logic' : 'Bin', op: op, a: left, b: right };
      }
    }
    function power() { var b = unary(); if (eat('op', '**')) return { type: 'Bin', op: '**', a: b, b: power() }; return b; }
    function unary() {
      var tk = peek();
      if (tk.t === 'op' && (tk.v === '!' || tk.v === '-' || tk.v === '+' || tk.v === '~')) { next(); return { type: 'Unary', op: tk.v, a: unary() }; }
      if (tk.t === 'op' && (tk.v === '++' || tk.v === '--')) { next(); var a = unary(); if (a.type !== 'Id' && a.type !== 'Mem') throw err('cannot ' + tk.v + ' that'); return { type: 'Update', op: tk.v, prefix: true, a: a }; }
      if (tk.t === 'kw' && (tk.v === 'typeof' || tk.v === 'void' || tk.v === 'delete')) { next(); return { type: 'Unary', op: tk.v, a: unary() }; }
      var e = postfix();
      return e;
    }
    function postfix() { var e = call(); if (is('op') && (peek().v === '++' || peek().v === '--') && !peek().nl) { var op = next().v; if (e.type !== 'Id' && e.type !== 'Mem') throw err('cannot ' + op + ' that'); return { type: 'Update', op: op, prefix: false, a: e }; } return e; }
    function args() { var as = []; expect('op', '('); while (!isOp(')')) { if (eat('op', '...')) as.push({ type: 'Spread', a: assign() }); else as.push(assign()); if (!eat('op', ',')) break; } expect('op', ')'); return as; }
    function call() {
      var e = primary();
      for (;;) {
        if (eat('op', '.')) { var nm = next(); if (nm.t !== 'name' && nm.t !== 'kw') throw err('expected a property name'); e = { type: 'Mem', o: e, p: nm.v, computed: false }; }
        else if (eat('op', '?.')) {
          if (isOp('(')) e = { type: 'Call', f: e, args: args(), opt: true };
          else if (eat('op', '[')) { var pe = expr(); expect('op', ']'); e = { type: 'Mem', o: e, p: pe, computed: true, opt: true }; }
          else { var nm2 = next(); e = { type: 'Mem', o: e, p: nm2.v, computed: false, opt: true }; }
        }
        else if (isOp('[')) { next(); var ce = expr(); expect('op', ']'); e = { type: 'Mem', o: e, p: ce, computed: true }; }
        else if (isOp('(')) e = { type: 'Call', f: e, args: args() };
        else if (is('tpl')) throw err('tagged templates are not available');
        else return e;
      }
    }
    function primary() {
      var tk = next();
      if (tk.t === 'num' || tk.t === 'str') return { type: 'Lit', v: tk.v };
      if (tk.t === 'tpl') return { type: 'Tpl', strs: tk.strs, exprs: tk.exprs.map(function (s) { var sub = parse('(' + s + ')', name); return sub.body[0].a; }) };
      if (tk.t === 'name') return { type: 'Id', name: tk.v };
      if (tk.t === 'kw') {
        if (tk.v === 'true') return { type: 'Lit', v: true }; if (tk.v === 'false') return { type: 'Lit', v: false };
        if (tk.v === 'null') return { type: 'Lit', v: null }; if (tk.v === 'undefined') return { type: 'Lit', v: undefined };
        if (tk.v === 'this') return { type: 'This' };
        if (tk.v === 'function') return func(false);
        if (tk.v === 'new') throw err('"new" is not available in scripts; use the game API (world.spawn, ...)');
      }
      if (tk.t === 'op' && tk.v === '(') { var e = expr(); expect('op', ')'); return e; }
      if (tk.t === 'op' && tk.v === '[') {
        var items = [];
        while (!isOp(']')) { if (eat('op', '...')) items.push({ type: 'Spread', a: assign() }); else items.push(assign()); if (!eat('op', ',')) break; }
        expect('op', ']'); return { type: 'Arr', items: items };
      }
      if (tk.t === 'op' && tk.v === '{') {
        var props = [];
        while (!isOp('}')) {
          if (eat('op', '...')) { props.push({ spread: assign() }); }
          else {
            var key, computed = null, kt = next();
            if (kt.t === 'op' && kt.v === '[') { computed = expr(); expect('op', ']'); }
            else if (kt.t === 'name' || kt.t === 'kw' || kt.t === 'str' || kt.t === 'num') key = String(kt.v);
            else throw err('bad object key');
            if (isOp('(')) { var ps = params(), body = block(); props.push({ k: key, ck: computed, v: { type: 'Fn', name: key, params: ps, body: body, arrow: false } }); }
            else if (eat('op', ':')) props.push({ k: key, ck: computed, v: assign() });
            else if (kt.t === 'name') props.push({ k: key, v: { type: 'Id', name: key } });
            else throw err('expected : in object');
          }
          if (!eat('op', ',')) break;
        }
        expect('op', '}'); return { type: 'Obj', props: props };
      }
      throw err('unexpected ' + (tk.v != null ? '"' + tk.v + '"' : tk.t), tk);
    }
    return program();
  }
  G.parseScript = parse;

  // ---------------------------------------------------------------- interpreter
  var FN = new WeakMap();
  function Scope(parent, isFn) { this.vars = new Map(); this.parent = parent; this.isFn = !!isFn; }
  Scope.prototype.lookup = function (n) { var s = this; while (s) { if (s.vars.has(n)) return s; s = s.parent; } return null; };
  function isIndex(k) { return typeof k === 'number' ? (k >= 0 && k === Math.floor(k)) : /^(0|[1-9]\d*)$/.test(k); }
  function lim(v) { if (typeof v === 'string' && v.length > MAX_LEN) throw new ScriptError('text too long (over ' + MAX_LEN + ' characters)'); if (Array.isArray(v) && v.length > MAX_LEN) throw new ScriptError('list too long'); return v; }
  function prim(recv, fn, guard) { return function () { var r = fn.apply(recv, arguments); return guard ? lim(r) : r; }; }
  var STR_M = set(['toUpperCase', 'toLowerCase', 'trim', 'trimStart', 'trimEnd', 'split', 'slice', 'substring', 'substr', 'indexOf', 'lastIndexOf', 'includes', 'startsWith', 'endsWith', 'replace', 'replaceAll', 'repeat', 'padStart', 'padEnd', 'charAt', 'charCodeAt', 'concat', 'at', 'localeCompare', 'toString']);
  var NUM_M = set(['toFixed', 'toString', 'toPrecision']);
  var ARR_M = set(['push', 'pop', 'shift', 'unshift', 'slice', 'splice', 'indexOf', 'lastIndexOf', 'includes', 'join', 'map', 'filter', 'reduce', 'reduceRight', 'forEach', 'find', 'findIndex', 'some', 'every', 'sort', 'reverse', 'concat', 'fill', 'flat', 'at', 'keys', 'values', 'entries']);

  function Interp(opts) {
    this.budget = opts.budget || 200000; this.steps = 0; this.depth = 0; this.maxDepth = opts.maxDepth || 120; this.name = opts.name || 'script';
    this.global = new Scope(null, true);
    var g = this.global;
    for (var k in (opts.globals || {})) g.vars.set(k, { v: opts.globals[k], c: true });
  }
  Interp.prototype.tick = function () { if (++this.steps > this.budget) throw new ScriptError(this.name + ': took too long (over ' + this.budget + ' steps) - check for an endless loop'); };
  Interp.prototype.run = function (node) { this.hoist(node.body, this.global); return this.block(node.body, this.global, true); };
  Interp.prototype.hoist = function (body, scope) {
    var self = this;
    (function walk(list, top) {
      list.forEach(function (s) {
        if (!s) return;
        if (s.type === 'FnDecl' && top) scope.vars.set(s.name, { v: self.makeFn(s.fn, scope), c: false });
        else if (s.type === 'Var' && s.kind === 'var') s.decls.forEach(function (d) { if (!scope.vars.has(d.name)) scope.vars.set(d.name, { v: undefined, c: false }); });
        else if (s.type === 'Block') walk(s.body, false);
        else if (s.type === 'If') { walk([s.c], false); walk([s.alt], false); }
        else if (s.type === 'For' || s.type === 'While' || s.type === 'DoWhile' || s.type === 'ForOf') { walk([s.init], false); walk([s.body], false); }
        else if (s.type === 'Try') { walk(s.b.body, false); if (s.handler) walk(s.handler.body, false); if (s.fin) walk(s.fin.body, false); }
      });
    })(body, true);
  };
  Interp.prototype.block = function (body, scope, noNew) {
    var sc = noNew ? scope : new Scope(scope), self = this;
    if (!noNew) body.forEach(function (s) { if (s.type === 'FnDecl') sc.vars.set(s.name, { v: self.makeFn(s.fn, sc), c: false }); });
    for (var i = 0; i < body.length; i++) { var r = this.exec(body[i], sc); if (r) return r; }
    return null;
  };
  Interp.prototype.declare = function (scope, kind, name, v) {
    var target = scope;
    if (kind === 'var') { while (target && !target.isFn) target = target.parent; target = target || this.global; }
    target.vars.set(name, { v: v, c: kind === 'const' });
  };
  Interp.prototype.exec = function (s, sc) {
    this.tick();
    switch (s.type) {
      case 'Expr': this.ev(s.a, sc); return null;
      case 'Var': for (var i = 0; i < s.decls.length; i++) { var d = s.decls[i]; this.declare(sc, s.kind, d.name, d.init ? this.ev(d.init, sc) : undefined); } return null;
      case 'FnDecl': return null;
      case 'Return': return { k: 'return', v: s.a ? this.ev(s.a, sc) : undefined };
      case 'If': if (this.ev(s.t, sc)) return this.exec(s.c, sc); return s.alt ? this.exec(s.alt, sc) : null;
      case 'Block': return this.block(s.body, sc);
      case 'While': while (this.ev(s.t, sc)) { var r = this.exec(s.body, sc); if (r) { if (r.k === 'break') break; if (r.k === 'continue') continue; return r; } } return null;
      case 'DoWhile': do { var r2 = this.exec(s.body, sc); if (r2) { if (r2.k === 'break') break; if (r2.k === 'continue') continue; return r2; } } while (this.ev(s.t, sc)); return null;
      case 'For': {
        var ls = new Scope(sc);
        if (s.init) this.exec(s.init, ls);
        var letNames = s.init && s.init.type === 'Var' && s.init.kind !== 'var' ? s.init.decls.map(function (x) { return x.name; }) : [];
        for (;;) {
          if (s.test && !this.ev(s.test, ls)) break;
          var iter = new Scope(ls);
          letNames.forEach(function (n) { iter.vars.set(n, { v: ls.vars.get(n).v, c: false }); });
          var r3 = this.exec(s.body, iter);
          letNames.forEach(function (n) { ls.vars.get(n).v = iter.vars.get(n).v; });
          if (r3) { if (r3.k === 'break') break; if (r3.k !== 'continue') return r3; }
          if (s.upd) this.ev(s.upd, ls);
        }
        return null;
      }
      case 'ForOf': {
        var col = this.ev(s.iter, sc), list;
        if (s.isIn) list = col == null ? [] : Array.isArray(col) ? col.map(function (x, i2) { return String(i2); }) : typeof col === 'object' ? Object.keys(col).filter(function (k) { return !BLOCKED[k]; }) : [];
        else if (Array.isArray(col)) list = col.slice(); else if (typeof col === 'string') list = Array.from(col); else throw new ScriptError(this.name + ': for...of needs a list or text');
        for (var j = 0; j < list.length; j++) {
          var it = new Scope(sc);
          if (s.kind) it.vars.set(s.name, { v: list[j], c: s.kind === 'const' }); else this.assignId(s.name, list[j], sc);
          var r4 = this.exec(s.body, it);
          if (r4) { if (r4.k === 'break') break; if (r4.k !== 'continue') return r4; }
        }
        return null;
      }
      case 'Break': return { k: 'break' };
      case 'Continue': return { k: 'continue' };
      case 'Throw': { var v = this.ev(s.a, sc); throw (v && v.scriptError) ? v : new ScriptError(typeof v === 'string' ? v : (v && v.message) || String(v), { thrown: v }); }
      case 'Try': {
        var out = null;
        try { out = this.block(s.b.body, sc); }
        catch (e) {
          if (!s.handler || (e && e.budget)) { if (!s.fin) throw e; out = null; this.exec(s.fin, sc); throw e; }
          if (this.steps > this.budget) throw e;                       // a blown budget cannot be caught
          var hs = new Scope(sc);
          if (s.param) hs.vars.set(s.param, { v: e && (e.scriptError || e.safety) ? { message: e.message, name: e.name, safety: !!e.safety } : { message: String(e && e.message || e), name: 'Error' }, c: false });
          out = this.block(s.handler.body, hs, true);
        }
        if (s.fin) { var f = this.exec(s.fin, sc); if (f) return f; }
        return out;
      }
      case 'Switch': {
        var dv = this.ev(s.d, sc), hit = false, bs = new Scope(sc);
        for (var c = 0; c < s.cases.length; c++) {
          var cs = s.cases[c];
          if (!hit && cs.test && this.ev(cs.test, bs) === dv) hit = true;
          if (hit) for (var b = 0; b < cs.body.length; b++) { var r5 = this.exec(cs.body[b], bs); if (r5) { if (r5.k === 'break') return null; return r5; } }
        }
        if (!hit) { var di = -1; s.cases.forEach(function (x, idx) { if (!x.test) di = idx; }); if (di >= 0) for (c = di; c < s.cases.length; c++) for (b = 0; b < s.cases[c].body.length; b++) { var r6 = this.exec(s.cases[c].body[b], bs); if (r6) { if (r6.k === 'break') return null; return r6; } } }
        return null;
      }
      case 'Empty': return null;
    }
    throw new ScriptError(this.name + ': cannot run ' + s.type);
  };
  Interp.prototype.assignId = function (name, v, sc) {
    var s = sc.lookup(name);
    if (!s) throw new ScriptError(this.name + ': "' + name + '" is not declared (use let / var)');
    var cell = s.vars.get(name);
    if (cell.c) throw new ScriptError(this.name + ': "' + name + '" is a constant');
    cell.v = v; return v;
  };
  Interp.prototype.get = function (o, k) {
    if (o == null) throw new ScriptError(this.name + ': cannot read "' + k + '" of ' + o);
    k = typeof k === 'number' ? k : String(k);
    if (BLOCKED[k]) throw new ScriptError(this.name + ': "' + k + '" is not available in scripts');
    var t = typeof o;
    if (t === 'string') { if (k === 'length') return o.length; if (isIndex(k)) return o[k]; return STR_M[k] ? prim(o, String.prototype[k], true) : undefined; }
    if (t === 'number' || t === 'boolean') return NUM_M[k] && t === 'number' ? prim(o, Number.prototype[k]) : undefined;
    if (t === 'function') throw new ScriptError(this.name + ': functions have no readable properties in scripts');
    if (Array.isArray(o)) { if (k === 'length') return o.length; if (isIndex(k)) return o[k]; return ARR_M[k] ? prim(o, Array.prototype[k], true) : undefined; }
    if (Object.prototype.hasOwnProperty.call(o, k)) return o[k];
    return undefined;
  };
  Interp.prototype.put = function (o, k, v) {
    if (o == null) throw new ScriptError(this.name + ': cannot set "' + k + '" of ' + o);
    k = typeof k === 'number' ? k : String(k);
    if (BLOCKED[k]) throw new ScriptError(this.name + ': "' + k + '" is not available in scripts');
    if (typeof o !== 'object') throw new ScriptError(this.name + ': cannot set properties on ' + typeof o);
    if (Array.isArray(o)) {
      if (k === 'length') { if (!(v >= 0 && v <= MAX_LEN)) throw new ScriptError('bad list length'); o.length = v; return v; }
      if (!isIndex(k) || +k > MAX_LEN) throw new ScriptError(this.name + ': lists only take index positions');
      o[k] = v; return v;
    }
    try { o[k] = v; } catch (e) { if (e && (e.safety || e.scriptError)) throw e; throw new ScriptError(this.name + ': "' + k + '" cannot be changed'); }
    return v;
  };
  Interp.prototype.makeFn = function (node, scope, thisV) {
    var self = this, f = function () { return self.call(f, this, Array.prototype.slice.call(arguments)); };
    FN.set(f, { node: node, scope: scope, thisV: thisV });
    return f;
  };
  Interp.prototype.call = function (f, thisArg, args) {
    var d = FN.get(f);
    if (!d) { if (typeof f !== 'function') throw new ScriptError(this.name + ': that is not a function'); return lim(f.apply(thisArg, args)); }
    if (++this.depth > this.maxDepth) { this.depth = 0; throw new ScriptError(this.name + ': too many nested calls'); }
    try {
      var n = d.node, sc = new Scope(d.scope, true);
      sc.vars.set('this', { v: n.arrow ? d.thisV : thisArg, c: true });
      for (var i = 0; i < n.params.length; i++) {
        var pr = n.params[i], v = pr.rest ? args.slice(i) : args[i];
        if (v === undefined && pr.def) v = this.ev(pr.def, sc);
        sc.vars.set(pr.name, { v: v, c: false });
      }
      if (n.exprBody) return this.ev(n.body, sc);
      this.hoist(n.body.body, sc);
      var r = this.block(n.body.body, sc, true);
      return r && r.k === 'return' ? r.v : undefined;
    } finally { this.depth--; }
  };
  Interp.prototype.callValue = function (fv, thisArg, args, label) {
    if (typeof fv !== 'function') throw new ScriptError(this.name + ': ' + label + ' is not a function');
    return this.call(fv, thisArg, args);
  };
  Interp.prototype.args = function (list, sc) {
    var out = [];
    for (var i = 0; i < list.length; i++) { var a = list[i]; if (a.type === 'Spread') { var v = this.ev(a.a, sc); if (!Array.isArray(v)) throw new ScriptError('... needs a list'); out.push.apply(out, v); } else out.push(this.ev(a, sc)); }
    return out;
  };
  function label(n) { return n.type === 'Id' ? n.name : n.type === 'Mem' && !n.computed ? label(n.o) + '.' + n.p : 'value'; }
  Interp.prototype.ev = function (n, sc) {
    this.tick();
    switch (n.type) {
      case 'Lit': return n.v;
      case 'Id': { if (n.name === 'undefined') return undefined; var s = sc.lookup(n.name); if (!s) throw new ScriptError(this.name + ': "' + n.name + '" is not defined'); return s.vars.get(n.name).v; }
      case 'This': { var ts = sc.lookup('this'); return ts ? ts.vars.get('this').v : undefined; }
      case 'Tpl': { var out = n.strs[0]; for (var i = 0; i < n.exprs.length; i++) { var v = this.ev(n.exprs[i], sc); out += (v == null ? String(v) : typeof v === 'object' ? (Array.isArray(v) ? v.join(',') : '[object]') : String(v)) + n.strs[i + 1]; } return lim(out); }
      case 'Arr': return lim(this.args(n.items, sc));
      case 'Obj': {
        var o = {};
        for (var j = 0; j < n.props.length; j++) {
          var pr = n.props[j];
          if (pr.spread) { var sv = this.ev(pr.spread, sc); if (sv && typeof sv === 'object') Object.keys(sv).forEach(function (k) { if (!BLOCKED[k]) o[k] = sv[k]; }); continue; }
          var key = pr.ck ? String(this.ev(pr.ck, sc)) : pr.k;
          if (BLOCKED[key]) throw new ScriptError(this.name + ': "' + key + '" cannot be an object key');
          o[key] = pr.v.type === 'Fn' ? this.makeFn(pr.v, sc, (sc.lookup('this') || { vars: new Map([['this', { v: undefined }]]) }).vars.get('this').v) : this.ev(pr.v, sc);
        }
        return o;
      }
      case 'Fn': { var tv = sc.lookup('this'); return this.makeFn(n, sc, tv ? tv.vars.get('this').v : undefined); }
      case 'Unary': {
        if (n.op === 'typeof') { if (n.a.type === 'Id' && !sc.lookup(n.a.name)) return 'undefined'; var tvv = this.ev(n.a, sc); return typeof tvv; }
        if (n.op === 'delete') { if (n.a.type !== 'Mem') return true; var dobj = this.ev(n.a.o, sc), dk = n.a.computed ? this.ev(n.a.p, sc) : n.a.p; if (dobj && typeof dobj === 'object' && !BLOCKED[dk]) { try { delete dobj[dk]; } catch (e) { throw new ScriptError(this.name + ': cannot delete "' + dk + '"'); } } return true; }
        var a = this.ev(n.a, sc);
        switch (n.op) { case '!': return !a; case '-': return -a; case '+': return +a; case '~': return ~a; case 'void': return undefined; }
        break;
      }
      case 'Update': {
        var old = +this.ev(n.a, sc), nv = n.op === '++' ? old + 1 : old - 1;
        this.store(n.a, nv, sc);
        return n.prefix ? nv : old;
      }
      case 'Bin': {
        var l = this.ev(n.a, sc), r = this.ev(n.b, sc);
        switch (n.op) {
          case '+': return lim(l + r); case '-': return l - r; case '*': return l * r; case '/': return l / r; case '%': return l % r; case '**': return Math.pow(l, r);
          case '==': return l == r; case '!=': return l != r; case '===': return l === r; case '!==': return l !== r; // eslint-disable-line eqeqeq
          case '<': return l < r; case '>': return l > r; case '<=': return l <= r; case '>=': return l >= r;
          case '<<': return l << r; case '>>': return l >> r; case '>>>': return l >>> r; case '&': return l & r; case '|': return l | r; case '^': return l ^ r;
          case 'in': if (r == null || typeof r !== 'object') throw new ScriptError(this.name + ': "in" needs an object'); return Array.isArray(r) ? isIndex(l) && +l < r.length : Object.prototype.hasOwnProperty.call(r, String(l));
        }
        break;
      }
      case 'Logic': { var lv = this.ev(n.a, sc); if (n.op === '&&') return lv ? this.ev(n.b, sc) : lv; if (n.op === '||') return lv ? lv : this.ev(n.b, sc); return lv != null ? lv : this.ev(n.b, sc); }
      case 'Cond': return this.ev(n.t, sc) ? this.ev(n.a, sc) : this.ev(n.b, sc);
      case 'Assign': {
        if (n.op === '=') return this.store(n.l, this.ev(n.r, sc), sc);
        var cur = this.ev(n.l, sc);
        if (n.op === '&&=') return cur ? this.store(n.l, this.ev(n.r, sc), sc) : cur;
        if (n.op === '||=') return cur ? cur : this.store(n.l, this.ev(n.r, sc), sc);
        if (n.op === '??=') return cur != null ? cur : this.store(n.l, this.ev(n.r, sc), sc);
        var rv = this.ev(n.r, sc), res;
        switch (n.op) { case '+=': res = lim(cur + rv); break; case '-=': res = cur - rv; break; case '*=': res = cur * rv; break; case '/=': res = cur / rv; break; case '%=': res = cur % rv; break; case '**=': res = Math.pow(cur, rv); break; case '<<=': res = cur << rv; break; case '>>=': res = cur >> rv; break; case '>>>=': res = cur >>> rv; break; case '&=': res = cur & rv; break; case '|=': res = cur | rv; break; case '^=': res = cur ^ rv; break; }
        return this.store(n.l, res, sc);
      }
      case 'Mem': { var ob = this.ev(n.o, sc); if (n.opt && ob == null) return undefined; return this.get(ob, n.computed ? this.ev(n.p, sc) : n.p); }
      case 'Call': {
        var fv, thisArg;
        if (n.f.type === 'Mem') { thisArg = this.ev(n.f.o, sc); if (n.f.opt && thisArg == null) return undefined; fv = this.get(thisArg, n.f.computed ? this.ev(n.f.p, sc) : n.f.p); }
        else fv = this.ev(n.f, sc);
        if (n.opt && fv == null) return undefined;
        return this.callValue(fv, thisArg, this.args(n.args, sc), label(n.f));
      }
      case 'Spread': throw new ScriptError(this.name + ': ... is only allowed in lists and calls');
    }
    throw new ScriptError(this.name + ': cannot evaluate ' + n.type);
  };
  Interp.prototype.store = function (target, v, sc) {
    if (target.type === 'Id') return this.assignId(target.name, v, sc);
    var o = this.ev(target.o, sc);
    return this.put(o, target.computed ? this.ev(target.p, sc) : target.p, v);
  };
  G.Interp = Interp;

  // ---------------------------------------------------------------- safe builtins
  function freezeAll(o) { Object.keys(o).forEach(function (k) { if (o[k] && typeof o[k] === 'object') freezeAll(o[k]); }); return Object.freeze(o); }
  function builtins() {
    var M = {}; ['abs', 'floor', 'ceil', 'round', 'min', 'max', 'sqrt', 'pow', 'sin', 'cos', 'tan', 'atan2', 'atan', 'asin', 'acos', 'exp', 'log', 'log2', 'log10', 'sign', 'trunc', 'hypot', 'cbrt'].forEach(function (k) { M[k] = Math[k]; });
    ['PI', 'E', 'SQRT2', 'LN2', 'LN10'].forEach(function (k) { M[k] = Math[k]; });
    return {
      Math: Object.freeze(M),
      JSON: Object.freeze({ parse: function (s) { return JSON.parse(String(s)); }, stringify: function (v, r, sp) { return lim(JSON.stringify(v, null, typeof sp === 'number' ? Math.min(sp, 4) : undefined)); } }),
      Object: Object.freeze({ keys: function (o) { return o && typeof o === 'object' ? Object.keys(o) : []; }, values: function (o) { return o && typeof o === 'object' ? Object.keys(o).map(function (k) { return o[k]; }) : []; }, entries: function (o) { return o && typeof o === 'object' ? Object.keys(o).map(function (k) { return [k, o[k]]; }) : []; }, assign: function (t) { for (var i = 1; i < arguments.length; i++) { var s = arguments[i]; if (s && typeof s === 'object') Object.keys(s).forEach(function (k) { if (!BLOCKED[k]) t[k] = s[k]; }); } return t; }, freeze: function (o) { return Object.freeze(o); } }),
      Array: Object.freeze({ isArray: Array.isArray, of: function () { return Array.prototype.slice.call(arguments); }, from: function (v, f) { var a = typeof v === 'string' ? Array.from(v) : Array.isArray(v) ? v.slice() : (v && v.length >= 0 && v.length <= MAX_LEN) ? Array.from({ length: v.length }) : []; return f ? a.map(f) : a; } }),
      Number: function (v) { return Number(v); }, String: function (v) { return String(v); }, Boolean: function (v) { return !!v; },
      parseInt: function (v, r) { return parseInt(v, r); }, parseFloat: function (v) { return parseFloat(v); }, isNaN: function (v) { return isNaN(v); }, isFinite: function (v) { return isFinite(v); },
      Infinity: Infinity, NaN: NaN
    };
  }

  // ---------------------------------------------------------------- node graphs -> StarkScript
  /** Compile a node graph to StarkScript source with the catalog (schema/node_catalog.json, embedded). */
  G.compileGraph = function (graph, catalog) {
    catalog = catalog || (G.root.GSYS_NODES || {}).nodes || {};
    var nodes = {}, links = graph.links || [], gname = 'graph "' + (graph.id || '?') + '"';
    (graph.nodes || []).forEach(function (n) { if (!n || n.id == null) throw new ScriptError(gname + ': a node has no id'); if (nodes[n.id]) throw new ScriptError(gname + ': duplicate node id ' + n.id); nodes[n.id] = n; });
    var uid = 0;
    function def(n) { var d = catalog[n.type]; if (!d) throw new ScriptError(gname + ': node "' + n.id + '" has unknown type "' + n.type + '"'); return d; }
    function lit(v) { return v === undefined ? 'undefined' : JSON.stringify(v); }
    function dataExpr(n, pname, depth) {
      var lk = links.filter(function (l) { return l.to === n.id && l.in === pname; })[0];
      if (lk) { var src = nodes[lk.from]; if (!src) throw new ScriptError(gname + ': link from missing node ' + lk.from); if (depth > 40) throw new ScriptError(gname + ': data links loop'); return code(src, depth + 1, true); }
      var p = n.params || {};
      if (p[pname] !== undefined) return lit(p[pname]);
      var t = (def(n).params || {})[pname];
      return t === 'number' ? '0' : t === 'bool' ? 'false' : t === 'string' ? '""' : 'null';
    }
    function chain(n, out, depth, seen) {
      var lks = links.filter(function (l) { return l.from === n.id && (l.out || 'next') === out && (l.in || 'exec') === 'exec'; });
      return lks.map(function (l) {
        var t = nodes[l.to]; if (!t) throw new ScriptError(gname + ': link to missing node ' + l.to);
        if (seen[t.id]) throw new ScriptError(gname + ': exec links loop at node ' + t.id + ' (use a loop node)');
        var s2 = Object.assign({}, seen); s2[t.id] = 1;
        return code(t, depth + 1, false, s2);
      }).join('\n');
    }
    function code(n, depth, asData, seen) {
      if (depth > 200) throw new ScriptError(gname + ': graph too deep');
      var d = def(n), id = '_' + (uid++);
      if (asData && d.kind !== 'data') throw new ScriptError(gname + ': node "' + n.id + '" is not a value');
      return d.code.replace(/\{(>?)(\w+)\}/g, function (m, arrowOut, key) {
        if (arrowOut) return chain(n, key, depth, seen || {});
        if (key === 'id') return id;
        return dataExpr(n, key, depth);
      });
    }
    var out = [];
    Object.keys(nodes).forEach(function (id) { var n = nodes[id]; if (def(n).kind === 'event') { var s = {}; s[id] = 1; out.push(code(n, 0, false, s)); } });
    return '// compiled from ' + gname + '\n' + out.join('\n');
  };

  // ---------------------------------------------------------------- game API + script system
  G.register('script', 96, function (rt) {
    var SC = { scripts: [], errors: [], vars: {} };
    var handles = new WeakMap();
    function ent(x) { if (x == null) return null; if (x.__id) return rt.get(x.__id); return rt.get(x); }
    function handle(e) {
      if (!e) return null;
      var h = handles.get(e); if (h) return h;
      h = {};
      function prop(k, get, setFn) { Object.defineProperty(h, k, { enumerable: true, get: get, set: setFn || function () { throw new ScriptError('"' + k + '" is read-only'); } }); }
      Object.defineProperty(h, '__id', { value: e.id, enumerable: false });
      prop('id', function () { return e.id; });
      prop('kind', function () { return e.kind; });
      prop('age', function () { return S.ageOf(e); }, function () { throw new S.SafetyError('Child safety: age groups are fixed when a character is created and cannot be changed by scripts.'); });
      prop('name', function () { return e.name; }, function (v) { e.name = S.assertText(String(v), 'entity name'); });
      ['x', 'y', 'z'].forEach(function (k) { prop(k, function () { return e.pos[k]; }, function (v) { if (isFinite(+v)) e.pos[k] = +v; }); });
      prop('rot', function () { return e.rot; }, function (v) { if (isFinite(+v)) e.rot = +v; });
      prop('hp', function () { return e.hp; }, function (v) { v = +v; if (!isFinite(v)) return; if (v < e.hp) { S.guard('damage', e); } e.hp = Math.max(0, Math.min(e.maxHp, v)); if (e.hp <= 0 && rt.sys.combat) rt.sys.combat.kill(e); });
      prop('maxHp', function () { return e.maxHp; });
      prop('dead', function () { return e.dead; });
      prop('team', function () { return e.team; }, function (v) { if (v === 'enemy') S.guard('hostile', e); e.team = String(v); });
      prop('tags', function () { return Array.from(e.tags); });
      var fns = {
        has: function (t) { return e.tags.has(String(t)); }, tag: function (t) { e.tags.add(String(t)); }, untag: function (t) { e.tags.delete(String(t)); },
        say: function (text) { rt.bus.emit('bark', { entity: e, text: S.assertText(String(text), 'say') }); },
        moveTo: function (x, z, sp) { if (rt.sys.ai) rt.sys.ai.goTo(e, x, z, sp); },
        lookAt: function (x, z) { e.rot = Math.atan2(+x - e.pos.x, +z - e.pos.z); },
        damage: function (n) { return rt.sys.combat ? rt.sys.combat.damage(e, n) : 0; },
        heal: function (n) { return rt.sys.combat ? rt.sys.combat.heal(e, n) : 0; },
        kill: function () { return rt.sys.combat ? rt.sys.combat.kill(e) : false; },
        setHostile: function (v) { if (v) { S.guard('hostile', e); e.team = 'enemy'; } else e.team = 'neutral'; },
        despawn: function () { return rt.remove(e); },
        get: function (k) { return (e.flags.vars || {})[String(k)]; }, set: function (k, v) { (e.flags.vars = e.flags.vars || {})[String(k)] = v; },
        distanceTo: function (o) { var t = ent(o); return t ? U.dist(e.pos, t.pos) : Infinity; },
        isMinor: function () { return S.isMinor(e); }
      };
      Object.keys(fns).forEach(function (k) { Object.defineProperty(h, k, { value: fns[k], enumerable: true }); });
      Object.freeze(h); handles.set(e, h);
      return h;
    }
    SC.handle = handle;
    function toScript(v, depth) {
      depth = depth || 0;
      if (v == null || typeof v !== 'object') return typeof v === 'function' ? undefined : v;
      if (v instanceof G.Entity) return handle(v);
      if (depth > 3) return undefined;
      if (Array.isArray(v)) return v.slice(0, 1000).map(function (x) { return toScript(x, depth + 1); });
      var o = {}; Object.keys(v).forEach(function (k) { if (k[0] === '_' || BLOCKED[k] || k === 'rt') return; var x = toScript(v[k], depth + 1); if (x !== undefined) o[k] = x; });
      return o;
    }
    function ids(x) { return x && x.__id ? x.__id : x; }
    function api(script) {
      var sys = rt.sys, self = script.entity ? handle(rt.get(script.entity)) : null;
      function on(ev, fn) { if (typeof fn !== 'function') throw new ScriptError('on() needs a function'); (script.handlers[ev] = script.handlers[ev] || []).push(fn); }
      return {
        on: on, off: function (ev, fn) { script.handlers[ev] = (script.handlers[ev] || []).filter(function (f) { return f !== fn; }); },
        emit: function (ev, data) { if (['damage', 'death', 'spawn', 'remove', 'tick'].indexOf(String(ev)) >= 0) throw new ScriptError('"' + ev + '" is a system event; scripts cannot fake it'); rt.bus.emit(String(ev), toHost(data)); },
        log: function () { var m = Array.prototype.slice.call(arguments).map(function (x) { return typeof x === 'object' ? JSON.stringify(x) : String(x); }).join(' '); SC.log.push('[' + script.id + '] ' + m); if (SC.log.length > 300) SC.log.shift(); },
        after: function (sec, fn) { rt.after(sec, function () { SC.invoke(script, fn, []); }, script); },
        every: function (sec, fn) { rt.every(sec, function () { SC.invoke(script, fn, []); }, script); },
        self: self,
        rand: function () { return rt.rng(); }, randInt: function (a, b) { return rt.rng.int(Math.floor(a), Math.floor(b)); }, pick: function (arr) { return Array.isArray(arr) ? rt.rng.pick(arr) : undefined; },
        clamp: U.clamp, lerp: U.lerp, dist: function (a, b) { var A = ent(a), B = ent(b); return A && B ? U.dist(A.pos, B.pos) : Infinity; },
        vars: Object.freeze({ get: function (k) { return SC.vars[String(k)]; }, set: function (k, v) { if (typeof v === 'string') S.assertText(v, 'saved variable'); SC.vars[String(k)] = v; } }),
        world: Object.freeze(Object.create(null, {
          player: { get: function () { return handle(rt.player); }, enumerable: true },
          time: { get: function () { return rt.time; }, enumerable: true },
          hour: { get: function () { return sys.env ? sys.env.hour() : 12; }, enumerable: true },
          day: { get: function () { return sys.env ? sys.env.day() : 0; }, enumerable: true },
          isNight: { get: function () { return sys.env ? sys.env.isNight() : false; }, enumerable: true },
          weather: { get: function () { return sys.env ? sys.env.weather().type : 'clear'; }, enumerable: true },
          find: { value: function (id) { return handle(rt.find(ids(id))); }, enumerable: true },
          query: { value: function (tag) { return rt.query(String(tag)).map(handle); }, enumerable: true },
          near: { value: function (x, z, r) { return rt.near({ x: +x, z: +z }, +r || 5).map(handle); }, enumerable: true },
          spawn: { value: function (spec) { spec = toHost(spec) || {}; if (spec.name) S.assertText(String(spec.name), 'spawned name'); var e = rt.spawn(spec, false); e.flags._runtime = true; return handle(e); }, enumerable: true },
          setWeather: { value: function (t, i) { if (sys.env) sys.env.setWeather(String(t), i); }, enumerable: true },
          setHour: { value: function (h) { if (sys.env) sys.env.setHour(+h); }, enumerable: true },
          raycast: { value: function (x, z, dx, dz, d) { var r = sys.physics ? sys.physics.ray(+x, +z, +dx, +dz, +d || 20, { entities: true }) : null; return r ? { dist: r.dist, x: r.x, z: r.z, entity: r.entity ? handle(r.entity) : null } : null; }, enumerable: true }
        })),
        entity: function (id) { return handle(rt.get(ids(id))); },
        combat: Object.freeze({
          attack: function (a, w) { var e = ent(a); return e && sys.combat ? sys.combat.attack(e, w) : 0; },
          damage: function (t, n, type) { var e = ent(t); if (!e) return 0; return sys.combat.damage(e, n, null, type); },
          heal: function (t, n) { var e = ent(t); return e ? sys.combat.heal(e, n) : 0; },
          kill: function (t) { var e = ent(t); return e ? sys.combat.kill(e) : false; },
          applyStatus: function (t, id, sec) { var e = ent(t); return e ? sys.combat.applyStatus(e, String(id), sec) : false; },
          useAbility: function (a, id) { var e = ent(a); return e ? sys.combat.useAbility(e, String(id)) : false; },
          setTeam: function (t, team) { var e = ent(t); if (!e) return; if (team === 'enemy') S.guard('hostile', e); e.team = String(team); }
        }),
        ai: Object.freeze({
          setBehavior: function (e, b) { sys.ai.setBehavior(ent(e), String(b)); },
          setTarget: function (e, t) { var tt = ent(t); if (tt) S.guard('target', tt); sys.ai.setTarget(ent(e), tt); },
          goTo: function (e, x, z, sp) { sys.ai.goTo(ent(e), +x, +z, sp); },
          capture: function (e, t) { var tt = ent(t); S.guard('capture', tt); return sys.ai.capture(ent(e), tt); }
        }),
        physics: Object.freeze({
          impulse: function (e, x, z) { var t = ent(e); return t && sys.physics ? sys.physics.impulse(t, +x, +z) : false; },
          blocked: function (x, z, r) { return sys.physics ? !!sys.physics.blocked(+x, +z, +r || 0.3) : false; }
        }),
        inventory: Object.freeze({
          give: function (item, n, who) { return sys.inventory.give(ent(who) || rt.player, String(item), n || 1); },
          take: function (item, n, who) { return sys.inventory.take(ent(who) || rt.player, String(item), n || 1); },
          has: function (item, n, who) { return sys.inventory.has(ent(who) || rt.player, String(item), n || 1); },
          count: function (item, who) { return sys.inventory.count(ent(who) || rt.player, String(item)); },
          craft: function (r, who) { return toScript(sys.inventory.craft(ent(who) || rt.player, String(r))); },
          roll: function (t) { return toScript(sys.inventory.roll(String(t))); }
        }),
        quests: Object.freeze({
          start: function (id) { return sys.quests.start(String(id)); }, complete: function (id) { return sys.quests.complete(String(id)); },
          fail: function (id) { return sys.quests.fail(String(id)); }, advance: function (id, n) { sys.quests.advance(String(id), n); },
          state: function (id) { return sys.quests.state(String(id)); },
          generate: function () { var q = sys.quests.generate(); return q ? q.id : null; },
          active: function () { return sys.quests.active().map(function (q) { return q.id; }); }
        }),
        dialogue: Object.freeze({ start: function (id, npc) { var v = sys.dialogue.start(String(id), ent(npc)); rt.bus.emit('dialogueView', v); return !!v; }, setVar: function (k, v) { sys.dialogue.setVar(String(k), v); }, getVar: function (k) { return sys.dialogue.getVar(String(k)); } }),
        factions: Object.freeze({ rep: function (f) { return sys.factions.rep(String(f)); }, addRep: function (f, n) { return sys.factions.addRep(String(f), +n); }, relation: function (a, b) { return sys.factions.relation(String(a), String(b)); } }),
        economy: Object.freeze({ gold: function () { return sys.economy.gold(); }, add: function (n) { return sys.economy.add(rt.player, +n); }, buy: function (s, i, n) { return toScript(sys.economy.buy(String(s), String(i), n)); }, sell: function (s, i, n) { return toScript(sys.economy.sell(String(s), String(i), n)); }, price: function (s, i, side) { return sys.economy.price(String(s), String(i), side); } }),
        progression: Object.freeze({ award: function (n) { return sys.progression.award(rt.player, +n, 'script'); }, level: function () { return sys.progression.level(rt.player); }, learn: function (t, n) { return toScript(sys.progression.learn(rt.player, String(t), String(n))); } }),
        ui: Object.freeze({ toast: function (t) { sys.ui.toast(S.assertText(String(t), 'toast')); }, say: function (sp, t) { sys.ui.say(sp, t); }, set: function (k, v) { if (typeof v === 'string') S.assertText(v, 'HUD value'); rt.hudVars = rt.hudVars || {}; rt.hudVars[String(k)] = v; } }),
        audio: Object.freeze({ play: function (c) { return sys.audio.play(String(c)); }, music: function (s) { sys.audio.music(String(s)); } }),
        camera: Object.freeze({ shake: function (n) { sys.camera.shake(+n); }, play: function (id) { return sys.camera.play(String(id)); }, stop: function () { sys.camera.stop(); } }),
        romance: Object.freeze({
          start: function (a, b) { var A = ent(a) || a, Bb = ent(b) || b; S.guard('romance', A, Bb); rt.bus.emit('romance', { a: ent(a), b: ent(b) }); return true; },
          affinity: function (a, n) { return sys.dialogue.addAffinity(ent(a) || a, +n, false); },
          addAffinity: function (a, n) { return sys.dialogue.addAffinity(ent(a) || a, +n, true); }
        }),
        appearance: Object.freeze({
          setOutfit: function (e, outfit) { var t = ent(e); var o = String(outfit).toLowerCase(); if (['none', 'nude', 'naked', 'underwear', 'lingerie', 'topless', 'bare', 'undressed', 'nothing'].indexOf(o) >= 0) S.guard('undress', t); if (t) { t.flags.outfit = String(outfit); rt.bus.emit('appearance', { entity: t, outfit: String(outfit) }); } },
          undress: function (e) { var t = ent(e); S.guard('undress', t); if (t) rt.bus.emit('appearance', { entity: t, outfit: 'none' }); }
        })
      };
    }
    function toHost(v) { if (v && v.__id) return rt.get(v.__id); if (Array.isArray(v)) return v.map(toHost); if (v && typeof v === 'object') { var o = {}; Object.keys(v).forEach(function (k) { if (!BLOCKED[k]) o[k] = toHost(v[k]); }); return o; } return v; }
    SC.log = [];
    var TOP = { onStart: 'start', onUpdate: 'update', onInteract: 'interact', onQuestStep: 'questStep', onQuestDone: 'questDone', onDamage: 'damage', onDeath: 'death', onEnterZone: 'enterZone', onExitZone: 'exitZone', onDialogueChoice: 'dialogueChoice', onCollect: 'collect', onHour: 'hour', onDawn: 'dawn', onDusk: 'dusk', onLevelUp: 'levelUp', onCraft: 'craft', onWeather: 'weather' };
    /** Add one script (source) -> script record. Errors are reported, never thrown at the game. */
    SC.add = function (spec) {
      var s = { id: String(spec.id || 'script' + SC.scripts.length), entity: spec.entity || null, handlers: {}, errors: 0, disabled: false, interp: null };
      try {
        var ast = parse(String(spec.source || ''), 'script "' + s.id + '"');
        var g = builtins(), a = api(s); for (var k in a) g[k] = a[k];
        s.interp = new Interp({ globals: g, name: 'script "' + s.id + '"', budget: +spec.budget || 200000 });
        s.interp.run(ast);
        Object.keys(TOP).forEach(function (fn) { var cell = s.interp.global.vars.get(fn); if (cell && typeof cell.v === 'function') (s.handlers[TOP[fn]] = s.handlers[TOP[fn]] || []).push(cell.v); });
      } catch (e) { SC.report(s, e); s.disabled = !s.interp; }
      SC.scripts.push(s);
      return s;
    };
    SC.report = function (s, e) {
      var msg = (e && e.message) || String(e);
      SC.errors.push({ script: s.id, message: msg, safety: !!(e && e.safety), t: rt.time }); if (SC.errors.length > 200) SC.errors.shift();
      G.warn(msg);
      rt.bus.emit('scriptError', { script: s.id, message: msg, safety: !!(e && e.safety) });
      if (++s.errors >= 20) { s.disabled = true; G.warn('script "' + s.id + '" disabled after 20 errors'); }
    };
    SC.invoke = function (s, fn, argv) {
      if (s.disabled || !s.interp) return undefined;
      s.interp.steps = 0; s.interp.depth = 0;
      try { return s.interp.call(fn, undefined, argv); } catch (e) { SC.report(s, e); return undefined; }
    };
    var FILTER = { interact: 'target', damage: 'target', death: 'entity', enterZone: 'entity', exitZone: 'entity' };
    function dispatch(ev, data) {
      SC.scripts.forEach(function (s) {
        var hs = s.handlers[ev]; if (!hs || !hs.length || s.disabled) return;
        if (s.entity && FILTER[ev] && data && data[FILTER[ev]] && data[FILTER[ev]].id !== s.entity && ev !== 'enterZone' && ev !== 'exitZone') return;
        var arg = toScript(data);
        hs.slice().forEach(function (fn) { SC.invoke(s, fn, [arg]); });
      });
    }
    SC.dispatch = dispatch;
    ['start', 'interact', 'questStep', 'questDone', 'damage', 'death', 'enterZone', 'exitZone', 'dialogueChoice', 'collect', 'hour', 'dawn', 'dusk', 'levelUp', 'craft', 'weather', 'win', 'questEvent', 'chestOpened', 'romance', 'bark', 'alert'].forEach(function (ev) {
      rt.bus.on(ev, function (d) {
        var data = d;
        if (ev === 'questStep' || ev === 'questDone') data = { quest: d.quest.id, title: d.quest.title, step: d.step };
        if (ev === 'collect' && d.entity !== rt.player) return;
        dispatch(ev, data);
      });
    });
    SC.init = function () {
      (rt.sysdata.scripts || []).forEach(function (sp) { SC.add(sp); });
      (rt.sysdata.graphs || []).forEach(function (g) {
        var src;
        try { src = G.compileGraph(g); } catch (e) { SC.report({ id: String(g.id || 'graph'), errors: 0 }, e); return; }
        SC.add({ id: 'graph:' + (g.id || SC.scripts.length), entity: g.entity, source: src });
      });
    };
    SC.update = function (dt) { if (SC.scripts.length) dispatch('update', { dt: dt }); };
    SC.save = function () { return { vars: U.copy(SC.vars) }; };
    SC.load = function (d) { SC.vars = (d && d.vars) || {}; };          // save-system restore hook
    return SC;
  });
})(GSYS);
