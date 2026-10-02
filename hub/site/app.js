/* SurveyAtlas hub — single-page app for every literature atlas (vanilla JS, no build step).
   Data per atlas: a/<id>/{papers,taxonomy,stats,meta,excluded}.json · /api/marks?atlas=<id>
   Hub: /api/atlases (list) · /api/settings (default atlas, theme, background, accent, density)
   Routes: #/<atlas>/<view>?… · #/atlases (all atlases) · #/ → default atlas */
(() => {
  'use strict';

  // ───────── helpers ─────────
  // admin key (unlocks editing from other devices): sent with every api/ call
  let KEY = ''; try { KEY = localStorage.getItem('atlas-admin-key') || ''; } catch (_) {}
  const _fetch = window.fetch.bind(window);
  const fetch = (url, opts = {}) => (KEY && String(url).startsWith('api/')
    ? _fetch(url, { ...opts, headers: { ...(opts.headers || {}), 'X-Atlas-Key': KEY } }) : _fetch(url, opts))
    .then((r) => { if (r.status === 401) location.href = '/login'; return r; });  // site password: session expired
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]);
  const fmt = (n) => Number(n || 0).toLocaleString('en-US');
  const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  const ICON = {
    star: '<svg viewBox="0 0 24 24"><path d="M12 2.8l2.8 5.9 6.4.8-4.7 4.4 1.2 6.4L12 17.2l-5.7 3.1 1.2-6.4L2.8 9.5l6.4-.8z"/></svg>',
    cite: '<svg viewBox="0 0 24 24"><path d="M7 7h4v4H8c0 2 1 3 3 3v2c-3 0-4-2-4-5zM14 7h4v4h-3c0 2 1 3 3 3v2c-3 0-4-2-4-5z"/></svg>',
    org: '<svg viewBox="0 0 24 24"><path d="M4 21V5l8-3v6l8 3v10zM7 8h2v2H7zm0 4h2v2H7zm0 4h2v2H7zm8-1h2v2h-2zm0-3h2v2h-2z"/></svg>',
    ext: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
    copy: '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/></svg>',
    dl: '<svg viewBox="0 0 24 24"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>',
    filter: '<svg viewBox="0 0 24 24"><path d="M4 5h16l-6 7v6l-4 2v-8z"/></svg>',
    info: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/></svg>',
    search: '<svg class="s-ico" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>',
    pen: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4"/></svg>',
    book: '<svg viewBox="0 0 24 24"><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 5v16M8 7h7M8 11h7"/></svg>',
    ask: '<svg viewBox="0 0 24 24"><path d="M4 5h16v11H9l-5 4z"/><path d="M9.8 8.6a2.3 2.3 0 1 1 3.2 2.1c-.6.3-1 .7-1 1.3M12 14v.2"/></svg>',
  };

  // current atlas (swapped by loadAtlas)
  let P = [], TAX = null, STATS = null, META = {}, UIC = {}, MARKS = {}, EXCL = null, MARKS_REMOTE = true;
  let BY = new Map();
  let T = { scope: {}, task: {}, setting: {}, pd: {}, contrib: {} };
  let PD_ORDER = [], PD_IDX = {}, TASK_GROUPS = [], PRESETS = [];
  // hub
  let ATLASES = [], SETTINGS = {}, A = '', ADMIN = false, ADMIN_LOCAL = false, JOBS = [], GATE = false, ON_HUB = false, STATIC = false, CAN_ASK = false;
  const hubUi = { editing: null, focusNew: false, newOpen: false };
  const CACHE = {};
  // paradigm colours follow the paradigm ORDER (validated categorical slots), "none" is neutral
  const pdVar = (c) => (!c || c === 'none' || PD_IDX[c] == null) ? 'var(--cat-none)' : `var(--cat-${(PD_IDX[c] % 8) + 1})`;
  const builtYear = () => +(STATS?.built_at || '').slice(0, 4) || new Date().getFullYear();
  const yearSpan = () => { const y0 = META.timeline_start || 2017, y1 = Math.max(y0, ...P.map((p) => p.y || 0)); const ys = []; for (let y = y0; y <= y1; y++) ys.push(y); return ys; };
  const ga = (o) => esc(JSON.stringify(o));
  const normBm = (b) => b.replace(/^other:\s*/i, '').trim();

  // ───────── state ─────────
  const FKEYS = ['sc', 'rd', 'pd', 'tk', 'st', 'ct', 'yr', 'vn', 'au', 'co', 'bm', 'tr'];
  const DEFAULT_SC = ['core'];
  const S = {
    view: 'library', q: '', sort: 'auto', dense: false, limit: 60, star: false,
    f: Object.fromEntries(FKEYS.map((k) => [k, new Set()])),
  };
  S.f.sc = new Set(DEFAULT_SC);

  const FACETS = [
    { key: 'sc', title: 'Scope', get: (p) => [p.sc], label: (c) => T.scope[c]?.label || c, order: () => TAX.scopes.map((s) => s.code).filter((c) => c !== 'out') },
    { key: 'rd', title: 'Deep reading', get: (p) => [p.r ? 'read' : 'unread'], label: (c) => (c === 'read' ? 'Deep-read' : 'Not read yet'), order: () => ['read', 'unread'] },
    { key: 'pd', title: 'Paradigm', get: (p) => [p.pd], label: (c) => T.pd[c]?.label || c, order: () => PD_ORDER, dot: (c) => pdVar(c) },
    { key: 'tk', title: 'Task', get: (p) => p.tk, label: (c) => T.task[c]?.label || c, order: () => TAX.tasks.map((t) => t.code) },
    { key: 'st', title: 'Setting', get: (p) => p.st, label: (c) => T.setting[c]?.label || c, order: () => TAX.settings.map((t) => t.code) },
    { key: 'ct', title: 'Contribution', get: (p) => p.ct, label: (c) => T.contrib[c]?.label || c, order: () => TAX.contribs.map((t) => t.code) },
    { key: 'yr', title: 'Year', get: (p) => [String(p.y)], hist: true },
    { key: 'co', title: 'Industry authors', get: (p) => (p.co && p.co.length ? p.co : ['No industry author']), label: (c) => c, top: 10, search: true },
    { key: 'vn', title: 'Venue', get: (p) => [p.vn || 'Preprint'], label: (c) => c, top: 10, search: true },
    { key: 'au', title: 'Author', get: (p) => p.a || [], label: (c) => c, top: 8, search: true },
    { key: 'bm', title: 'Benchmark', get: (p) => p._bm, label: (c) => c, top: 12, search: true },
    { key: 'tr', title: 'Technique', get: (p) => p.tr, label: (c) => c, top: 10 },
  ];
  const FORDER = ['sc', 'yr', 'au', 'pd', 'tk', 'st', 'ct', 'vn', 'bm', 'co', 'tr', 'rd'];  // sidebar order
  FACETS.sort((x, y) => FORDER.indexOf(x.key) - FORDER.indexOf(y.key));
  const FBY = Object.fromEntries(FACETS.map((f) => [f.key, f]));
  const facetUi = { expanded: new Set(), collapsed: new Set(['ct', 'tr', 'co']), search: {} };

  // ───────── query parsing ─────────
  function parseQuery(q) {
    const out = { terms: [], neg: [], fields: [] };
    const re = /(-)?(?:(\w+):)?(?:"([^"]+)"|(\S+))/g;
    let m;
    while ((m = re.exec(q))) {
      const neg = !!m[1], field = m[2]?.toLowerCase(), val = (m[3] ?? m[4] ?? '').toLowerCase();
      if (!val) continue;
      if (field && ['task', 'tk', 'pd', 'paradigm', 'bench', 'bm', 'venue', 'co', 'org', 'industry', 'year', 'author', 'au', 'name', 'id'].includes(field)) {
        out.fields.push({ field, val, neg });
      } else if (neg) out.neg.push(val);
      else out.terms.push(field ? `${field}:${val}` : val);
    }
    return out;
  }
  function fieldOk(p, { field, val }) {
    switch (field) {
      case 'task': case 'tk': return p.tk.some((t) => t.toLowerCase().includes(val));
      case 'pd': case 'paradigm': return p.pd.includes(val);
      case 'bench': case 'bm': return p._bm.some((b) => b.toLowerCase().includes(val));
      case 'venue': return (p.v || 'preprint').toLowerCase().includes(val);
      case 'co': case 'org': case 'industry': return (p.co || []).some((c) => c.toLowerCase().includes(val === 'any' || val === 'yes' ? '' : val));
      case 'author': case 'au': return p._au.includes(val);
      case 'name': return (p._n || '').includes(val);
      case 'id': return p.id.toLowerCase().startsWith(val);
      case 'year': {
        const y = p.y, r = val.match(/^(>=|<=|>|<)?(\d{4})(?:-(\d{4}))?$/);
        if (!r) return true;
        const a = +r[2], b = r[3] ? +r[3] : null;
        if (b) return y >= a && y <= b;
        return r[1] === '>=' ? y >= a : r[1] === '<=' ? y <= a : r[1] === '>' ? y > a : r[1] === '<' ? y < a : y === a;
      }
    }
    return true;
  }
  let QP = parseQuery('');
  function queryOk(p) {
    for (const t of QP.terms) if (!p._h.includes(t)) return false;
    for (const t of QP.neg) if (p._h.includes(t)) return false;
    for (const f of QP.fields) if (fieldOk(p, f) === f.neg) return false;
    return true;
  }
  function score(p) {
    let s = 0;
    for (const t of QP.terms) {
      if (p._n === t) s += 60; else if (p._n && p._n.includes(t)) s += 25;
      if (p._t.includes(t)) s += 10;
      if (p._tl.includes(t)) s += 4;
      if (p._au.includes(t)) s += 5;
      s += 1;
    }
    return s + Math.log1p(p.c || 0) * 0.9 + (p.y >= 2024 ? 0.6 : 0);
  }

  // ───────── filtering ─────────
  function passes(p, except) {
    for (const k of FKEYS) {
      if (k === except) continue;
      const sel = S.f[k];
      if (!sel.size) continue;
      const vals = FBY[k].get(p);
      let ok = false;
      for (const v of vals) if (sel.has(v)) { ok = true; break; }
      if (!ok) return false;
    }
    if (S.star && !MARKS[p.id]?.star) return false;
    return true;
  }
  function filtered() {
    const out = [];
    for (const p of P) if (queryOk(p) && passes(p)) out.push(p);
    const mode = S.sort === 'auto' ? (QP.terms.length ? 'rel' : 'new') : S.sort;
    const cmp = {
      rel: (a, b) => b._s - a._s,
      new: (a, b) => (b.d || '').localeCompare(a.d || ''),
      old: (a, b) => (a.d || '').localeCompare(b.d || ''),
      cited: (a, b) => (b.c || 0) - (a.c || 0),
      title: (a, b) => a.t.localeCompare(b.t),
      author: (a, b) => surname(a).localeCompare(surname(b)) || (b.y || 0) - (a.y || 0),
      added: (a, b) => (b.fs || '').localeCompare(a.fs || '') || (b.d || '').localeCompare(a.d || ''),
    }[mode];
    if (mode === 'rel') for (const p of out) p._s = score(p);
    return out.sort(cmp);
  }
  function surname(p) { const n = (p.a || [])[0] || '~'; return n.trim().split(/\s+/).pop().toLowerCase(); }
  function facetCounts(key) {
    const cnt = new Map();
    const f = FBY[key];
    for (const p of P) {
      if (!queryOk(p) || !passes(p, key)) continue;
      for (const v of new Set(f.get(p))) cnt.set(v, (cnt.get(v) || 0) + 1);
    }
    return cnt;
  }

  // ───────── URL state ─────────
  function stateToHash() {
    const u = new URLSearchParams();
    if (S.q) u.set('q', S.q);
    for (const k of FKEYS) {
      const v = [...S.f[k]];
      if (k === 'sc') {
        const def = v.length === DEFAULT_SC.length && v.every((x) => DEFAULT_SC.includes(x));
        if (!def) u.set('sc', v.length ? v.join(',') : 'any');
      } else if (v.length) u.set(k, v.join(','));
    }
    if (S.sort !== 'auto') u.set('sort', S.sort);
    if (S.star) u.set('star', '1');
    const s = u.toString();
    return `#/${A}/library${s ? '?' + s : ''}`;
  }
  function hashToState(qs) {
    const u = new URLSearchParams(qs);
    S.q = u.get('q') || '';
    for (const k of FKEYS) {
      const v = u.get(k);
      if (k === 'sc') S.f.sc = new Set(v == null ? DEFAULT_SC : v === 'any' ? [] : v.split(','));
      else S.f[k] = new Set(v ? v.split(',') : []);
    }
    S.sort = u.get('sort') || 'auto';
    S.star = u.get('star') === '1';
    QP = parseQuery(S.q.toLowerCase());
  }
  function goLibrary(f = {}, q = '') {
    for (const k of FKEYS) S.f[k] = new Set(f[k] || (k === 'sc' ? DEFAULT_SC : []));
    S.q = q; S.sort = f.sort || 'auto'; S.star = false; S.limit = 60;
    QP = parseQuery(q.toLowerCase());
    const h = stateToHash();
    if (location.hash === h) route(); else location.hash = h;
    window.scrollTo(0, 0);
  }
  const syncHash = debounce(() => history.replaceState(null, '', stateToHash()), 250);

  // ───────── boot / atlas loading ─────────
  async function boot() {
    const [atlases, settings, who] = await Promise.all([
      fetch('api/atlases').then((r) => r.json()),
      fetch('api/settings').then((r) => r.json()),
      fetch('api/whoami').then((r) => r.json()).catch(() => ({ admin: false })),
    ]);
    ATLASES = atlases; SETTINGS = settings; ADMIN = !!who.admin; ADMIN_LOCAL = !!who.local; GATE = !!who.gate; ON_HUB = !!who.on_hub; STATIC = !!who.static; CAN_ASK = !!who.ask;
    document.documentElement.classList.toggle('static', STATIC);
    if (!ADMIN) { try { Object.assign(SETTINGS, JSON.parse(localStorage.getItem('atlas-look-local') || '{}')); } catch (_) {} }
    applySettings();
    renderSwitcher();
    window.addEventListener('hashchange', route);
    await route();
  }

  async function loadAtlas(id) {
    if (A === id && P.length) return;
    let c = CACHE[id];
    if (!c) {
      const [papers, tax, stats, meta] = await Promise.all(['papers', 'taxonomy', 'stats', 'meta'].map((f) => fetch(`a/${id}/${f}.json`).then((r) => { if (!r.ok) throw new Error(`atlas "${id}" is not built yet — run ./atlas update ${id}`); return r.json(); })));
      let marks = {}, remote = true;
      try { if (STATIC) throw 0; const r = await fetch(`api/marks?atlas=${id}`); if (!r.ok) throw 0; marks = await r.json(); }  // static copies keep marks in the browser
      catch (_) { remote = false; try { marks = JSON.parse(localStorage.getItem(`atlas-marks-${id}`) || '{}'); } catch (e) { marks = {}; } }
      const by = new Map();
      for (const p of papers) {
        p._bm = [...new Set((p.bm || []).map(normBm))];
        p._n = (p.n || '').toLowerCase();
        p._t = p.t.toLowerCase();
        p._tl = (p.tl || '').toLowerCase();
        p._au = (p.a || []).join(' · ').toLowerCase();
        p._h = [p._n, p._t, p._tl, p._au, (p.ab || '').toLowerCase(), p._bm.join(' ').toLowerCase(), (p.v || '').toLowerCase(), p.id.toLowerCase(), (p.rk || '').toLowerCase(), (p.sm || p.zh || '').toLowerCase()].join(' \u0001 ');
        by.set(p.id, p);
      }
      const t = { scope: {}, task: {}, setting: {}, pd: {}, contrib: {} };
      for (const x of tax.scopes) t.scope[x.code] = x;
      for (const x of tax.tasks) t.task[x.code] = x;
      for (const x of tax.settings) t.setting[x.code] = x;
      for (const x of tax.paradigms) t.pd[x.code] = x;
      for (const x of tax.contribs) t.contrib[x.code] = x;
      c = CACHE[id] = { papers, tax, stats, meta, marks, remote, by, t, excl: null, board: null, surveys: null };
    }
    A = id; P = c.papers; TAX = c.tax; STATS = c.stats; META = c.meta; UIC = c.meta.ui || {};
    MARKS = c.marks; MARKS_REMOTE = c.remote; BY = c.by; T = c.t; EXCL = c.excl;
    const pds = TAX.paradigms.map((x) => x.code);
    PD_ORDER = [...pds.filter((x) => x !== 'none'), ...(pds.includes('none') ? ['none'] : [])];
    PD_IDX = Object.fromEntries(PD_ORDER.filter((x) => x !== 'none').map((x, i) => [x, i]));
    TASK_GROUPS = UIC.task_groups?.length ? UIC.task_groups : [['Tasks', TAX.tasks.map((x) => x.code)]];
    PRESETS = UIC.presets || [];
    for (const k of FKEYS) S.f[k] = new Set(k === 'sc' ? DEFAULT_SC : []);
    S.q = ''; S.sort = 'auto'; S.star = false; S.limit = 60; QP = parseQuery('');
    facetUi.search = {};
    $('#app').innerHTML = '';
    const nCore = P.filter((p) => p.sc === 'core').length;
    $('#stamp').textContent = `${fmt(nCore)} core papers · built ${STATS.built_at.slice(0, 10)}`;
    document.title = `${META.title || id} · SurveyAtlas`;
    $$('#tabs a').forEach((a) => { a.href = `#/${id}/${a.dataset.view}`; });
    renderSwitcher();
  }

  // ───────── router ─────────
  const VIEWS = ['library', 'ask', 'map', 'timeline', 'benchmarks', 'surveys', 'survey', 'about', 'paper'];
  async function route() {
    const h = location.hash.replace(/^#\/?/, '');
    const [path, qs] = h.split('?');
    let [seg0, seg1] = path.split('/');
    closeDrawer(true);
    closeMenus();
    if (seg0 === 'guide') { S.view = 'guide'; $('#tabs').classList.add('hidden'); return renderGuide(seg1); }
    if (seg0 === 'unlock') { await unlock(decodeURIComponent(seg1 || '')); history.replaceState(null, '', '#/atlases'); S.view = 'atlases'; $('#tabs').classList.add('hidden'); return renderHub(); }
    if (seg0 === 'settings') { S.view = 'atlases'; $('#tabs').classList.add('hidden'); renderHub(); openSettings(); return; }
    if (seg0 === 'atlases') { S.view = 'atlases'; $$('#tabs a').forEach((a) => a.classList.remove('on')); $('#tabs').classList.add('hidden'); return renderHub(); }
    $('#tabs').classList.remove('hidden');
    const ids = ATLASES.filter((x) => x.built).map((x) => x.id);
    let id = seg0, view = seg1;
    if (!ids.includes(seg0)) { id = SETTINGS.default_atlas || ids[0]; view = VIEWS.includes(seg0) ? seg0 : 'library'; }
    if (!id) { S.view = 'atlases'; return renderHub(); }
    view = VIEWS.includes(view) ? view : 'library';
    try { await loadAtlas(id); }
    catch (err) { $('#app').innerHTML = `<div class="empty"><h3>Could not load “${esc(id)}”</h3><p>${esc(err.message)}</p></div>`; return; }
    S.view = view;
    $$('#tabs a').forEach((a) => a.classList.toggle('on', a.dataset.view === (S.view === 'survey' ? 'surveys' : S.view)));
    if (S.view === 'paper') {
      const pid = decodeURIComponent(qs || '');
      if (!$('.lib')) renderLibrary();
      if (BY.has(pid)) openDrawer(pid, true);
      return;
    }
    if (S.view === 'library') { hashToState(qs || ''); renderLibrary(); }
    else if (S.view === 'ask') renderAsk();
    else if (S.view === 'map') renderMap();
    else if (S.view === 'timeline') renderTimeline();
    else if (S.view === 'benchmarks') renderBenchmarks();
    else if (S.view === 'surveys') renderSurveys();
    else if (S.view === 'survey') renderSurvey(path.split('/')[2] || '');
    else if (S.view === 'about') renderAbout();
    window.scrollTo(0, 0);
  }

  // ───────── library ─────────
  function renderLibrary() {
    const app = $('#app');
    app.onclick = null;
    if (!$('.lib', app)) {
      app.innerHTML = `
        <div class="lib">
          <aside class="facets" id="facets"></aside>
          <section>
            <div class="searchbar">${ICON.search}
              <input id="q" type="search" autocomplete="off" spellcheck="false" placeholder="Search ${fmt(P.length)} papers — method, title, author, benchmark…">
              <span class="kbd"><kbd>/</kbd> to search</span>
            </div>
            <div class="presets">${PRESETS.length ? '<span class="lbl">Try</span>' : ''}${PRESETS.map(([l], i) => `<button class="preset" data-preset="${i}">${esc(l)}</button>`).join('')}</div>
            <div class="syntax">Search syntax: <code>task:${esc(TAX.tasks[0]?.code || 'X')}</code> ${Object.values(TAX.benchmarks || {}).flat()[0] ? `<code>bench:${esc(Object.values(TAX.benchmarks).flat()[0])}</code>` : ''} <code>year:>=2024</code> <code>year:2019-2021</code> <code>venue:CVPR</code> <code>author:smith</code> <code>pd:${esc(PD_ORDER[0] || 'x')}</code> <code>"exact phrase"</code> <code>-exclude</code> <a href="#/guide/search">full syntax →</a></div>
            ${(() => { try { return localStorage.getItem('atlas-guide-seen') ? '' : `<div class="tip" id="guideTip">${ICON.info}<span>First time here? The <a href="#/guide" data-seen>guide</a> explains every page in 3 minutes.</span><button class="icon-btn" data-seen aria-label="Dismiss">✕</button></div>`; } catch (_) { return ''; } })()}
            <div class="fbar card" id="fbar">
              <div class="fb-year">
                <div class="fb-head"><span class="fb-lbl">Years</span><b class="fb-val" id="yrVal"></b></div>
                <div class="yr-range" id="yrRange"><div class="yr-spark" id="yrSpark"></div><div class="yr-track"><i id="yrFill"></i></div>
                  <input type="range" id="yrLo" aria-label="From year"><input type="range" id="yrHi" aria-label="To year"></div>
                <div class="yr-quick" id="yrQuick"></div>
              </div>
              <div class="fb-author">
                <div class="fb-head"><span class="fb-lbl">Authors</span><span class="fb-hint" id="auHint"></span></div>
                <div class="ac-wrap"><svg class="ac-ico" viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>
                  <input id="auQ" autocomplete="off" spellcheck="false" placeholder="Type an author name…" role="combobox" aria-expanded="false" aria-controls="auList">
                  <div class="ac-list" id="auList" role="listbox"></div></div>
                <div class="au-chips" id="auChips"></div>
              </div>
              <div class="fb-venue">
                <div class="fb-head"><span class="fb-lbl">Venue</span></div>
                <select class="select" id="vnSel" aria-label="Venue"></select>
                <div class="vn-seg seg" id="pubSeg"><button data-p="">All</button><button data-p="pub">Published</button><button data-p="pre">Preprints</button></div>
              </div>
            </div>
            <div id="auCard"></div>
            <div class="toolbar">
              <button class="btn filter-toggle" id="ftog">${ICON.filter} Filters</button>
              <span class="count" id="count"></span>
              <label class="btn" title="Only starred"><input type="checkbox" id="starOnly" style="margin:0 4px 0 0"> ★ Starred</label>
              <select class="select" id="sort" aria-label="Sort">
                <option value="auto">Relevance / newest</option><option value="new">Newest</option><option value="old">Oldest</option>
                <option value="cited">Most cited</option><option value="added">Recently added</option><option value="title">Title A–Z</option><option value="author">First author A–Z</option>
              </select>
              <div class="seg" id="dens"><button data-d="0">Cards</button><button data-d="1">Compact</button></div>
              <button class="btn" id="expBib" title="Download BibTeX for all results">${ICON.dl} BibTeX</button>
              <button class="btn" id="expCsv" title="Download all results as CSV (opens in Excel / Sheets)">${ICON.dl} CSV</button>
              <button class="btn" id="expCite" title="Copy \\cite{} for all results">${ICON.copy} \\cite</button>
            </div>
            <div class="active-filters" id="af"></div>
            <div class="results" id="results"></div>
            <div class="sentinel" id="sentinel"></div>
          </section>
        </div>`;
      const q = $('#q');
      q.addEventListener('input', debounce(() => { S.q = q.value; QP = parseQuery(S.q.toLowerCase()); S.limit = 60; updateLibrary(); syncHash(); }, 90));
      $('#sort').addEventListener('change', (e) => { S.sort = e.target.value; S.limit = 60; updateLibrary(); syncHash(); });
      $('#starOnly').addEventListener('change', (e) => { S.star = e.target.checked; S.limit = 60; updateLibrary(); syncHash(); });
      $('#dens').addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; S.dense = b.dataset.d === '1'; try { localStorage.setItem('atlas-dense', S.dense ? '1' : '0'); } catch (_) {} updateLibrary(); });
      $('#ftog').addEventListener('click', () => $('#facets').classList.toggle('open'));
      $('.presets').addEventListener('click', (e) => { const b = e.target.closest('[data-preset]'); if (b) goLibrary(PRESETS[+b.dataset.preset][1]); });
      $('#expBib').addEventListener('click', () => exportBib(filtered()));
      $('#expCsv').addEventListener('click', () => exportCsv(filtered()));
      $('#expCite').addEventListener('click', () => copy(`\\cite{${filtered().map((p) => p.key).join(',')}}`, 'Copied \\cite for all results'));
      $('#facets').addEventListener('click', onFacetClick);
      $('#facets').addEventListener('input', (e) => { if (e.target.matches('.facet-search')) { facetUi.search[e.target.dataset.k] = e.target.value; renderFacets(e.target.dataset.k); } });
      $('#af').addEventListener('click', onActiveFilterClick);
      initFilterBar();
      $('#results').addEventListener('click', onResultsClick);
      if ($('#guideTip')) $('#guideTip').addEventListener('click', (e) => { if (e.target.closest('[data-seen]')) { try { localStorage.setItem('atlas-guide-seen', '1'); } catch (_) {} $('#guideTip').remove(); } });
      new IntersectionObserver((ents) => {
        if (ents[0].isIntersecting && S.view === 'library' && S._n > S.limit) { S.limit += 60; renderResults(true); }
      }, { rootMargin: '600px' }).observe($('#sentinel'));
      S.dense = SETTINGS.density === 'compact';
      try { const d = localStorage.getItem('atlas-dense'); if (d != null) S.dense = d === '1'; } catch (_) {}
    }
    $('#q').value = S.q;
    $('#sort').value = S.sort;
    $('#starOnly').checked = S.star;
    updateLibrary();
  }

  function updateLibrary() {
    S._list = filtered();
    S._n = S._list.length;
    $('#count').innerHTML = `<b>${fmt(S._n)}</b> paper${S._n === 1 ? '' : 's'}`;
    $$('#dens button').forEach((b) => b.classList.toggle('on', (b.dataset.d === '1') === S.dense));
    $('#results').classList.toggle('compact', S.dense);
    renderFacets();
    renderFilterBar();
    renderActive();
    renderResults(false);
  }

  // ───────── filter bar: year range, author search + profile, venue (all write the same facet state as the sidebar) ─────────
  let AU = null;  // author → {n, y0, y1}
  function authorIndex() {
    if (AU && AU._atlas === A) return AU;
    AU = new Map(); AU._atlas = A;
    for (const p of P) for (const a of p.a || []) {
      const e = AU.get(a) || { n: 0, y0: 9999, y1: 0, core: 0 };
      e.n++; if (p.y > 1900) { e.y0 = Math.min(e.y0, p.y); e.y1 = Math.max(e.y1, p.y); } if (p.sc === 'core') e.core++;
      AU.set(a, e);
    }
    return AU;
  }
  const fold = (t) => t.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  function yearBounds() {
    const ys = P.map((p) => p.y).filter((y) => y > 1900).sort((a, b) => a - b);
    const lo = Math.max(ys[Math.floor(ys.length * 0.01)] || 2010, (META.timeline_start || 2017) - 6);
    return [lo, ys[ys.length - 1] || new Date().getFullYear()];
  }
  function setYears(a, b) {
    const [lo, hi] = yearBounds();
    S.f.yr = new Set(a <= lo && b >= hi ? [] : [...new Set(P.map((p) => p.y))].filter((y) => (y >= a || a <= lo) && y <= b && y > 0).map(String));
    S.limit = 60; updateLibrary(); syncHash();
  }
  function curYears() {
    const [lo, hi] = yearBounds();
    if (!S.f.yr.size) return [lo, hi];
    const ys = [...S.f.yr].map(Number);
    return [Math.max(lo, Math.min(...ys)), Math.min(hi, Math.max(...ys))];
  }
  function initFilterBar() {
    const [lo, hi] = yearBounds();
    for (const id of ['yrLo', 'yrHi']) { const r = $('#' + id); r.min = lo; r.max = hi; r.step = 1; }
    const onRange = (e) => {
      let a = +$('#yrLo').value, b = +$('#yrHi').value;
      if (a > b) { if (e.target.id === 'yrLo') a = b; else b = a; $('#yrLo').value = a; $('#yrHi').value = b; }
      paintYears(a, b);
      clearTimeout(onRange._t); onRange._t = setTimeout(() => setYears(a, b), 120);
    };
    $('#yrLo').addEventListener('input', onRange); $('#yrHi').addEventListener('input', onRange);
    $('#yrQuick').addEventListener('click', (e) => { const b = e.target.closest('[data-yr]'); if (!b) return; const [a, z] = b.dataset.yr.split('-').map(Number); setYears(a, z); });
    $('#yrSpark').addEventListener('click', (e) => { const b = e.target.closest('[data-y]'); if (!b) return; const y = +b.dataset.y; const [a, z] = curYears(); setYears(a === y && z === y ? lo : y, a === y && z === y ? hi : y); });
    // authors
    const inp = $('#auQ'), list = $('#auList');
    let hits = [], sel = 0;
    const close = () => { list.classList.remove('open'); inp.setAttribute('aria-expanded', 'false'); };
    const show = () => {
      const t = fold(inp.value.trim());
      if (t.length < 2) { close(); return; }
      const idx = authorIndex(); hits = [];
      for (const [name, e] of idx) { const f = fold(name); const at = f.indexOf(t); if (at < 0) continue; hits.push([name, e, (at === 0 || f[at - 1] === ' ' ? 2 : 1)]); }
      hits.sort((a, b) => b[2] - a[2] || b[1].n - a[1].n); hits = hits.slice(0, 8); sel = 0;
      list.innerHTML = hits.length ? hits.map(([name, e], i) => `<button class="ac-opt${i === sel ? ' on' : ''}" data-i="${i}" role="option"><span>${esc(name).replace(new RegExp(`(${reEsc(esc(inp.value.trim()))})`, 'i'), '<mark>$1</mark>')}</span><small>${e.n} paper${e.n > 1 ? 's' : ''} · ${e.y0 === e.y1 ? e.y0 : `${e.y0}–${e.y1}`}</small></button>`).join('')
        : '<div class="ac-none">No author matches in this atlas</div>';
      list.classList.add('open'); inp.setAttribute('aria-expanded', 'true');
    };
    const pick = (i) => { const h = hits[i]; if (!h) return; S.f.au.add(h[0]); inp.value = ''; close(); S.limit = 60; updateLibrary(); syncHash(); };
    inp.addEventListener('input', show);
    inp.addEventListener('focus', show);
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); sel = (sel + (e.key === 'ArrowDown' ? 1 : hits.length - 1)) % Math.max(1, hits.length); $$('.ac-opt', list).forEach((b, i) => b.classList.toggle('on', i === sel)); }
      else if (e.key === 'Enter') { e.preventDefault(); pick(sel); }
      else if (e.key === 'Escape') close();
    });
    list.addEventListener('mousedown', (e) => { const b = e.target.closest('.ac-opt'); if (b) { e.preventDefault(); pick(+b.dataset.i); } });
    inp.addEventListener('blur', () => setTimeout(close, 120));
    $('#auChips').addEventListener('click', (e) => { const b = e.target.closest('[data-au]'); if (b) { S.f.au.delete(b.dataset.au); S.limit = 60; updateLibrary(); syncHash(); } });
    $('#auCard').addEventListener('click', (e) => {
      const c = e.target.closest('[data-coau]');
      if (c) { S.f.au = new Set([c.dataset.coau]); S.limit = 60; updateLibrary(); syncHash(); window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
      if (e.target.closest('[data-auclear]')) { S.f.au.clear(); S.limit = 60; updateLibrary(); syncHash(); }
      const o = e.target.closest('[data-open]'); if (o) openDrawer(o.dataset.open);
    });
    // venue
    $('#vnSel').addEventListener('change', (e) => { const v = e.target.value; S.f.vn = new Set(v ? [v] : []); S.limit = 60; updateLibrary(); syncHash(); });
    $('#pubSeg').addEventListener('click', (e) => {
      const b = e.target.closest('[data-p]'); if (!b) return;
      const venues = [...new Set(P.map((p) => p.vn || 'Preprint'))];
      S.f.vn = new Set(b.dataset.p === 'pub' ? venues.filter((v) => v !== 'Preprint') : b.dataset.p === 'pre' ? ['Preprint'] : []);
      S.limit = 60; updateLibrary(); syncHash();
    });
  }
  function paintYears(a, b) {
    const [lo, hi] = yearBounds(), span = Math.max(1, hi - lo);
    $('#yrFill').style.left = `${((a - lo) / span) * 100}%`; $('#yrFill').style.right = `${((hi - b) / span) * 100}%`;
    $('#yrVal').textContent = a <= lo && b >= hi ? 'All years' : a === b ? String(a) : `${a <= lo ? '≤ ' + lo : a} – ${b}`;
    $$('#yrSpark i').forEach((x) => x.classList.toggle('in', +x.dataset.y >= a && +x.dataset.y <= b || (+x.dataset.y < lo && a <= lo)));
  }
  function renderFilterBar() {
    if (!$('#fbar')) return;
    const [lo, hi] = yearBounds(), [a, b] = curYears();
    // histogram of the current result set without the year filter
    const cnt = new Map(); let mx = 1;
    for (const p of P) { if (!queryOk(p) || !passes(p, 'yr')) continue; const y = Math.max(lo, p.y || lo); cnt.set(y, (cnt.get(y) || 0) + 1); }
    for (const v of cnt.values()) mx = Math.max(mx, v);
    const bars = []; for (let y = lo; y <= hi; y++) { const n = cnt.get(y) || 0; bars.push(`<i data-y="${y}" style="height:${n ? Math.max(6, (n / mx) * 100) : 2}%" data-tip="${y === lo ? '≤ ' : ''}${y}: ${fmt(n)} papers"></i>`); }
    $('#yrSpark').innerHTML = bars.join('');
    $('#yrLo').value = a; $('#yrHi').value = b;
    paintYears(a, b);
    const now = hi;
    const quick = [['All', `${lo}-${hi}`], [`${now - 2}+`, `${now - 2}-${hi}`], [`${now}`, `${now}-${now}`], ['2020–23', '2020-2023'], [`≤ 2019`, `${lo}-2019`]];
    $('#yrQuick').innerHTML = quick.map(([l, v]) => { const [x, z] = v.split('-').map(Number); return `<button class="${x === a && z === b ? 'on' : ''}" data-yr="${v}">${l}</button>`; }).join('');
    // authors
    const idx = authorIndex();
    $('#auHint').textContent = `${fmt(idx.size)} in this atlas`;
    $('#auChips').innerHTML = [...S.f.au].map((n) => `<button class="au-chip" data-au="${esc(n)}" title="Remove">${esc(n)}<i>✕</i></button>`).join('');
    // venue select: top venues by count in the current set (without the venue filter)
    const vc = facetCounts('vn'), top = [...vc.entries()].filter(([v]) => v !== 'Preprint').sort((x, y) => y[1] - x[1]).slice(0, 40);
    const one = S.f.vn.size === 1 ? [...S.f.vn][0] : '';
    $('#vnSel').innerHTML = `<option value="">All venues</option>${S.f.vn.size > 1 ? '<option value="" selected disabled>Several selected</option>' : ''}` + top.map(([v, n]) => `<option value="${esc(v)}"${v === one ? ' selected' : ''}>${esc(v)} (${fmt(n)})</option>`).join('') + `<option value="Preprint"${one === 'Preprint' ? ' selected' : ''}>Preprints (${fmt(vc.get('Preprint') || 0)})</option>`;
    const venues = [...new Set(P.map((p) => p.vn || 'Preprint'))];
    const mode = !S.f.vn.size ? '' : S.f.vn.size === 1 && S.f.vn.has('Preprint') ? 'pre' : (!S.f.vn.has('Preprint') && S.f.vn.size === venues.length - 1) ? 'pub' : 'x';
    $$('#pubSeg button').forEach((x) => x.classList.toggle('on', x.dataset.p === mode));
    renderAuthorCard();
  }
  function renderAuthorCard() {
    const box = $('#auCard');
    if (S.f.au.size !== 1) { box.innerHTML = ''; return; }
    const name = [...S.f.au][0];
    const mine = P.filter((p) => (p.a || []).includes(name));
    if (!mine.length) { box.innerHTML = ''; return; }
    const ys = mine.map((p) => p.y).filter((y) => y > 1900);
    const co = new Map(), vn = new Map(), pd = new Map();
    for (const p of mine) {
      for (const x of p.a || []) if (x !== name) co.set(x, (co.get(x) || 0) + 1);
      if (p.vn) vn.set(p.vn, (vn.get(p.vn) || 0) + 1);
      pd.set(p.pd, (pd.get(p.pd) || 0) + 1);
    }
    const cites = mine.reduce((s, p) => s + (p.c || 0), 0);
    const top = mine.slice().sort((x, y) => (y.c || 0) - (x.c || 0))[0];
    const first = mine.filter((p) => (p.a || [])[0] === name).length;
    const pdBar = PD_ORDER.filter((c) => pd.get(c)).map((c) => `<i style="flex:${pd.get(c)};background:${pdVar(c)}" data-tip="${esc(T.pd[c]?.label || c)}: ${pd.get(c)}"></i>`).join('');
    const yrs = []; for (let y = Math.min(...ys); y <= Math.max(...ys); y++) yrs.push([y, ys.filter((v) => v === y).length]);
    const ym = Math.max(1, ...yrs.map((x) => x[1]));
    const initials = name.split(/\s+/).filter(Boolean).map((w) => w[0]).slice(0, 2).join('').toUpperCase();
    box.innerHTML = `<div class="au-card card">
      <div class="au-avatar" style="--h:${[...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7)}">${esc(initials)}</div>
      <div class="au-main">
        <div class="au-name">${esc(name)} <button class="icon-btn au-x" data-auclear aria-label="Clear author filter">✕</button></div>
        <div class="au-stats"><span><b>${mine.length}</b> paper${mine.length > 1 ? 's' : ''}</span><span><b>${first}</b> as first author</span><span><b>${fmt(cites)}</b> citations</span><span>active <b>${Math.min(...ys)}–${Math.max(...ys)}</b></span></div>
        <div class="au-pd">${pdBar}</div>
        ${top ? `<div class="au-top">Most cited here: <a data-open="${esc(top.id)}" href="#/${esc(A)}/paper?${encodeURIComponent(top.id)}">${esc(top.n || top.t)}</a> <span class="muted">(${fmt(top.c || 0)})</span></div>` : ''}
        ${co.size ? `<div class="au-co"><span class="muted">Frequent co-authors</span>${[...co.entries()].sort((x, y) => y[1] - x[1]).slice(0, 8).map(([x, n]) => `<button class="preset" data-coau="${esc(x)}">${esc(x)} <small>${n}</small></button>`).join('')}</div>` : ''}
        ${vn.size ? `<div class="au-co"><span class="muted">Venues</span>${[...vn.entries()].sort((x, y) => y[1] - x[1]).slice(0, 6).map(([x, n]) => `<span class="chip">${esc(x)} <small>${n}</small></span>`).join('')}</div>` : ''}
      </div>
      <div class="au-years" aria-label="Papers per year">${yrs.map(([y, n]) => `<div><i style="height:${n ? Math.max(8, (n / ym) * 100) : 0}%" data-tip="${y}: ${n}"></i><span>${String(y).slice(2)}</span></div>`).join('')}</div>
    </div>`;
  }

  function renderActive() {
    const chips = [];
    for (const k of FKEYS) {
      for (const v of S.f[k]) {
        if (k === 'sc' && S.f.sc.size === 1 && v === 'core') continue;
        const lab = FBY[k].label ? FBY[k].label(v) : v;
        chips.push(`<button class="af" data-k="${k}" data-v="${esc(v)}"><span>${esc(FBY[k].title)}: ${esc(lab)}</span><i>✕</i></button>`);
      }
    }
    if (S.q) chips.push(`<button class="af" data-k="q"><span>“${esc(S.q)}”</span><i>✕</i></button>`);
    if (chips.length) chips.push('<button class="af reset" data-k="reset">Reset all</button>');
    $('#af').innerHTML = chips.join('');
  }
  function onActiveFilterClick(e) {
    const b = e.target.closest('.af'); if (!b) return;
    const k = b.dataset.k;
    if (k === 'reset') return goLibrary();
    if (k === 'q') { S.q = ''; $('#q').value = ''; QP = parseQuery(''); }
    else S.f[k].delete(b.dataset.v);
    S.limit = 60; updateLibrary(); syncHash();
  }

  function renderFacets(only) {
    const box = $('#facets');
    const html = [];
    for (const f of FACETS) {
      if (only && only !== f.key) { html.push(null); continue; }
      const cnt = facetCounts(f.key);
      const sel = S.f[f.key];
      const collapsed = facetUi.collapsed.has(f.key);
      let body = '';
      if (f.hist) body = yearHist(cnt, sel);
      else {
        let codes = f.order ? f.order() : [...cnt.keys()].sort((a, b) => cnt.get(b) - cnt.get(a));
        if (!f.order) for (const s of sel) if (!codes.includes(s)) codes.push(s);
        const term = (facetUi.search[f.key] || '').toLowerCase();
        if (term) codes = codes.filter((c) => String(f.label(c)).toLowerCase().includes(term));
        let shown = codes;
        const lim = f.top && !facetUi.expanded.has(f.key) && !term ? f.top : Infinity;
        if (codes.length > lim) shown = codes.filter((c, i) => i < lim || sel.has(c));
        if (f.key === 'pd') {
          const grp = (line) => shown.filter((c) => (T.pd[c]?.line || '-') === line);
          const lines = [...new Set(PD_ORDER.map((c) => T.pd[c]?.line || '-'))];
          const lineLab = UIC.paradigm_lines || {};
          body = lines.map((l) => { const g = grp(l); return g.length ? `<div class="sublbl">${esc(lineLab[l] || (l === '-' ? 'No method' : l))}</div>` + g.map((c) => optHtml(f, c, cnt, sel)).join('') : ''; }).join('');
        } else body = shown.map((c) => optHtml(f, c, cnt, sel)).join('');
        if (f.search) body = `<input class="facet-search" data-k="${f.key}" placeholder="Filter ${f.title.toLowerCase()}s…" value="${esc(facetUi.search[f.key] || '')}">` + body;
        if (codes.length > shown.length) body += `<button class="more-btn" data-more="${f.key}">Show all ${codes.length}</button>`;
        else if (f.top && facetUi.expanded.has(f.key)) body += `<button class="more-btn" data-more="${f.key}">Show fewer</button>`;
      }
      html.push(`<div class="facet${collapsed ? ' collapsed' : ''}" data-facet="${f.key}">
        <h4 data-toggle="${f.key}"><span>${f.title}</span>${sel.size ? `<span class="clear" data-clear="${f.key}">clear</span>` : `<span>${collapsed ? '+' : '–'}</span>`}</h4>
        <div class="opts">${body}</div></div>`);
    }
    if (only) {
      const i = FACETS.findIndex((f) => f.key === only);
      const el = $(`[data-facet="${only}"] .opts`, box);
      const tmp = document.createElement('div'); tmp.innerHTML = html[i];
      const inp = $('.facet-search', el); const pos = inp ? inp.selectionStart : null;
      el.replaceWith($('.opts', tmp));
      const ni = $(`[data-facet="${only}"] .facet-search`, box);
      if (ni && pos != null) { ni.focus(); ni.setSelectionRange(pos, pos); }
    } else box.innerHTML = html.join('');
  }
  function optHtml(f, c, cnt, sel) {
    const n = cnt.get(c) || 0, on = sel.has(c);
    const dot = f.dot ? `<span class="dot" style="--c:${f.dot(c)}"></span>` : '';
    const title = f.key === 'tk' ? T.task[c]?.desc : f.key === 'pd' ? T.pd[c]?.desc : f.key === 'sc' ? T.scope[c]?.desc : f.key === 'st' ? T.setting[c]?.desc : '';
    return `<button class="opt${on ? ' on' : ''}${n ? '' : ' zero'}" data-k="${f.key}" data-v="${esc(c)}" title="${esc(title || '')}"><span class="box"></span>${dot}<span class="name">${esc(f.label(c))}</span><span class="n">${fmt(n)}</span></button>`;
  }
  function yearHist(cnt, sel) {
    const lo0 = (META.timeline_start || 2017) - 3;
    const years = [...new Set(P.map((p) => p.y))].filter((y) => y >= lo0).sort();
    const lo = years[0], hi = years[years.length - 1];
    const all = []; for (let y = lo; y <= hi; y++) all.push(String(y));
    const max = Math.max(1, ...all.map((y) => cnt.get(y) || 0));
    const bars = all.map((y) => {
      const n = cnt.get(y) || 0;
      return `<button data-k="yr" data-v="${y}" class="${sel.has(y) ? 'on' : ''}" style="height:${Math.max(3, (n / max) * 100)}%" data-tip="${y}: ${fmt(n)} papers"></button>`;
    }).join('');
    const pre = P.filter((p) => p.y < lo0).length;
    return `<div class="yearhist">${bars}</div><div class="yearaxis"><span>${lo}</span><span>${Math.round((+lo + +hi) / 2)}</span><span>${hi}</span></div>${pre ? `<div class="muted" style="font-size:11px;margin-top:4px">+${pre} papers before ${lo}</div>` : ''}`;
  }
  function onFacetClick(e) {
    const o = e.target.closest('[data-k][data-v]');
    if (o && !o.matches('.facet-search')) {
      const s = S.f[o.dataset.k];
      s.has(o.dataset.v) ? s.delete(o.dataset.v) : s.add(o.dataset.v);
      S.limit = 60; updateLibrary(); syncHash(); return;
    }
    const c = e.target.closest('[data-clear]');
    if (c) { S.f[c.dataset.clear].clear(); S.limit = 60; updateLibrary(); syncHash(); return; }
    const m = e.target.closest('[data-more]');
    if (m) { const k = m.dataset.more; facetUi.expanded.has(k) ? facetUi.expanded.delete(k) : facetUi.expanded.add(k); renderFacets(); return; }
    const t = e.target.closest('[data-toggle]');
    if (t) { const k = t.dataset.toggle; facetUi.collapsed.has(k) ? facetUi.collapsed.delete(k) : facetUi.collapsed.add(k); t.parentElement.classList.toggle('collapsed'); t.lastElementChild.textContent = S.f[k].size ? 'clear' : facetUi.collapsed.has(k) ? '+' : '–'; }
  }

  function hl(text) {
    const s = esc(text);
    const terms = QP.terms.filter((t) => t.length > 1);
    if (!terms.length) return s;
    const re = new RegExp(`(${terms.map((t) => reEsc(esc(t))).join('|')})`, 'gi');
    return s.replace(re, '<mark>$1</mark>');
  }
  function authorsShort(a) {
    if (!a || !a.length) return '';
    return a.length > 4 ? `${a.slice(0, 3).join(', ')}, … ${a[a.length - 1]}` : a.join(', ');
  }
  function venueBadge(p) {
    if (!p.v) return `<span class="venue pre" title="No published venue found yet">${p.ax ? 'arXiv' : 'no venue'}</span>`;
    const cls = p.vk === 'workshop' ? ' ws' : p.vk === 'other' ? ' other' : '';
    const txt = p.v.length > 38 ? p.v.slice(0, 36) + '…' : p.v;
    return `<span class="venue${cls}" title="${esc(p.v)} · source: ${esc(p.vs)}">${esc(txt)}</span>`;
  }
  function coBadge(p) {  // industry authorship (engine/affil.py): Google, Meta, NVIDIA, ByteDance, Alibaba, Xiaomi, ...
    const co = p.co || [];
    if (!co.length) return '';
    const txt = co.length > 2 ? `${co.slice(0, 2).join(' · ')} +${co.length - 2}` : co.join(' · ');
    return `<span class="co-tag" title="Industry authors: ${esc(co.join(', '))}" data-f="co:${esc(co[0])}">${ICON.org}${esc(txt)}</span>`;
  }
  function isNew(p) {
    if (!p.fs || p.fs <= (META.bulk_date || '')) return false;
    return (Date.parse(STATS.built_at) - Date.parse(p.fs)) / 864e5 <= 10;
  }
  function cardHtml(p) {
    const m = MARKS[p.id] || {};
    const tasks = p.tk.map((t) => `<span class="chip task" data-f="tk:${t}">${esc(T.task[t]?.label || t)}</span>`).join('');
    const bms = p._bm.slice(0, 5).map((b) => `<span class="chip bench" data-f="bm:${esc(b)}">${esc(b)}</span>`).join('');
    const more = p._bm.length > 5 ? `<span class="muted" style="font-size:11px">+${p._bm.length - 5}</span>` : '';
    return `<article class="pcard${p.sc !== 'core' ? ' dim' : ''}" style="--c:${pdVar(p.pd)}" data-id="${esc(p.id)}">
      <div class="pc-top">
        ${p.n ? `<span class="pc-name">${hl(p.n)}</span>` : ''}${venueBadge(p)}${coBadge(p)}<span class="pc-year">${esc((p.d || String(p.y)).slice(0, 7))}</span>
        ${p.sc !== 'core' ? `<span class="chip" data-f="sc:${p.sc}">${esc(T.scope[p.sc]?.label || p.sc)}</span>` : ''}
        ${isNew(p) ? '<span class="new-tag" title="Added in a recent update">new</span>' : ''}
        ${p.r ? '<span class="rd-tag" title="Deep-read: motivation, method, insight, results in the paper panel">deep-read</span>' : ''}
        ${m.status ? `<span class="status-tag">${esc(m.status)}</span>` : ''}
        <span class="pc-right">${p.c ? `<span class="pc-cite" title="Citations (OpenAlex)">${ICON.cite}${fmt(p.c)}</span>` : ''}
          <button class="star-btn${m.star ? ' on' : ''}" data-star="${esc(p.id)}" title="Star">${ICON.star}</button></span>
      </div>
      <h3 class="pc-title">${hl(p.t)}</h3>
      <div class="pc-authors">${esc(authorsShort(p.a))}</div>
      ${p.tl ? `<p class="pc-tldr">${hl(p.tl)}</p>` : ''}${(p.sm || p.zh) && !S.dense ? `<p class="pc-zh">${esc(p.sm || p.zh)}</p>` : ''}
      <div class="pc-tags">
        <span class="pd-pill" data-f="pd:${p.pd}"><span class="dot" style="--c:${pdVar(p.pd)}"></span>${esc(T.pd[p.pd]?.label || p.pd)}</span>
        ${tasks ? '<span class="sep"></span>' + tasks : ''}${bms ? '<span class="sep"></span>' + bms + more : ''}
        ${p.cf === 'low' ? '<span class="lowconf" title="Classifier was unsure about scope — worth a look">check</span>' : ''}
      </div>
    </article>`;
  }
  function renderResults(append) {
    const box = $('#results');
    const list = S._list;
    if (!list.length) {
      box.innerHTML = `<div class="empty"><h3>No papers match</h3><p>Try removing a filter or broadening the query.</p></div>`;
      return;
    }
    const start = append ? box.children.length : 0;
    const html = list.slice(start, S.limit).map(cardHtml).join('');
    if (append) box.insertAdjacentHTML('beforeend', html); else box.innerHTML = html;
  }
  function onResultsClick(e) {
    const st = e.target.closest('[data-star]');
    if (st) { e.stopPropagation(); toggleStar(st.dataset.star, st); return; }
    const f = e.target.closest('[data-f]');
    if (f) {
      e.stopPropagation();
      const [k, ...rest] = f.dataset.f.split(':'); const v = rest.join(':');
      if (k === 'sc') S.f.sc.add(v); else S.f[k].add(v);
      S.limit = 60; updateLibrary(); syncHash(); window.scrollTo({ top: 0, behavior: 'smooth' }); return;
    }
    const c = e.target.closest('.pcard');
    if (c) openDrawer(c.dataset.id);
  }

  // ───────── ask (engine/ask.py behind POST api/ask; static copies fall back to the best-matching papers) ─────────
  const ASKS = {};  // per atlas: [{q, res}] for this browser session
  const ASK_STOP = new Set('a an the of in on for to and or with by from at as is are was were be it its this that what which who how why when where do does did can could should would will may than then there their they we our you your about into over between via vs using use used based any all some most more less many each other such not no paper papers work method methods approach approaches model models recent latest best'.split(' '));
  function localMatches(q, k = 12) {
    const terms = [...new Set(q.toLowerCase().split(/[^a-z0-9+.-]+/).filter((t) => t.length > 1 && !ASK_STOP.has(t)))];
    if (!terms.length) return [];
    const out = [];
    for (const p of P) {
      if (p.sc === 'out') continue;
      let s = 0;
      for (const t of terms) { if (p._n && p._n.includes(t)) s += 6; if (p._t.includes(t)) s += 3; if (p._h.includes(t)) s += 1; }
      if (s) out.push([s + Math.log1p(p.c || 0) * 0.3 + (p.sc === 'core' ? 1 : 0), p]);
    }
    return out.sort((a, b) => b[0] - a[0]).slice(0, k).map(([, p]) => ({ id: p.id, n: p.n, t: p.t, y: p.y, v: p.vn }));
  }
  const plab = (p) => (p.n && !p.t.toLowerCase().startsWith(p.n.toLowerCase()) ? `${p.n}: ${p.t}` : p.t);
  function citeLinks(html, order) {  // [2304.03047] → numbered reference chips, in order of first mention
    return html.replace(/\[((?:\d{4}\.\d{4,5})|(?:oa:W\d+))\]/g, (m, id) => {
      const p = BY.get(id);
      if (!p) return m;
      if (!order.has(id)) order.set(id, order.size + 1);
      return `<a class="cite-link" data-open="${esc(id)}" href="#/${esc(A)}/paper?${encodeURIComponent(id)}" title="${esc(p.n ? p.n + ': ' : '')}${esc(p.t)} (${p.y})">${order.get(id)}</a>`;
    }).replace(/<\/a>\s*<a class="cite-link"/g, '</a><a class="cite-link"');
  }
  function askResultHtml({ q, res }) {
    const papers = (res.papers || []).map((x) => {
      const p = BY.get(x.id);
      return `<li><a data-open="${esc(x.id)}" href="#/${esc(A)}/paper?${encodeURIComponent(x.id)}">${esc(p ? plab(p) : x.t)}</a> <span class="muted">${esc(x.v || 'preprint')} ${esc(x.y || '')}</span></li>`;
    }).join('');
    const rows = res.board || [];
    const keys = [...new Set(rows.flatMap((r) => Object.keys(r.m || {})))].slice(0, 5);
    const board = rows.length ? `<details class="ask-board"${res.answer ? '' : ' open'}><summary>Reported results on ${esc(rows[0].bench)} ${esc(rows[0].split || '')} (${rows.length} rows, full splits, as each paper reports them)</summary>
      <div class="g-table"><table class="qtable"><thead><tr><th>Method</th><th>Year</th><th>Paradigm</th>${keys.map((k) => `<th class="num">${esc(k)}</th>`).join('')}</tr></thead><tbody>
      ${rows.map((r) => `<tr><td><a data-open="${esc(r.id)}" href="#/${esc(A)}/paper?${encodeURIComponent(r.id)}">${esc(String(r.method || r.id).replace(/\s*\((ours|Ours|OURS)\)/, ''))}</a>${r.comparable === false ? ' <span class="lowconf" title="protocol deviation found">not comparable</span>' : ''}</td><td>${esc(r.year || '')}</td><td><span class="dot" style="--c:${pdVar(r.pd)}"></span> ${esc(T.pd[r.pd]?.label || r.pd || '')}</td>${keys.map((k) => `<td class="num">${esc((r.m || {})[k] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>` : '';
    const order = new Map();
    const body = res.answer ? `<div class="ask-answer g-body">${citeLinks(renderMd(res.answer).html, order)}</div>`
      : `<p class="muted">${esc(res.error || res.note || (STATIC ? 'This is a static copy of the atlas: answers need a running hub with Claude. The most relevant papers are listed below.' : 'No answer was generated; the most relevant papers are listed below.'))}</p>`;
    return `<div class="card ask-item"><div class="ask-q">${ICON.ask}<b>${esc(q)}</b>${res.model ? `<span class="muted">${esc(res.model)}${res.cached ? ' · cached' : ''}${res.secs != null ? ` · ${res.secs}s` : ''}</span>` : ''}</div>
      ${body}${order.size ? `<ol class="ask-refs">${[...order.keys()].map((id) => { const p = BY.get(id); return `<li value="${order.get(id)}"><a data-open="${esc(id)}" href="#/${esc(A)}/paper?${encodeURIComponent(id)}">${esc(plab(p))}</a> <span class="muted">${esc(p.vn || 'preprint')} ${esc(p.y)}</span></li>`; }).join('')}</ol>` : ''}${board}${papers ? `<details class="ask-papers"${res.answer ? '' : ' open'}><summary>${res.answer ? 'Papers given to the model' : 'Most relevant papers'} (${(res.papers || []).length})</summary><ol>${papers}</ol></details>` : ''}</div>`;
  }
  function renderAsk() {
    const hist = ASKS[A] || (ASKS[A] = []);
    const ex = UIC.ask_examples || ['What changed in this field after 2023?', 'What are the main open problems?', 'Which papers introduced the most-used benchmarks?'];
    const mode = STATIC ? 'Static copy: questions are matched to papers in your browser.'
      : CAN_ASK ? 'Answers come from Claude on the hub machine, using only this atlas (titles, abstracts, deep-reading notes and reported results), and cite papers you can open.'
      : 'Answers need Claude on the hub machine (unlock editing, or start the hub with ATLAS_ASK_OPEN=1). Until then you get the most relevant papers.';
    $('#app').innerHTML = `
      <div class="page-head"><div><h1>Ask the atlas</h1><p>${esc(mode)}</p></div></div>
      <form class="ask-form" id="askForm"><div class="searchbar">${ICON.ask.replace('<svg ', '<svg class="s-ico" ')}<input id="askQ" autocomplete="off" maxlength="500" placeholder="Ask anything about ${esc(META.field || META.title || 'this field')}…"></div>
        <button class="btn primary" type="submit" id="askGo">Ask</button></form>
      <div class="presets"><span class="lbl">Try</span>${ex.map((x) => `<button class="preset" data-ex="${esc(x)}">${esc(x)}</button>`).join('')}</div>
      <div id="askOut">${hist.slice().reverse().map(askResultHtml).join('')}</div>`;
    const form = $('#askForm'), inp = $('#askQ');
    inp.focus();
    $('#app').onclick = (e) => {
      const o = e.target.closest('[data-open]');
      if (o) { e.preventDefault(); openDrawer(o.dataset.open); return; }
      const x = e.target.closest('[data-ex]');
      if (x) { inp.value = x.dataset.ex; form.requestSubmit(); }
    };
    form.onsubmit = async (e) => {
      e.preventDefault();
      const q = inp.value.trim();
      if (q.length < 3) return;
      const btn = $('#askGo'); btn.disabled = true;
      const wait = document.createElement('div');
      wait.className = 'card ask-item'; wait.innerHTML = `<div class="ask-q">${ICON.ask}<b>${esc(q)}</b></div><div class="loading" style="min-height:0;padding:14px 0"><div class="spinner"></div><span>${STATIC || !CAN_ASK ? 'Searching…' : 'Reading the atlas… (usually 10–40 s)'}</span></div>`;
      $('#askOut').prepend(wait);
      let res;
      if (STATIC) res = { papers: localMatches(q) };
      else {
        try {
          const r = await fetch('api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ atlas: A, q }) });
          res = await r.json();
          if (!r.ok) res = { papers: localMatches(q), error: res.error || `error ${r.status}` };
        } catch (err) { res = { papers: localMatches(q), error: 'The hub did not answer; showing papers matched in the browser.' }; }
      }
      hist.push({ q, res });
      wait.outerHTML = askResultHtml({ q, res });
      btn.disabled = false; inp.select();
      history.replaceState(null, '', `#/${A}/ask?q=${encodeURIComponent(q)}`);  // shareable link to this question
    };
    const q0 = new URLSearchParams(location.hash.split('?')[1] || '').get('q');
    if (q0 && !hist.some((x) => x.q === q0)) { inp.value = q0; form.requestSubmit(); }
  }

  // ───────── marks ─────────
  async function saveMark(uid, patch) {
    const m = { ...(MARKS[uid] || {}), ...patch };
    if (!m.star && !m.status && !m.note) delete MARKS[uid]; else MARKS[uid] = m;
    if (MARKS_REMOTE) {
      try {
        const r = await fetch(`api/marks?atlas=${A}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid, ...patch }) });
        if (!r.ok) throw 0;
        return true;
      } catch (_) { toast('Could not save to server'); return false; }
    }
    try { localStorage.setItem(`atlas-marks-${A}`, JSON.stringify(MARKS)); } catch (_) {}
    return true;
  }
  function toggleStar(uid, btn) {
    const on = !MARKS[uid]?.star;
    saveMark(uid, { star: on });
    $$(`[data-star="${CSS.escape(uid)}"]`).forEach((b) => b.classList.toggle('on', on));
    if (btn) btn.classList.toggle('on', on);
  }

  // ───────── drawer ─────────
  function linkBtn(href, label) { return `<a class="btn" href="${esc(href)}" target="_blank" rel="noopener">${esc(label)} ${ICON.ext}</a>`; }
  function openDrawer(id, fromRoute) {
    const p = BY.get(id); if (!p) return;
    const m = MARKS[id] || {};
    const tq = encodeURIComponent(p.t);
    const links = [
      p.ax && linkBtn(`https://arxiv.org/abs/${p.ax}`, 'arXiv'),
      p.ax && linkBtn(`https://arxiv.org/pdf/${p.ax}`, 'PDF'),
      p.doi && !/arxiv/i.test(p.doi) && linkBtn(`https://doi.org/${p.doi}`, 'DOI'),
      linkBtn(p.ax ? `https://www.semanticscholar.org/arxiv/${p.ax}` : `https://www.semanticscholar.org/search?q=${tq}`, 'Semantic Scholar'),
      linkBtn(`https://scholar.google.com/scholar?q=${tq}`, 'Google Scholar'),
      linkBtn(`https://dblp.org/search?q=${tq}`, 'DBLP'),
      linkBtn(`https://github.com/search?q=${encodeURIComponent(p.n || p.t)}&type=repositories`, 'Code?'),
    ].filter(Boolean).join('');
    const chips = (arr, k, lab) => arr.length ? arr.map((c) => `<span class="chip${k === 'tk' ? ' task' : k === 'bm' ? ' bench' : ''}" data-go="${k}:${esc(c)}">${esc(lab ? lab(c) : c)}</span>`).join('') : '<span class="muted">—</span>';
    const vsName = { 'semantic-scholar': 'Semantic Scholar', 'arxiv-comment': 'the arXiv comment', openalex: 'OpenAlex', dblp: 'DBLP' }[p.vs] || p.vs;
    const bibSrc = {
      dblp: 'Official BibTeX from DBLP.',
      crossref: `Official publisher BibTeX via CrossRef (DOI ${p.doi}).`,
      'auto-venue': `Published-venue entry generated with a canonical booktitle — venue from ${vsName}.`,
      arxiv: 'arXiv preprint — no published venue found yet (re-checked on every update).',
      misc: 'Generated from OpenAlex metadata.',
    }[p.bs] || '';
    $('#drawerBody').innerHTML = `
      <div class="d-kicker">${p.n ? `<span class="pc-name">${esc(p.n)}</span>` : ''}${venueBadge(p)}${coBadge(p)}<span class="pc-year">${esc(p.d || p.y)}</span>
        ${p.c ? `<span class="pc-cite muted">${ICON.cite} ${fmt(p.c)} citations</span>` : ''}</div>
      <h2 class="d-title" id="dTitle">${esc(p.t)}</h2>
      <div class="d-authors">${(p.a || []).map((n) => `<a class="au-link" href="#/${esc(A)}/library?au=${encodeURIComponent(n)}" title="All papers by ${esc(n)} in this atlas">${esc(n)}</a>`).join(', ')}</div>
      <div class="d-links">${links}</div>
      ${p.tl ? `<p class="d-tldr" style="--c:${pdVar(p.pd)}"><b>TL;DR</b> — ${esc(p.tl)}</p>` : ''}
      <div class="d-sec"><h3>My marks</h3>
        <div class="marks-row">
          <button class="btn" id="dStar">${m.star ? '★ Starred' : '☆ Star'}</button>
          <div class="seg" id="dStatus">${['', 'queued', 'reading', 'read', 'skip'].map((s) => `<button data-s="${s}" class="${(m.status || '') === s ? 'on' : ''}">${s || 'unread'}</button>`).join('')}</div>
          <span class="saved" id="dSaved">saved</span>
        </div>
        <textarea class="note" id="dNote" placeholder="Notes (motivation, method, what to cite it for…) — auto-saved${MARKS_REMOTE ? ' & shared' : ' locally'}">${esc(m.note || '')}</textarea>
      </div>
      <div class="d-sec"><h3>Classification</h3>
        <dl class="kv">
          <dt>Scope</dt><dd>${chips([p.sc], 'sc', (c) => T.scope[c]?.label || c)}${p.cf === 'low' ? '<span class="lowconf">low confidence</span>' : ''}</dd>
          <dt>Paradigm</dt><dd><span class="pd-pill" data-go="pd:${p.pd}"><span class="dot" style="--c:${pdVar(p.pd)}"></span>${esc(T.pd[p.pd]?.label || p.pd)}</span></dd>
          <dt>Tasks</dt><dd>${chips(p.tk, 'tk', (c) => T.task[c]?.label || c)}</dd>
          <dt>Settings</dt><dd>${chips(p.st, 'st', (c) => T.setting[c]?.label || c)}</dd>
          <dt>Benchmarks</dt><dd>${chips(p._bm, 'bm')}</dd>
          <dt>Contribution</dt><dd>${chips(p.ct, 'ct', (c) => T.contrib[c]?.label || c)}</dd>
          <dt>Techniques</dt><dd>${chips(p.tr, 'tr')}</dd>
        </dl>
      </div>
      <div class="d-sec"><h3>Abstract</h3><p class="d-abs">${esc(p.ab || 'No abstract available.')}</p></div>
      ${readingHtml(p)}
      <div class="d-sec"><h3>BibTeX</h3>
        <div class="bib"><pre id="dBib">${esc(p.bib)}</pre><button class="btn" id="dCopyBib">${ICON.copy} Copy</button></div>
        <div class="bib-src">${esc(bibSrc)}${p.cm ? ` · arXiv comment: <i>${esc(p.cm)}</i>` : ''}</div>
      </div>
      <div class="d-sec"><h3>Provenance</h3>
        <p class="muted" style="font-size:12.5px">id <span class="mono">${esc(p.id)}</span> · sources ${esc((p.src || []).join(' + '))} · matched ${p.q.length} quer${p.q.length === 1 ? 'y' : 'ies'}: <span class="mono">${esc(p.q.join(', '))}</span></p>
      </div>`;
    const d = $('#drawer');
    d.classList.add('open'); d.setAttribute('aria-hidden', 'false');
    $('.drawer-panel').scrollTop = 0;
    document.body.style.overflow = 'hidden';
    S._drawer = id;
    $('#dCopyBib').onclick = () => copy(p.bib, 'BibTeX copied');
    loadReading(p);
    $('#dStar').onclick = (e) => { toggleStar(id); e.target.textContent = MARKS[id]?.star ? '★ Starred' : '☆ Star'; flashSaved(); };
    $('#dStatus').onclick = (e) => {
      const b = e.target.closest('button'); if (!b) return;
      $$('#dStatus button').forEach((x) => x.classList.toggle('on', x === b));
      saveMark(id, { status: b.dataset.s }).then(flashSaved);
      const card = $(`.pcard[data-id="${CSS.escape(id)}"]`); if (card) card.outerHTML = cardHtml(p);
    };
    $('#dNote').oninput = debounce((e) => saveMark(id, { note: e.target.value }).then(flashSaved), 600);
    $('#drawerBody').onclick = (e) => {
      const g = e.target.closest('[data-go]'); if (!g) return;
      const [k, ...rest] = g.dataset.go.split(':');
      goLibrary({ [k]: [rest.join(':')], sc: k === 'sc' ? [rest.join(':')] : DEFAULT_SC });
    };
    if (!fromRoute) history.pushState({ drawer: id }, '', `#/${A}/paper?${encodeURIComponent(id)}`);
  }
  async function loadReading(p) {
    const box = $('#rdBox'); if (!box || !p.r) return;
    try {
      const r = await fetch(`a/${A}/reading/${encodeURIComponent(p.id.replace(/[:/]/g, '_'))}.json`).then((x) => x.json());
      if ($('#rdBox')) $('#rdBox').outerHTML = readingFull(r);
    } catch (_) { box.innerHTML = '<div class="pending">Could not load the reading notes.</div>'; }
  }
  function readingFull(r) {
    const li = (v) => (Array.isArray(v) ? `<ul class="rd-list">${v.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : `<p class="d-abs">${esc(v)}</p>`);
    const sec = (t, v) => (v && (!Array.isArray(v) || v.length) ? `<div class="rd-sec"><h4>${t}</h4>${li(v)}</div>` : '');
    const st = r.setting || {};
    const known = { learned: 1, backbone: 1, backbone_open: 1, model_size: 1, training_data: 1, observation: 1, action_space: 1, simulator: 1, real_robot: 1, privileged: 1 };
    const label = (k) => k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
    const txt = (v) => (v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v));
    const kv = [['Learned', st.learned === true ? 'yes — trained' : st.learned === false ? 'no — training-free' : '—'], ['Backbone', st.backbone], ['Open weights', st.backbone_open == null ? '—' : st.backbone_open ? 'yes' : 'no'], ['Size', st.model_size], ['Training data', st.training_data], ['Observation', st.observation], ['Action space', st.action_space], ['Simulator', st.simulator], ['Real robot', 'real_robot' in st ? (st.real_robot ? 'yes' : 'no') : ''], ['Privileged info', st.privileged]]
      .concat(Object.keys(st).filter((k) => !known[k]).map((k) => [label(k), txt(st[k])]))  // atlas-specific setting fields (the atlas's read_system.md)
      .filter(([, v]) => v).map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join('');
    const own = (Array.isArray(r.own_tasks) ? r.own_tasks : []).filter((n) => n && typeof n === 'object').map((n) => `<tr><td>${esc(n.setup || '')}${n.trials ? `<br><span class="muted">${esc(n.trials)}</span>` : ''}</td>
        <td>${esc(n.method || '')}</td>
        <td>${Object.entries(n.metrics || {}).map(([k, v]) => `<b>${esc(k)}</b>&nbsp;${esc(v)}`).join(' · ')}${n.best_baseline ? `<br><span class="muted">best baseline: ${esc(n.best_baseline)}</span>` : ''}</td>
        <td title="${esc(n.evidence || '')}">${n.verified === true ? '<span class="ok-tag">✓ in text</span>' : n.verified === false ? '<span class="lowconf">check</span>' : ''}</td></tr>`).join('');
    const res = r.resource && typeof r.resource === 'object' ? Object.entries(r.resource).filter(([, v]) => v).map(([k, v]) => `<dt>${esc(label(k))}</dt><dd>${esc(txt(v))}</dd>`).join('') : '';
    const nums = (r.numbers || []).map((n) => `<tr><td><span class="mono">${esc(n.bench)}</span><br><span class="muted">${esc(n.split || '')}${n.eval_set === 'subset' ? ` · <b class="subset" title="${esc(n.eval_set_note || '')}">subset</b>` : ''}</span></td>
        <td>${esc(n.method || '')}<br><span class="muted">${esc(n.backbone || '')}${n.zero_shot ? ' · zero-shot' : ''}</span></td>
        <td>${Object.entries(n.metrics || {}).map(([k, v]) => `<b>${esc(k)}</b>&nbsp;${esc(v)}`).join(' · ')}${n.extra ? `<br><span class="muted">${esc(n.extra)}</span>` : ''}</td>
        <td title="${esc(n.evidence || '')}">${n.verified === true ? '<span class="ok-tag">✓ in text</span>' : n.verified === false ? '<span class="lowconf">check</span>' : ''}</td></tr>`).join('');
    const v = r.verify || {};
    return `<div class="d-sec" id="rdBox"><h3>Deep reading <span class="badge done" style="position:static">${esc(r.model || '')} · ${esc((r.read_at || '').slice(0, 10))} · ${esc(r.source || '')}</span>${r.shared_from ? ` <span class="badge next" style="position:static" title="Read once in another atlas that also contains this paper, and shared here">shared from ${esc((ATLASES.find((x) => x.id === r.shared_from) || {}).title || r.shared_from)}</span>` : ''}</h3>
      ${r.summary || r.summary_zh ? `<p class="d-tldr zh">${esc(r.summary || r.summary_zh)}</p>` : ''}
      ${sec('Problem', r.problem)}${sec('Motivation', r.motivation)}${sec('Key idea', r.key_idea)}${sec('Method', r.method)}${sec('Insight', r.insight)}
      ${kv ? `<div class="rd-sec"><h4>Setting</h4><dl class="kv">${kv}</dl></div>` : ''}
      ${sec('Results', r.results)}
      ${nums ? `<div class="rd-sec"><h4>Reported numbers <span class="muted" style="font-weight:400">— ${v.verified ?? 0}/${v.metrics ?? 0} numbers found verbatim in the paper text; hover ✓ for the source</span></h4><div class="g-table"><table class="qtable"><thead><tr><th>Benchmark</th><th>Method / backbone</th><th>Metrics</th><th></th></tr></thead><tbody>${nums}</tbody></table></div>${r.numbers_note ? `<p class="muted" style="font-size:12.5px">${esc(r.numbers_note)}</p>` : ''}</div>` : ''}
      ${own ? `<div class="rd-sec"><h4>Own task suites <span class="muted" style="font-weight:400">— real-robot or paper-specific evaluations, not comparable across papers</span></h4><div class="g-table"><table class="qtable"><thead><tr><th>Setup</th><th>Method</th><th>Metrics</th><th></th></tr></thead><tbody>${own}</tbody></table></div></div>` : ''}
      ${res ? `<div class="rd-sec"><h4>Resource</h4><dl class="kv">${res}</dl></div>` : ''}
      ${sec('Limitations', r.limitations)}
      ${(r.builds_on || []).length || (r.compares_to || []).length ? `<div class="rd-sec"><h4>Lineage</h4><p class="d-abs">${(r.builds_on || []).length ? `<b>builds on</b> ${esc(r.builds_on.join(', '))}` : ''}${(r.compares_to || []).length ? `<br><b>compared with</b> ${esc(r.compares_to.join(', '))}` : ''}</p></div>` : ''}
    </div>`;
  }
  function readingHtml(p) {
    if (p.r) return '<div class="d-sec" id="rdBox"><h3>Deep reading</h3><div class="pending">Loading the notes…</div></div>';
    const r = p.rd;
    if (!r) return `<div class="d-sec"><h3>Deep reading <span class="badge next" style="position:static">stage 2</span></h3>
        <div class="pending"><b>Motivation · Method · Key results · Limitations</b> — not read yet. The stage-2 reading agent fills this from the full text (atlases/${esc(A)}/data/reading/), together with the benchmark numbers for the comparison tables.</div></div>`;
    const list = (v) => Array.isArray(v) ? `<ul class="rd-list">${v.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : `<p class="d-abs">${esc(v)}</p>`;
    const sec = (t, v) => v && (!Array.isArray(v) || v.length) ? `<div class="rd-sec"><h4>${t}</h4>${list(v)}</div>` : '';
    const nums = (r.numbers || []).map((n) => `<tr><td class="mono">${esc(n.bench)}</td><td>${esc(n.split || '')}</td><td>${Object.entries(n.metrics || {}).map(([k, v]) => `<b>${esc(k)}</b> ${esc(v)}`).join(' · ')}</td></tr>`).join('');
    return `<div class="d-sec"><h3>Deep reading <span class="badge done" style="position:static">read${r.read_at ? ' ' + esc(r.read_at.slice(0, 10)) : ''}</span></h3>
      ${sec('Motivation', r.motivation)}${sec('Method', r.method)}${sec('Key results', r.results)}${sec('Limitations', r.limitations)}
      ${nums ? `<div class="rd-sec"><h4>Reported numbers</h4><table class="qtable"><tbody>${nums}</tbody></table></div>` : ''}</div>`;
  }
  function flashSaved() { const s = $('#dSaved'); if (!s) return; s.classList.add('show'); clearTimeout(s._t); s._t = setTimeout(() => s.classList.remove('show'), 1200); }
  function closeDrawer(silent) {
    const d = $('#drawer');
    if (!d.classList.contains('open')) return;
    d.classList.remove('open'); d.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    S._drawer = null;
    if (!silent && location.hash.includes('/paper?')) history.back();
  }
  $('#drawer').addEventListener('click', (e) => { if (e.target.closest('[data-close]')) closeDrawer(); });

  // ───────── export ─────────
  function download(text, name, type) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  function exportCsv(list) {
    const cols = [['id', (p) => p.id], ['name', (p) => p.n || ''], ['title', (p) => p.t], ['authors', (p) => (p.a || []).join('; ')], ['year', (p) => p.y],
      ['venue', (p) => p.vn || 'Preprint'], ['citations', (p) => p.c || 0], ['scope', (p) => p.sc], ['paradigm', (p) => T.pd[p.pd]?.label || p.pd],
      ['tasks', (p) => (p.tk || []).join('; ')], ['benchmarks', (p) => (p._bm || []).join('; ')], ['tldr', (p) => p.tl || ''],
      ['url', (p) => (p.doi ? `https://doi.org/${p.doi}` : /^\d{4}\.\d{4,5}$/.test(p.id) ? `https://arxiv.org/abs/${p.id}` : '')], ['bibkey', (p) => p.key || '']];
    const cell = (v) => { const s = String(v ?? ''); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    download('\ufeff' + [cols.map(([h]) => h).join(','), ...list.map((p) => cols.map(([, f]) => cell(f(p))).join(','))].join('\n') + '\n', `${A}-${list.length}.csv`, 'text/csv');
    toast(`Exported ${fmt(list.length)} papers as CSV`);
  }
  function exportBib(list) {
    const blob = new Blob([list.map((p) => p.bib).join('\n\n') + '\n'], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = `${A}-${list.length}.bib`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    toast(`Exported ${fmt(list.length)} BibTeX entries`);
  }
  function copy(text, msg) {
    const done = () => toast(msg || 'Copied');
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, () => fallback());
    else fallback();
    function fallback() { const t = document.createElement('textarea'); t.value = text; document.body.appendChild(t); t.select(); try { document.execCommand('copy'); done(); } catch (_) {} t.remove(); }
  }
  function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove('show'), 1800); }

  // ───────── shared chart bits ─────────
  const tip = $('#tip');
  function showTip(html, x, y) {
    tip.innerHTML = html; tip.classList.add('show');
    const r = tip.getBoundingClientRect();
    let left = x + 14, top = y + 14;
    if (left + r.width > innerWidth - 8) left = x - r.width - 14;
    if (top + r.height > innerHeight - 8) top = y - r.height - 14;
    tip.style.left = `${Math.max(8, left)}px`; tip.style.top = `${Math.max(8, top)}px`;
  }
  const hideTip = () => tip.classList.remove('show');
  document.addEventListener('mousemove', (e) => {
    const t = e.target.closest && e.target.closest('[data-tip]');
    if (t) showTip(t.dataset.tip, e.clientX, e.clientY);
    else if (!e.target.closest || !e.target.closest('[data-tiphtml]')) hideTip();
  });
  function scopeSeg(id, cur) {
    return `<div class="seg" id="${id}"><button data-v="core" class="${cur === 'core' ? 'on' : ''}">Core</button><button data-v="core+adj" class="${cur !== 'core' ? 'on' : ''}">Core + adjacent</button></div>`;
  }
  const inScope = (p, sc) => p.sc === 'core' || (sc !== 'core' && p.sc === 'adjacent');
  const UI = { mapScope: 'core', mapEra: 'all', tlScope: 'core', tlMode: 'count', tlHidden: new Set() };

  // ───────── map ─────────
  function renderMap() {
    const split = UIC.era_split || 2023;
    const list = P.filter((p) => inScope(p, UI.mapScope) && (UI.mapEra === 'all' || (UI.mapEra === 'pre' ? p.y < split : p.y >= split)));
    const pds = PD_ORDER;
    const count = (tk, pd) => list.filter((p) => p.tk.includes(tk) && p.pd === pd).length;
    let max = 1;
    const rows = TASK_GROUPS.map(([g, tks]) => [g, tks.map((tk) => { const r = pds.map((pd) => count(tk, pd)); max = Math.max(max, ...r); return [tk, r]; })]);
    const scFilter = UI.mapScope === 'core' ? ['core'] : ['core', 'adjacent'];
    const yrFilter = UI.mapEra === 'all' ? [] : [...new Set(list.map((p) => String(p.y)))];
    const colTot = pds.map((pd) => list.filter((p) => p.pd === pd).length);
    const table = `<table class="matrix"><thead><tr><th></th>${pds.map((pd) => `<th><span class="line">${esc(T.pd[pd]?.line || '')}</span><span class="dot" style="--c:${pdVar(pd)}"></span>${esc(T.pd[pd]?.label || pd)}</th>`).join('')}<th class="tot">All</th></tr></thead><tbody>
      ${rows.map(([g, rs]) => `<tr class="grp"><th colspan="${pds.length + 2}">${esc(g)}</th></tr>` + rs.map(([tk, r]) => {
        const tot = list.filter((p) => p.tk.includes(tk)).length;
        return `<tr><th title="${esc(T.task[tk]?.desc)}">${esc(T.task[tk]?.label || tk)}</th>${r.map((n, i) => {
          const a = n ? 0.07 + 0.93 * Math.sqrt(n / max) : 0;
          return `<td class="cell${n ? '' : ' zero'}${a > 0.6 ? ' hot' : ''}" style="--a:${a.toFixed(3)}" data-go="${ga({ tk: [tk], pd: [pds[i]], sc: scFilter, yr: yrFilter })}" data-tip="${esc(T.task[tk]?.label)} × ${esc(T.pd[pds[i]]?.label)}: ${n} papers">${n || '·'}</td>`;
        }).join('')}<td class="tot">${fmt(tot)}</td></tr>`;
      }).join('')).join('')}
      <tr><th class="tot" style="text-align:left">All tasks</th>${colTot.map((n) => `<td class="tot">${fmt(n)}</td>`).join('')}<td class="tot">${fmt(list.length)}</td></tr>
      </tbody></table>`;

    // task × year
    const years = yearSpan();
    let ymax = 1;
    const yrows = TASK_GROUPS.flatMap(([, tks]) => tks).map((tk) => {
      const r = years.map((y, i) => list.filter((p) => p.tk.includes(tk) && (i === 0 ? p.y <= y : p.y === y)).length);
      ymax = Math.max(ymax, ...r); return [tk, r];
    });
    const ytable = `<table class="matrix"><thead><tr><th></th>${years.map((y, i) => `<th>${i === 0 ? '≤' : ''}${y}</th>`).join('')}</tr></thead><tbody>
      ${yrows.map(([tk, r]) => `<tr><th>${esc(T.task[tk]?.label || tk)}</th>${r.map((n, i) => {
        const a = n ? 0.07 + 0.93 * Math.sqrt(n / ymax) : 0;
        const yrs = i === 0 ? [...new Set(P.filter((p) => p.y <= years[0]).map((p) => String(p.y)))] : [String(years[i])];
        return `<td class="cell${n ? '' : ' zero'}${a > 0.6 ? ' hot' : ''}" style="--a:${a.toFixed(3)}" data-go="${ga({ tk: [tk], yr: yrs, sc: scFilter })}" data-tip="${esc(T.task[tk]?.label)} · ${i === 0 ? '≤' : ''}${years[i]}: ${n} papers">${n || '·'}</td>`;
      }).join('')}</tr>`).join('')}</tbody></table>`;

    const pdCards = TAX.paradigms.map((pd) => `<div class="card tax-card"><h4><span class="dot" style="--c:${pdVar(pd.code)}"></span>${esc(pd.label)}<span class="n">${fmt(P.filter((p) => p.sc === 'core' && p.pd === pd.code).length)}</span></h4><p>${esc(pd.desc)}</p></div>`).join('');
    const tkCards = TAX.tasks.map((t) => `<div class="card tax-card"><h4>${esc(t.label)} <span class="mono muted">${esc(t.code)}</span><span class="n">${fmt(P.filter((p) => p.sc === 'core' && p.tk.includes(t.code)).length)}</span></h4><p>${esc(t.desc)}</p></div>`).join('');
    const scCards = TAX.scopes.map((t) => `<div class="card tax-card"><h4>${esc(t.label)}<span class="n">${fmt(t.code === 'out' ? (STATS.funnel.scope_out || 0) : P.filter((p) => p.sc === t.code).length)}</span></h4><p>${esc(t.desc)}</p></div>`).join('');

    $('#app').innerHTML = `
      <div class="page-head"><div><h1>Research map</h1><p>Where the literature sits: <b>task</b> (rows) against <b>method paradigm</b> (columns). Dense cells are mature lines; empty cells are open questions. Click any cell to open those papers.</p></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">${scopeSeg('mapScope', UI.mapScope)}
        <div class="seg" id="mapEra"><button data-v="all" class="${UI.mapEra === 'all' ? 'on' : ''}">All years</button><button data-v="pre" class="${UI.mapEra === 'pre' ? 'on' : ''}">≤ ${split - 1}</button><button data-v="post" class="${UI.mapEra === 'post' ? 'on' : ''}">${split} +</button></div></div></div>
      <div class="card"><div class="matrix-wrap">${table}</div>
        <div style="display:flex;justify-content:flex-end;padding:0 16px 12px"><span class="legend-ramp">fewer<span class="ramp"></span>more papers</span></div></div>
      <div class="section"><h2>Task × year</h2><p class="sub">How each task family grew. ${builtYear()} covers January–${esc(new Date(STATS.built_at).toLocaleString('en-US', { month: 'long' }))}.</p>
        <div class="card"><div class="matrix-wrap">${ytable}</div></div></div>
      <div class="section"><h2>Paradigms</h2><p class="sub">The survey's time axis. ${esc(UIC.paradigm_note || '')}</p><div class="tax-grid">${pdCards}</div></div>
      <div class="section"><h2>Tasks</h2><p class="sub">Multi-label: a generalist paper counts in every task it evaluates.</p><div class="tax-grid">${tkCards}</div></div>
      <div class="section"><h2>Scope</h2><p class="sub">What gets into the library at all.</p><div class="tax-grid">${scCards}</div></div>`;
    $('#app').onclick = (e) => {
      const c = e.target.closest('[data-go]'); if (c) { hideTip(); return goLibrary(JSON.parse(c.dataset.go)); }
      const s = e.target.closest('#mapScope button'); if (s) { UI.mapScope = s.dataset.v; return renderMap(); }
      const r = e.target.closest('#mapEra button'); if (r) { UI.mapEra = r.dataset.v; return renderMap(); }
    };
  }

  // ───────── timeline ─────────
  function renderTimeline() {
    const list = P.filter((p) => inScope(p, UI.tlScope));
    const years = yearSpan();
    const pds = PD_ORDER.filter((pd) => !UI.tlHidden.has(pd));
    const data = years.map((y, i) => {
      const ps = list.filter((p) => (i === 0 ? p.y <= y : p.y === y));
      const by = Object.fromEntries(PD_ORDER.map((pd) => [pd, ps.filter((p) => p.pd === pd).length]));
      return { y, by, tot: pds.reduce((s, pd) => s + by[pd], 0) };
    });
    const share = UI.tlMode === 'share';
    const W = 1100, H = 380, ml = 44, mr = 16, mt = 14, mb = 30;
    const iw = W - ml - mr, ih = H - mt - mb;
    const ymax = share ? 1 : niceMax(Math.max(1, ...data.map((d) => d.tot)));
    const bw = iw / years.length, gap = Math.min(14, bw * 0.28);
    const yS = (v) => mt + ih - (v / ymax) * ih;
    const ticks = share ? [0, .25, .5, .75, 1] : niceTicks(ymax);
    let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Papers per year by paradigm">`;
    for (const t of ticks) svg += `<line class="grid" x1="${ml}" x2="${W - mr}" y1="${yS(t)}" y2="${yS(t)}"/><text x="${ml - 8}" y="${yS(t) + 4}" text-anchor="end">${share ? Math.round(t * 100) + '%' : fmt(t)}</text>`;
    data.forEach((d, i) => {
      const x = ml + i * bw + gap / 2, w = bw - gap;
      let acc = 0;
      const tipRows = PD_ORDER.filter((pd) => !UI.tlHidden.has(pd)).slice().reverse().map((pd) => `<div class="tt-r"><span class="dot" style="--c:${pdVar(pd)}"></span>${esc(T.pd[pd]?.label)}<b>${d.by[pd]}${share && d.tot ? ` · ${Math.round(d.by[pd] / d.tot * 100)}%` : ''}</b></div>`).join('');
      const tipH = `<div class="tt-h">${i === 0 ? '≤ ' : ''}${d.y} — ${fmt(d.tot)} papers</div>${tipRows}`;
      for (const pd of pds) {
        const v = share ? (d.tot ? d.by[pd] / d.tot : 0) : d.by[pd];
        if (!v) continue;
        const y0 = yS(acc), y1 = yS(acc + v);
        const hgt = Math.max(0, y0 - y1 - 1);
        svg += `<rect class="seg" x="${x}" y="${y1}" width="${w}" height="${hgt}" rx="${hgt > 6 ? 3 : 1}" fill="${pdVar(pd)}" data-tip="${esc(tipH)}" data-go="${ga({ pd: [pd], yr: i === 0 ? [...new Set(P.filter((p) => p.y <= d.y).map((p) => String(p.y)))] : [String(d.y)], sc: UI.tlScope === 'core' ? ['core'] : ['core', 'adjacent'] })}"/>`;
        acc += v;
      }
      if (!share && d.tot) svg += `<text class="dl" x="${x + w / 2}" y="${yS(d.tot) - 6}" text-anchor="middle">${fmt(d.tot)}</text>`;
      svg += `<text x="${x + w / 2}" y="${H - 10}" text-anchor="middle">${i === 0 ? '≤' : ''}${d.y}${d.y === builtYear() ? '*' : ''}</text>`;
    });
    svg += `<line class="axis" x1="${ml}" x2="${W - mr}" y1="${yS(0)}" y2="${yS(0)}"/></svg>`;
    const legend = PD_ORDER.map((pd) => `<span data-pd="${pd}" class="${UI.tlHidden.has(pd) ? 'off' : ''}"><i class="sw" style="--c:${pdVar(pd)}"></i>${esc(T.pd[pd]?.label)}</span>`).join('');

    // small multiples per task
    const tasks = TAX.tasks.map((t) => t.code);
    const smalls = tasks.map((tk) => {
      const counts = years.map((y, i) => list.filter((p) => p.tk.includes(tk) && (i === 0 ? p.y <= y : p.y === y)).length);
      const tot = list.filter((p) => p.tk.includes(tk)).length;
      if (!tot) return '';
      const m = Math.max(1, ...counts), sw = 220, sh = 54, bw2 = sw / counts.length;
      const bars = counts.map((n, i) => { const h = (n / m) * (sh - 12); return `<rect x="${i * bw2 + 1.5}" y="${sh - 12 - h}" width="${bw2 - 3}" height="${Math.max(n ? 1.5 : 0, h)}" rx="1.5" data-tip="${esc(T.task[tk]?.label)} · ${i === 0 ? '≤' : ''}${years[i]}: ${n}"/>`; }).join('');
      return `<div class="card small" data-go="${ga({ tk: [tk], sc: UI.tlScope === 'core' ? ['core'] : ['core', 'adjacent'] })}"><h4>${esc(T.task[tk]?.label)}<span>${fmt(tot)}</span></h4>
        <svg viewBox="0 0 ${sw} ${sh}" style="width:100%;height:auto">${bars}<text x="0" y="${sh - 1}">${years[0]}</text><text x="${sw}" y="${sh - 1}" text-anchor="end">${years[years.length - 1]}</text></svg></div>`;
    }).join('');

    // landmarks: most-cited core papers per year
    const lmYears = years.slice().reverse();
    const landmarks = lmYears.map((y) => {
      const ps = P.filter((p) => p.sc === 'core' && p.y === y && p.pd !== 'none' || (p.sc === 'core' && p.y === y && p.ct.includes('benchmark')));
      const top = ps.slice().sort((a, b) => (b.c || 0) - (a.c || 0)).slice(0, 7);
      if (!top.length) return '';
      return `<div class="card lm-year"><h4>${y}<span>${fmt(P.filter((p) => p.sc === 'core' && p.y === y).length)} core papers</span></h4>
        ${top.map((p) => `<div class="lm-item" data-open="${esc(p.id)}"><span class="dot" style="--c:${pdVar(p.pd)}"></span><span class="t" title="${esc(p.t)}">${p.n ? `<b>${esc(p.n)}</b> — ` : ''}${esc(p.t)}</span><span class="c">${fmt(p.c)}</span></div>`).join('')}</div>`;
    }).join('');

    $('#app').innerHTML = `
      <div class="page-head"><div><h1>Timeline</h1><p>${esc(UIC.timeline_intro || 'How the field moved between method paradigms over time.')} Click a bar segment to open those papers.</p></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">${scopeSeg('tlScope', UI.tlScope)}<div class="seg" id="tlMode"><button data-v="count" class="${!share ? 'on' : ''}">Count</button><button data-v="share" class="${share ? 'on' : ''}">Share</button></div></div></div>
      <div class="card chart-card"><div class="chart-head"><h3>Papers per year, by paradigm</h3><span class="muted" style="font-size:12px">* ${builtYear()} = year to date · first bar pools everything up to ${years[0]}</span></div>
        <div class="legend" id="tlLegend">${legend}</div>${svg}</div>
      <div class="section"><h2>Task trends</h2><p class="sub">Papers per year for each task family (same years as above; each panel on its own scale).</p><div class="smalls">${smalls}</div></div>
      <div class="section"><h2>Landmarks by year</h2><p class="sub">Most-cited core papers of each year (Semantic Scholar / OpenAlex citation counts; recent years are naturally low). A starting list for the survey's “milestones” figure.</p><div class="landmarks">${landmarks}</div></div>`;
    $('#app').onclick = (e) => {
      const g = e.target.closest('[data-go]'); if (g) { hideTip(); return goLibrary(JSON.parse(g.dataset.go)); }
      const o = e.target.closest('[data-open]'); if (o) return openDrawer(o.dataset.open);
      const s = e.target.closest('#tlScope button'); if (s) { UI.tlScope = s.dataset.v; return renderTimeline(); }
      const m = e.target.closest('#tlMode button'); if (m) { UI.tlMode = m.dataset.v; return renderTimeline(); }
      const l = e.target.closest('#tlLegend [data-pd]'); if (l) { const pd = l.dataset.pd; UI.tlHidden.has(pd) ? UI.tlHidden.delete(pd) : UI.tlHidden.add(pd); if (UI.tlHidden.size === PD_ORDER.length) UI.tlHidden.clear(); return renderTimeline(); }
    };
  }
  function niceStep(raw) { const p = Math.pow(10, Math.floor(Math.log10(raw))); const n = raw / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p; }
  function niceMax(v) { const st = niceStep(v / 5); return Math.ceil(v / st) * st; }
  function niceTicks(max) { const st = niceStep(max / 5); const out = []; for (let t = 0; t <= max + 1e-9; t += st) out.push(Math.round(t)); return out; }

  // ───────── benchmarks ─────────
  async function renderBenchmarks() {
    const list = P.filter((p) => p.sc === 'core' || p.sc === 'adjacent');
    const years = yearSpan();
    const cnt = new Map();
    for (const p of list) for (const b of p._bm) cnt.set(b, (cnt.get(b) || 0) + 1);
    const known = new Set(Object.values(TAX.benchmarks).flat());
    const groups = Object.entries(TAX.benchmarks).map(([g, names]) => [g, names]);
    const others = [...cnt.entries()].filter(([b, n]) => !known.has(b) && n >= 3).sort((a, b) => b[1] - a[1]).map(([b]) => b);
    groups.push(['Other benchmarks (≥ 3 papers)', others]);
    const row = (b) => {
      const ps = list.filter((p) => p._bm.includes(b));
      const n = ps.length;
      const c = years.map((y, i) => ps.filter((p) => (i === 0 ? p.y <= y : p.y === y)).length);
      const m = Math.max(1, ...c), bw = 100 / c.length;
      const spark = `<svg class="spark" viewBox="0 0 100 24" preserveAspectRatio="none">${c.map((v, i) => `<rect x="${i * bw + .6}" y="${24 - (v / m) * 22}" width="${bw - 1.2}" height="${(v / m) * 22}"/>`).join('')}</svg>`;
      return `<div class="bm-row" data-go="${ga({ bm: [b], sc: ['core', 'adjacent'] })}" data-tip="${esc(esc(b))}: ${n} papers · first ${ps.length ? Math.min(...ps.map((p) => p.y)) : '–'}"><span class="bm-name">${esc(b)}</span>${spark}<span class="bm-n">${n}</span></div>`;
    };
    const cards = groups.map(([g, names]) => {
      const rows = names.filter((b) => cnt.get(b)).sort((a, b) => cnt.get(b) - cnt.get(a));
      if (!rows.length) return '';
      return `<div class="card bm-group"><h3>${esc(g)}<span>${rows.length} benchmarks</span></h3>${rows.map(row).join('')}</div>`;
    }).join('');
    const board = await getBoard();
    $('#app').innerHTML = `
      <div class="page-head"><div><h1>Benchmarks</h1><p>Which benchmarks the literature evaluates on, as stated in abstracts. Bars show papers per year ${years[0]}→${years[years.length - 1]}. Click a benchmark to list its papers.</p></div></div>
      <div class="section" style="margin-top:0"><h2>Results leaderboard</h2><p class="sub">Every result the deep reading extracted (${fmt(board.length)} rows so far), with what you need to compare fairly: full split vs subset, zero-shot vs trained, backbone and open / closed weights, privileged information. Click a column to re-rank; click a row to open the paper.</p>
        <div class="card" style="padding:14px 16px" id="lb">${leaderboardHtml(board)}</div></div>
      <div class="section"><h2>Who evaluates on what</h2><p class="sub">Benchmarks named in abstracts, papers per year.</p>
      <div class="bm-grid">${cards}</div></div>`;
    bindLeaderboard(board, () => { $('#lb').innerHTML = leaderboardHtml(board); });
    $('#app').onclick = (e) => { if (e.target.closest('#lb')) return; const g = e.target.closest('[data-go]'); if (g) { hideTip(); goLibrary(JSON.parse(g.dataset.go)); } };
  }


  // ───────── lazily loaded per-atlas data ─────────
  async function getBoard() { const c = CACHE[A]; if (!c.board) c.board = await fetch(`a/${A}/leaderboard.json`).then((r) => (r.ok ? r.json() : [])).catch(() => []); return c.board; }
  async function getSurveys() { const c = CACHE[A]; if (!c.surveys) c.surveys = await fetch(`a/${A}/surveys.json`).then((r) => (r.ok ? r.json() : [])).catch(() => []); return c.surveys; }

  // ───────── results leaderboard (from deep reading) ─────────
  const METRIC_ORDER = ['SR', 'SPL', 'NE', 'OSR', 'nDTW', 'SDTW', 'CLS', 'RGS', 'RGSPL', 'GP', 'DTG', 'SoftSPL', 'PR', 'TL'];
  const LOWER_BETTER = new Set(['NE', 'DTG', 'TL']);
  const LB = { bench: '', split: '', set: 'all', zs: 'all', open: 'all', ok: false, std: false, pd: new Set(), sort: '', dir: -1 };
  // same rule as engine/survey.py: only settings that break comparability (extra training data is not a flag)
  const NONSTD = /beam|pre-?explor|ensemble|test[- ]time aug|ground[- ]truth|\bGT\b|oracle|privileged|pre-?built map|prior map|known map|teleport|full observ/i;
  const flagged = (r) => ('audit_std' in r ? !r.audit_std : 'cmp' in r ? !r.cmp : !!((r.priv && !/^none/i.test(r.priv) && NONSTD.test(r.priv)) || NONSTD.test(r.extra || '')));
  function leaderboardHtml(board) {
    if (!board.length) return `<div class="callout">${ICON.info}<div><b>Results leaderboard.</b> Filled by deep reading (<span class="mono">./atlas read ${esc(A)}</span>): every paper's reported numbers with split, subset / full set, backbone and privileged information. None read yet.</div></div>`;
    const benches = Object.entries(board.reduce((m, r) => ((m[r.bench] = (m[r.bench] || 0) + 1), m), {})).sort((a, b) => b[1] - a[1]);
    const qb = decodeURIComponent((location.hash.split('?')[1] || '').replace(/^bench=/, ''));  // #/<atlas>/benchmarks?bench=IR2R-CE
    if (qb && qb !== LB._q && benches.some(([b]) => b === qb)) { LB.bench = qb; LB.split = ''; LB._q = qb; }
    if (!LB.bench || !benches.some(([b]) => b === LB.bench)) { LB.bench = benches[0][0]; LB.split = ''; }
    const rowsB = board.filter((r) => r.bench === LB.bench);
    const splits = Object.entries(rowsB.reduce((m, r) => ((m[r.split] = (m[r.split] || 0) + 1), m), {})).sort((a, b) => b[1] - a[1]).map(([x]) => x);
    if (!LB.split || !splits.includes(LB.split)) LB.split = splits.includes('val-unseen') ? 'val-unseen' : splits[0];
    let rows = rowsB.filter((r) => r.split === LB.split);
    const allN = rows.length;
    if (LB.set !== 'all') rows = rows.filter((r) => (r.eval_set || 'full') === LB.set);
    if (LB.zs !== 'all') rows = rows.filter((r) => !!r.zs === (LB.zs === 'zs'));
    if (LB.open !== 'all') rows = rows.filter((r) => r.open === (LB.open === 'open'));
    if (LB.ok) rows = rows.filter((r) => r.ok === true);
    if (LB.std) rows = rows.filter((r) => !flagged(r));
    if (LB.pd.size) rows = rows.filter((r) => LB.pd.has(r.pd));
    const mcount = {};
    rowsB.filter((r) => r.split === LB.split).forEach((r) => Object.keys(r.m).forEach((k) => (mcount[k] = (mcount[k] || 0) + 1)));
    const metrics = Object.keys(mcount).filter((k) => mcount[k] >= Math.max(1, allN * 0.25)).sort((a, b) => ((METRIC_ORDER.indexOf(a) + 1 || 99) - (METRIC_ORDER.indexOf(b) + 1 || 99)) || mcount[b] - mcount[a]).slice(0, 7);
    const key = metrics.includes(LB.sort) ? LB.sort : (metrics.includes('SR') ? 'SR' : metrics[0]);
    const dir = LB.sort === key ? LB.dir : (LOWER_BETTER.has(key) ? 1 : -1);
    const num = (v) => (typeof v === 'number' ? v : parseFloat(v));
    rows.sort((a, b) => { const x = num(a.m[key]), y = num(b.m[key]); if (isNaN(x)) return 1; if (isNaN(y)) return -1; return (x - y) * dir; });
    const opt = (grp, val, lab, cur) => `<button data-lb="${grp}" data-v="${val}" class="${cur === val ? 'on' : ''}">${lab}</button>`;
    const pdChips = PD_ORDER.map((pd) => `<button class="pd-pill${LB.pd.has(pd) ? ' on' : ''}" data-lbpd="${pd}"><span class="dot" style="--c:${pdVar(pd)}"></span>${esc(T.pd[pd]?.label || pd)}</button>`).join('');
    return `
      <div class="lb-controls">
        <label>Benchmark <select class="select" id="lbBench">${benches.filter(([, n]) => n >= 1).slice(0, 80).map(([b, n]) => `<option value="${esc(b)}" ${b === LB.bench ? 'selected' : ''}>${esc(b)} (${n})</option>`).join('')}</select></label>
        <label>Split <select class="select" id="lbSplit">${splits.map((x) => `<option ${x === LB.split ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select></label>
        <div class="seg">${opt('set', 'all', 'All sets', LB.set)}${opt('set', 'full', 'Full split', LB.set)}${opt('set', 'subset', 'Subset', LB.set)}</div>
        <div class="seg">${opt('zs', 'all', 'All', LB.zs)}${opt('zs', 'zs', 'Zero-shot', LB.zs)}${opt('zs', 'trained', 'Trained on it', LB.zs)}</div>
        <div class="seg">${opt('open', 'all', 'Any model', LB.open)}${opt('open', 'open', 'Open weights', LB.open)}${opt('open', 'closed', 'Closed (GPT…)', LB.open)}</div>
        <label class="btn" title="Hide rows with privileged info or extra conditions (beam search, pre-exploration, GT maps, ensembles…)"><input type="checkbox" id="lbStd" ${LB.std ? 'checked' : ''} style="margin:0 4px 0 0">standard setting only (no ⚑)</label>
        <label class="btn"><input type="checkbox" id="lbOk" ${LB.ok ? 'checked' : ''} style="margin:0 4px 0 0">verified only</label>
      </div>
      <div class="lb-pd">${pdChips}</div>
      <p class="muted" style="font-size:12.5px;margin:4px 0 10px">${fmt(rows.length)} of ${fmt(allN)} results on <b>${esc(LB.bench)}</b> · ${esc(LB.split)}, sorted by <b>${esc(key)}</b> ${dir < 0 ? '↓' : '↑'}. <b class="subset">subset</b> = evaluated on part of the split (hover for which); ⚑ = privileged info / extra conditions (hover); ✓ = every number found verbatim in the paper. Numbers and flags are extracted by a language model and can be wrong: open the paper to see the quoted evidence.</p>
      <div class="g-table lb-table"><table class="qtable"><thead><tr><th>#</th><th>Method</th><th>Paradigm</th><th>Backbone</th><th>Set</th>${metrics.map((m) => `<th class="num sortable${m === key ? ' on' : ''}" data-sort="${esc(m)}">${esc(m)}${m === key ? (dir < 0 ? ' ↓' : ' ↑') : ''}</th>`).join('')}<th></th></tr></thead>
      <tbody>${rows.map((r, i) => `<tr data-open="${esc(r.id)}">
        <td class="num">${i + 1}</td>
        <td><b>${esc(r.n || r.t.slice(0, 40))}</b>${r.method && r.method !== r.n ? `<br><span class="muted">${esc(r.method.slice(0, 60))}</span>` : ''}<br><span class="muted">${esc(r.y)} · ${esc(r.v || 'arXiv')}</span>${coBadge(r)}</td>
        <td><span class="dot" style="--c:${pdVar(r.pd)}"></span> ${esc(T.pd[r.pd]?.label || r.pd)}${r.zs ? '<br><span class="muted">zero-shot</span>' : ''}</td>
        <td>${esc((r.backbone || '').slice(0, 48))}${r.open === true ? ' <span class="ok-tag">open</span>' : r.open === false ? ' <span class="chip">closed</span>' : ''}</td>
        <td>${r.eval_set === 'subset' ? `<b class="subset" title="${esc(r.eval_note)}">subset</b>` : 'full'}</td>
        ${metrics.map((m) => `<td class="num${m === key ? ' key' : ''}">${r.m[m] ?? ''}</td>`).join('')}
        <td>${flagged(r) ? `<span class="flag" title="${esc([r.priv && !/^none/i.test(r.priv) ? 'Privileged: ' + r.priv : '', r.extra ? 'Extra: ' + r.extra : ''].filter(Boolean).join('\n'))}">⚑</span>` : ''}${r.ok ? ' <span class="ok-tag" title="' + esc(r.ev || '') + '">✓</span>' : ''}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function bindLeaderboard(board, rerender) {
    const box = $('#lb'); if (!box) return;
    box.onchange = (e) => {
      if (e.target.id === 'lbBench') { LB.bench = e.target.value; LB.split = ''; LB.sort = ''; }
      if (e.target.id === 'lbSplit') LB.split = e.target.value;
      if (e.target.id === 'lbOk') LB.ok = e.target.checked;
      if (e.target.id === 'lbStd') LB.std = e.target.checked;
      rerender();
    };
    box.onclick = (e) => {
      const b = e.target.closest('[data-lb]'); if (b) { LB[b.dataset.lb] = b.dataset.v; return rerender(); }
      const pd = e.target.closest('[data-lbpd]'); if (pd) { const k = pd.dataset.lbpd; LB.pd.has(k) ? LB.pd.delete(k) : LB.pd.add(k); return rerender(); }
      const th = e.target.closest('[data-sort]'); if (th) { const k = th.dataset.sort; if (LB.sort === k) LB.dir = -LB.dir; else { LB.sort = k; LB.dir = LOWER_BETTER.has(k) ? 1 : -1; } return rerender(); }
      const tr = e.target.closest('[data-open]'); if (tr) openDrawer(tr.dataset.open);
    };
  }

  // ───────── survey projects ─────────
  function svLeaves(nodes) { return nodes.flatMap((n) => ('def' in n ? [n] : []).concat(svLeaves(n.children || []))); }
  function svMatch(p, spec) { return Object.entries(spec || {}).every(([k, vals]) => !vals || !vals.length || (FBY[k] ? FBY[k].get(p) : []).some((v) => vals.includes(v))); }
  const SV = { open: new Set(['I', 'II', 'II.1', 'II.2', 'II.3', 'II.3.A', 'II.3.B']), leaf: null };
  async function renderSurvey(sid) {
    $('#app').onclick = null;
    $('#app').innerHTML = '<div class="loading"><div class="spinner"></div><span>Loading the survey…</span></div>';
    const list = await getSurveys();
    const sv = list.find((x) => x.id === sid);
    if (!sv) { $('#app').innerHTML = `<div class="empty"><h3>No survey “${esc(sid)}”</h3><p>Surveys are defined in <span class="mono">atlases/${esc(A)}/atlas.py</span> (SURVEYS).</p></div>`; return; }
    document.title = `${sv.short || sv.title} · ${META.title || A}`;
    const inScope = P.filter((p) => svMatch(p, sv.scope));
    const asg = sv.assign || {};
    const byLeaf = {};
    Object.entries(asg).forEach(([id, a]) => { const p = BY.get(id); if (p) (byLeaf[a.leaf] = byLeaf[a.leaf] || []).push([p, a]); });
    const nAssigned = Object.keys(asg).length, nRead = inScope.filter((p) => p.r).length;
    const secs = sv.files?.sections || {};
    const written = Object.entries(secs).filter(([k, n]) => n > 400 && !k.startsWith('_') && !k.startsWith('IV_')).map(([k]) => k);
    const order = sv.files?.order || [];  // [file stem, title, depth] of every body file, in reading order
    const units = order.length ? order.map((o) => o[0]) : ['abstract', 'intro', ...sv.outline.flatMap((t) => (t.id === 'II' ? (t.children || []).map((c) => c.id) : [t.id])), 'conclusion'];
    const leafDone = (id) => secs[id] > 400;
    const count = (n) => ('def' in n ? (byLeaf[n.id] || []).length : 0) + (n.children || []).reduce((s, c) => s + count(c), 0);
    const readIn = (n) => ('def' in n ? (byLeaf[n.id] || []).filter(([p]) => p.r).length : 0) + (n.children || []).reduce((s, c) => s + readIn(c), 0);
    const years = []; for (let y = (META.timeline_start || 2017); y <= builtYear(); y++) years.push(y);
    const strip = (ps) => { const c = years.map((y, i) => ps.filter(([p]) => (i === 0 ? p.y <= y : p.y === y)).length), m = Math.max(1, ...c); return `<svg class="sv-strip" viewBox="0 0 100 18" preserveAspectRatio="none">${c.map((v, i) => `<rect x="${i * (100 / c.length) + 0.5}" y="${18 - (v / m) * 17}" width="${100 / c.length - 1}" height="${(v / m) * 17}"/>`).join('')}</svg>`; };
    const roleRank = { landmark: 0, representative: 1, incremental: 2 };
    const node = (n, depth) => {
      const isLeaf = 'def' in n, kids = n.children || [], open = SV.open.has(n.id) || SV.leaf === n.id;
      const c = count(n), rd = readIn(n);
      const unit = units.includes(n.id) ? (leafDone(n.id) ? '<span class="ok-tag">drafted</span>' : '<span class="chip">to write</span>') : '';
      let body = '';
      if (open && isLeaf) {
        const ps = (byLeaf[n.id] || []).slice().sort((a, b) => a[0].y - b[0].y || roleRank[a[1].role] - roleRank[b[1].role]);
        body = `<p class="sv-def">${esc(n.def)}</p>${ps.length ? `<div class="sv-papers">${ps.map(([p, a]) => `<div class="sv-paper role-${a.role}" data-open="${esc(p.id)}"><span class="sv-y">${p.y}</span><span class="dot" style="--c:${pdVar(p.pd)}"></span><span class="sv-t"><b>${esc(p.n || '')}</b> ${esc(p.t)}</span>${a.role !== 'incremental' ? `<span class="sv-role">${a.role}</span>` : ''}${p.r ? '<span class="rd-tag">deep-read</span>' : ''}</div>`).join('')}</div>` : '<p class="muted">No papers assigned yet.</p>'}`;
      } else if (open && n.synth) {
        body = `<p class="sv-def">${n.synth === 'threads' ? 'Written from the problem threads below, each traced across the outline.' : n.synth === 'leaderboard' ? 'Written from the results leaderboard (Benchmarks page), grouped by paradigm and evaluation protocol.' : 'Synthesis section.'}</p>`;
      }
      return `<div class="sv-node d${depth}${isLeaf ? ' leaf' : ''}${open ? ' open' : ''}">
        <div class="sv-head" data-node="${esc(n.id)}"><span class="sv-caret">${isLeaf || kids.length || n.synth ? (open ? '▾' : '▸') : ''}</span><span class="sv-id">${esc(n.id)}</span><span class="sv-title">${esc(n.title)}</span>${n.era ? `<span class="chip">${esc(n.era)}</span>` : ''}${unit}
          <span class="sv-right">${c ? `${strip(isLeaf ? byLeaf[n.id] || [] : svLeaves([n]).flatMap((l) => byLeaf[l.id] || []))}<span class="sv-n">${fmt(c)}</span><span class="muted sv-rd">${rd}/${c} read</span>` : ''}</span></div>
        ${n.transition && open ? `<p class="sv-trans">→ ${esc(n.transition)}</p>` : ''}
        ${body}${open ? kids.map((k) => node(k, depth + 1)).join('') : ''}</div>`;
    };
    const tcount = Object.fromEntries((sv.threads || []).map((t) => [t[0], Object.values(asg).filter((a) => (a.threads || []).includes(t[0])).length]));
    const base = `a/${A}/survey/${sv.id}/`;
    $('#app').innerHTML = `
      <div class="page-head"><div><div class="sv-crumb"><a href="#/${esc(A)}/surveys">Surveys</a> ›</div><h1>${esc(sv.title)}</h1>
        <p>Outline-driven workspace. Every leaf of the outline receives its papers (assigned by Claude from the leaf definitions) and lists them chronologically; landmark and representative papers are the ones the text discusses. Synthesis nodes are written from the leaves, the problem threads and the results tables.</p></div></div>
      <div class="sv-stats">
        <div><b>${fmt(inScope.length)}</b><span>papers in scope</span></div>
        <div><b>${fmt(nAssigned)}</b><span>placed in the outline</span></div>
        <div><b>${fmt(nRead)}</b><span>deep-read</span></div>
        <div><b>${written.length}/${units.length}</b><span>sections drafted</span></div>
        <div class="sv-files">${(sv.files?.pdfs || []).length ? sv.files.pdfs.map((v) => `<a class="btn primary" href="${base}${esc(v.file)}" target="_blank" title="${esc(v.note || '')}${v.mtime ? ' · built ' + esc(v.mtime.slice(0, 16).replace('T', ' ')) : ''}">${ICON.doc || ''}${esc(v.label)} (PDF)</a>`).join('') : '<span class="btn" title="Not compiled yet">PDF —</span>'}<a class="btn" href="${base}main.tex" target="_blank">main.tex</a><a class="btn" href="${base}refs.bib" target="_blank">refs.bib</a></div>
      </div>
      ${nAssigned < inScope.length ? `<div class="callout">${ICON.info}<div>${fmt(inScope.length - nAssigned)} papers are not placed in the outline yet — <span class="mono">./atlas survey ${esc(A)} ${esc(sv.id)} assign</span> (runs after deep reading so placement uses the notes).</div></div>` : ''}
      <div class="sv-grid">
        <div class="card sv-tree">${sv.outline.map((n) => node(n, 0)).join('')}</div>
        <aside class="sv-side">
          <div class="card" style="padding:14px 16px"><h3 class="sv-h">Problem threads</h3>${(sv.threads || []).map((t) => `<div class="sv-thread"><b>${esc(t[1])}</b><span class="muted">${fmt(tcount[t[0]] || 0)} papers</span><p>${esc(t[2])}</p></div>`).join('')}</div>
          <div class="card" style="padding:14px 16px"><h3 class="sv-h">LaTeX sections</h3>${units.map((u, i) => `<div class="sv-sec" style="padding-left:${(order[i]?.[2] || 0) * 12}px" title="${esc(order[i]?.[1] || u)}"><span class="mono">${esc(u)}.tex</span>${secs[u] > 400 ? `<span class="ok-tag">${fmt(Math.round(secs[u] / 1000))}k chars</span>` : '<span class="muted">empty</span>'}<a href="${base}sections/${encodeURIComponent(u)}.tex" target="_blank">view</a></div>`).join('')}
            <p class="muted" style="font-size:12px;margin:8px 0 0">Source: <span class="mono">atlases/${esc(A)}/surveys/${esc(sv.id)}/</span></p></div>
        </aside>
      </div>`;
    $('#app').onclick = (e) => {
      const h = e.target.closest('[data-node]');
      if (h) { const id = h.dataset.node; SV.open.has(id) ? SV.open.delete(id) : SV.open.add(id); const y = window.scrollY; renderSurvey(sid).then(() => window.scrollTo(0, y)); return; }
      const o = e.target.closest('[data-open]'); if (o) openDrawer(o.dataset.open);
    };
  }

  // ───────── surveys ─────────
  function matchSpec(p, spec) {
    for (const [k, vals] of Object.entries(spec)) {
      if (!vals || !vals.length) continue;
      const got = FBY[k].get(p);
      if (!got.some((v) => vals.includes(v))) return false;
    }
    return true;
  }
  async function renderSurveys() {
    const projects = await getSurveys();
    const SURVEYS = UIC.surveys || [];
    const blocks = SURVEYS.map((sv) => {
      const base = P.filter((p) => matchSpec(p, sv.base));
      const secs = sv.sections.map(([t, sub, spec]) => {
        const full = { ...sv.base, ...spec };
        return { t, sub, full, n: P.filter((p) => matchSpec(p, full)).length };
      });
      const max = Math.max(1, ...secs.map((s) => s.n));
      return `<div class="card survey"><div class="survey-head"><div><h3>${esc(sv.title)}</h3><p>${esc(sv.blurb)}</p></div>
          <div class="big"><b>${fmt(base.length)}</b><span>papers in scope</span></div></div>
        <div class="outline">${secs.map((s, i) => `<div class="ol-row" data-go="${ga(s.full)}"><span class="num">§${i + 1}</span><span class="ttl">${esc(s.t)}${s.sub ? `<small>${esc(s.sub)}</small>` : ''}</span><span class="meter"><i style="width:${(s.n / max) * 100}%"></i></span><span class="cnt">${fmt(s.n)}</span></div>`).join('')}</div></div>`;
    }).join('');
    const stages = UIC.roadmap || [
      ['01', 'Collect & classify', 'Harvest, prefilter, LLM labels, venues + BibTeX.', 'done'],
      ['02', 'Deep reading', 'Per-paper agent reads the full text: motivation, method, results, limitations.', 'now'],
      ['03', 'Benchmark tables', 'Extract reported numbers into leaderboards.', 'next'],
      ['04', 'Write', 'Draft the surveys from the outlines, with reading notes as evidence.', 'next'],
    ];
    $('#app').innerHTML = `
      <div class="page-head"><div><h1>Survey workbench</h1><p>Candidate surveys, each an outline whose sections are live queries over the library. Counts update as papers are added; click a section to open its papers.</p></div></div>
      <div class="section" style="margin-top:0"><h2>Roadmap</h2><p class="sub">The system is built in stages; this page tracks where we are.</p>
        <div class="roadmap">${stages.map(([n, t, d, s]) => `<div class="card stage"><span class="st-n">${n}</span><span class="badge ${s}">${s === 'done' ? 'done' : s === 'now' ? 'next up' : 'planned'}</span><h4>${esc(t)}</h4><p>${esc(d)}</p></div>`).join('')}</div></div>
      ${projects.length ? `<div class="section" style="margin-top:0"><h2>Survey projects</h2><p class="sub">Each has its own page: outline tree, papers per section, deep-reading progress and the LaTeX / PDF draft.</p>
        <div class="sv-projects">${projects.map((sv) => { const n = P.filter((p) => svMatch(p, sv.scope)).length; const secs = Object.values(sv.files?.sections || {}).filter((x) => x > 400).length; const pdfs = (sv.files?.pdfs || []).map((v) => `<a class="btn primary sv-pdf" href="a/${esc(A)}/survey/${esc(sv.id)}/${esc(v.file)}" target="_blank" title="${esc(v.note || '')}">${esc(v.label)} · PDF</a>`).join(''); return `<div class="card sv-proj"><a class="sv-proj-link" href="#/${esc(A)}/survey/${esc(sv.id)}"><h3>${esc(sv.title)}</h3><div class="sv-proj-stats"><span><b>${fmt(n)}</b> papers</span><span><b>${fmt(Object.keys(sv.assign || {}).length)}</b> placed</span><span><b>${fmt(P.filter((p) => p.r && svMatch(p, sv.scope)).length)}</b> read</span><span><b>${secs}</b> sections drafted</span></div></a>${pdfs ? `<div class="sv-proj-pdfs">${pdfs}</div>` : ''}<a class="gb-go" href="#/${esc(A)}/survey/${esc(sv.id)}">Open workspace →</a></div>`; }).join('')}</div></div>` : ''}
      <div class="section"><h2>Candidate surveys</h2><p class="sub">${SURVEYS.length ? `${SURVEYS.length} cut${SURVEYS.length > 1 ? 's' : ''} over the same corpus, each defined in <span class="mono">atlases/${esc(A)}/atlas.py</span> (UI.surveys).` : `No outlines yet — add them to UI.surveys in <span class="mono">atlases/${esc(A)}/atlas.py</span>.`}</p>${blocks}</div>`;
    $('#app').onclick = (e) => { const g = e.target.closest('[data-go]'); if (g) goLibrary(JSON.parse(g.dataset.go)); };
  }

  // ───────── about / pipeline ─────────
  async function renderAbout() {
    const f = STATS.funnel;
    const labelled = TAX.scopes.reduce((n, x) => n + (f['scope_' + x.code] || 0), 0);
    const rows = [
      ['Harvested (unique)', f.harvested, 'var(--ink-2)'],
      ['Passed rule prefilter', f.harvested - (f.prefilter_drop || 0), 'var(--muted)'],
      ['Labelled by classifier', labelled + (f.duplicate || 0), 'var(--accent)'],
      ...TAX.scopes.map((x, i) => [`→ ${x.label}`, f['scope_' + x.code] || 0, x.code === 'out' ? 'var(--cat-none)' : `var(--cat-${i + 1})`]),
    ];
    if (f.duplicate) rows.splice(3, 0, ['Duplicates merged', f.duplicate, 'var(--muted)']);
    if (f.unlabelled) rows.splice(3, 0, ['Awaiting labels', f.unlabelled, 'var(--warn)']);
    const mx = Math.max(...rows.map((r) => r[1]));
    const funnel = rows.map(([l, n, c]) => `<div class="fn-row"><span>${esc(l)}</span><span class="bar"><i style="width:${(n / mx) * 100}%;--c:${c}"></i></span><b>${fmt(n)}</b></div>`).join('');
    const q = STATS.queries.slice().sort((a, b) => (a.src === b.src ? b.core - a.core : a.src.localeCompare(b.src)));
    const qt = `<table class="qtable"><thead><tr><th>Source</th><th>Key</th><th>Query</th><th class="num">Hits</th><th class="num">Core</th></tr></thead><tbody>${q.map((r) => `<tr><td>${r.src}</td><td class="mono">${esc(r.key)}</td><td><code>${esc(r.query)}</code></td><td class="num">${fmt(r.hits)}</td><td class="num">${fmt(r.core)}</td></tr>`).join('')}</tbody></table>`;
    const venues = P.filter((p) => p.sc === 'core');
    const nv = venues.filter((p) => p.v).length, nd = venues.filter((p) => p.bs === 'dblp' || p.bs === 'crossref').length, na = venues.filter((p) => p.bs === 'auto-venue').length;
    $('#app').innerHTML = `
      <div class="page-head"><div><h1>Pipeline</h1><p>How this atlas is built and kept fresh: the field lives in <span class="mono">atlases/${esc(A)}/atlas.py</span>, the pipeline in <span class="mono">engine/</span>. Built ${esc(STATS.built_at.replace('T', ' ').slice(0, 16))} UTC.</p></div></div>
      <div class="two-col">
        <div class="card" style="padding:18px 20px"><h3 style="margin:0 0 12px;font:600 17px var(--serif)">Funnel</h3><div class="funnel">${funnel}</div>
          <p class="muted" style="font-size:12.5px;margin:14px 0 0">Core papers with a known venue: <b>${fmt(nv)}</b> / ${fmt(venues.length)} · official publisher BibTeX: <b>${fmt(nd)}</b> · canonical venue BibTeX: <b>${fmt(na)}</b></p></div>
        <div class="card" style="padding:18px 20px"><h3 style="margin:0 0 12px;font:600 17px var(--serif)">Stages</h3>
          <ol style="margin:0;padding-left:18px;color:var(--ink-2);font-size:13.5px;line-height:1.7">
            <li><b>Harvest</b> — ${STATS.queries.filter((x) => x.src === 'arxiv').length} arXiv API queries (recall-first: field phrases, benchmark and task names, method families × field, a title safety net) + OpenAlex title/abstract search for venue-only papers + hand-picked seeds.</li>
            <li><b>Merge & prefilter</b> — dedupe by arXiv id and normalised title; drop rows with no field term, non-CS categories, too old, or weak OpenAlex-only matches.</li>
            <li><b>Classify</b> — Claude (Sonnet, headless; Opus second opinion on low-confidence borderline papers) labels scope, tasks, settings, paradigm, contribution, techniques, benchmarks, short name and TL;DR from title + abstract, 40 papers per call, validated against the taxonomy.</li>
            <li><b>Resolve</b> — published venue from Semantic Scholar (batch API) → arXiv comment (“accepted to …”) → OpenAlex; official publisher BibTeX via CrossRef when there is a DOI, a canonical <span class="mono">@inproceedings</span> for DOI-less venues (NeurIPS, ICLR, CoRL, RSS), else an arXiv entry. Venue-only copies of arXiv papers are merged as duplicates.</li>
            <li><b>Build</b> — <span class="mono">atlases/${esc(A)}/public/*.json</span> + <span class="mono">atlas.bib</span>, served by the hub.</li>
          </ol></div>
      </div>
      ${ADMIN ? `<div class="section"><h2>Run now</h2><p class="sub">Starts on the hub machine in the background; progress and logs appear below and on the Atlases page.</p>
        <div class="card" style="padding:14px 18px"><div class="marks-row" style="margin-bottom:12px">
          <button class="btn primary" data-pjob="update">Update now</button><button class="btn" data-pjob="update-full">Full refresh</button><button class="btn" data-pjob="build">Rebuild site data</button><button class="btn" data-pjob="recall">Recall check</button></div>
          <div id="pjob">${jobsHtml(A)}</div></div></div>` : ''}
      <div class="section"><h2>Keep it fresh</h2><p class="sub">Runs automatically once a week (<span class="mono">./atlas cron install</span>). Incremental: only new papers are fetched and labelled; arXiv papers that got accepted since are upgraded to their venue. New papers carry a <span class="new-tag">new</span> badge for 10 days — sort by “Recently added”.</p>
        <pre class="cmd">./atlas add ${esc(A)} 2510.12345     # saw a paper? add it now (~1 min), prints its labels
./atlas update ${esc(A)}             # refresh now: harvest (last 2 months) → merge → classify → venues → build
./atlas update --all               # every atlas (this is what cron runs)
cat atlases/${esc(A)}/data/logs/new-&lt;date&gt;.md   # what each update added</pre></div>
      <div class="section"><h2>Queries</h2><p class="sub">“Core” = how many in-scope core papers each query found (a paper can be found by several). Queries with high hits and low core are noisy; queries with unique core finds are what protects recall.</p>
        <div class="card scroll-box">${qt}</div></div>
      <div class="section"><h2>Excluded papers</h2><p class="sub">Everything the classifier or the prefilter threw out — search here when you suspect a paper is missing.</p>
        <div class="card" style="padding:14px 18px"><input class="facet-search" id="exq" placeholder="Search excluded titles…" style="max-width:420px"><div id="exl" class="muted" style="font-size:13px">Loading…</div></div></div>`;
    if (!EXCL) EXCL = CACHE[A].excl = await fetch(`a/${A}/excluded.json`).then((r) => r.json());
    const draw = () => {
      const t = $('#exq').value.toLowerCase().trim();
      const rows = (t ? EXCL.filter((e) => e.t.toLowerCase().includes(t)) : EXCL.filter((e) => e.why === 'llm:out')).slice(0, 80);
      $('#exl').innerHTML = `<div style="margin:8px 0 4px">${t ? `${fmt(rows.length)}${rows.length === 80 ? '+' : ''} matches` : `${fmt(EXCL.filter((e) => e.why === 'llm:out').length)} papers labelled out-of-scope · ${fmt(EXCL.length)} excluded in total`}</div>` +
        rows.map((e) => `<div class="ex-item"><span class="why">${esc(e.why)}</span><span style="color:var(--ink-2)">${esc(e.t)} <span class="muted">(${e.y || '?'})</span>${e.tl ? `<br><span class="muted" style="font-size:12px">${esc(e.tl)}</span>` : ''}</span></div>`).join('');
    };
    $('#exq').addEventListener('input', debounce(draw, 120));
    $('#app').onclick = (e) => {
      const b = e.target.closest('[data-pjob]'); if (!b) return;
      if (b.dataset.pjob === 'update-full' && !confirm('Full refresh re-pages every query (≈30 min + classification of anything new). Continue?')) return;
      runJob(b.dataset.pjob, A, `${b.dataset.pjob} started`);
    };
    if (ADMIN) pollJobs();
    draw();
  }

  // ───────── global keys / theme ─────────
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { if ($('#settings').classList.contains('open')) closeSettings(); else if (S._drawer) closeDrawer(); closeMenus(); return; }
    if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      e.preventDefault();
      if (S.view !== 'library') location.hash = `#/${A || SETTINGS.default_atlas}/library`;
      setTimeout(() => $('#q')?.focus(), 30);
    }
  });
  window.addEventListener('popstate', () => { if (!location.hash.includes('/paper?') && S._drawer) closeDrawer(true); });
  $('#themeBtn').addEventListener('click', () => {
    const cur = document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    saveSettings({ theme: cur === 'dark' ? 'light' : 'dark' });
  });


  // ───────── hub: settings ─────────
  const BGS = [['paper', 'Paper'], ['white', 'White'], ['mist', 'Mist'], ['sand', 'Sand'], ['grid', 'Grid'], ['dots', 'Dots'], ['aurora', 'Aurora'], ['custom', 'Custom']];
  const ACCENTS = [['indigo', 'Indigo'], ['teal', 'Teal'], ['violet', 'Violet'], ['rose', 'Rose'], ['amber', 'Amber'], ['graphite', 'Graphite']];
  function applySettings() {
    const r = document.documentElement;
    if (!SETTINGS.theme || SETTINGS.theme === 'auto') delete r.dataset.theme; else r.dataset.theme = SETTINGS.theme;
    r.dataset.bg = SETTINGS.background || 'paper';
    r.dataset.accent = SETTINGS.accent || 'indigo';
    r.style.setProperty('--bg-custom', SETTINGS.bg_custom || '#f4f1ea');
    try { localStorage.setItem('atlas-look', JSON.stringify({ theme: SETTINGS.theme, background: SETTINGS.background, accent: SETTINGS.accent, bg_custom: SETTINGS.bg_custom })); } catch (_) {}
  }
  async function saveSettings(patch) {
    SETTINGS = { ...SETTINGS, ...patch };
    applySettings();
    if ($('#settings').classList.contains('open')) renderSettings();
    if (!ADMIN) {  // viewers on the LAN: keep their look in this browser only
      try { const l = JSON.parse(localStorage.getItem('atlas-look-local') || '{}'); localStorage.setItem('atlas-look-local', JSON.stringify({ ...l, ...patch })); } catch (_) {}
      return;
    }
    try {
      const r = await fetch('api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) });
      if (!r.ok) throw 0;
      SETTINGS = await r.json();
      renderSwitcher();
      if (S.view === 'atlases') renderHub();
    } catch (_) { toast('Could not save settings to the server'); }
  }
  function renderSettings() {
    const opt = (key, val, label, extra = '') => `<button class="${(SETTINGS[key] || '') === val ? 'on' : ''}" data-set="${key}" data-val="${val}" ${extra}>${label}</button>`;
    $('#settingsBody').innerHTML = `
      <h2 class="d-title" style="font-size:22px">Settings</h2>
      <p class="muted" style="margin:0 0 18px;font-size:13px">${ADMIN ? 'Saved on the server (<span class="mono">settings.json</span>) — everyone who opens this hub sees the same look and default atlas.' : 'Look changes are kept in this browser only until you unlock editing.'}</p>
      <div class="set-sec"><h3>Editing</h3>${ADMIN_LOCAL ? '<p class="muted">You are on the hub machine — creating, renaming, updates and backups are enabled.</p>'
        : ADMIN ? '<div class="marks-row"><span class="muted" style="font-size:13px">Unlocked on this device with the admin key.</span><button class="btn" id="lockBtn">Lock</button></div>'
        : `<p class="muted">Viewing from another device. Unlock to create / rename atlases and run updates or backups here.</p>${unlockHtml()}`}</div>
      ${GATE ? `<div class="set-sec"><h3>Site password</h3><p class="muted">${ON_HUB ? 'Other devices must enter this password to open the hub. This machine is never asked.' : 'You are signed in with the site password. Changing it signs every other device out.'}</p>
        <form class="pw-form" id="pwForm">${ON_HUB ? '' : '<input name="current" type="password" placeholder="current password" autocomplete="current-password" required>'}
          <input name="new" type="password" placeholder="new password (4+ characters)" autocomplete="new-password" minlength="4" required>
          <input name="confirm" type="password" placeholder="repeat new password" autocomplete="new-password" minlength="4" required>
          <div class="marks-row"><button class="btn primary">Save password</button>${ON_HUB ? '' : '<a class="btn" href="/logout">Sign out</a>'}</div></form></div>` : ''}
      <div class="set-sec"><h3>Default atlas</h3><p class="muted">What <span class="mono">/</span> opens.</p>
        <div class="set-list">${ATLASES.map((a) => `<button class="set-atlas${SETTINGS.default_atlas === a.id ? ' on' : ''}" data-set="default_atlas" data-val="${esc(a.id)}" ${a.built ? '' : 'disabled'}>
          <span class="radio"></span><span><b>${esc(a.title || a.id)}</b><small>${esc(a.subtitle || '')}</small></span><span class="muted">${a.built ? fmt(a.n_core) + ' core' : 'not built'}</span></button>`).join('')}</div></div>
      <div class="set-sec"><h3>Theme</h3><div class="seg">${opt('theme', 'auto', 'Auto')}${opt('theme', 'light', 'Light')}${opt('theme', 'dark', 'Dark')}</div></div>
      <div class="set-sec"><h3>Background</h3>
        <div class="swatches">${BGS.map(([v, l]) => `<button class="swatch bg-${v}${SETTINGS.background === v ? ' on' : ''}" data-set="background" data-val="${v}" title="${l}"><i></i><span>${l}</span></button>`).join('')}</div>
        <label class="custom-bg${SETTINGS.background === 'custom' ? '' : ' dim'}">Custom colour (light theme) <input type="color" id="bgCustom" value="${esc(SETTINGS.bg_custom || '#f4f1ea')}"></label></div>
      <div class="set-sec"><h3>Accent</h3>
        <div class="swatches">${ACCENTS.map(([v, l]) => `<button class="swatch acc acc-${v}${SETTINGS.accent === v ? ' on' : ''}" data-set="accent" data-val="${v}" title="${l}"><i></i><span>${l}</span></button>`).join('')}</div></div>
      <div class="set-sec"><h3>Library cards</h3><div class="seg">${opt('density', 'cards', 'Cards')}${opt('density', 'compact', 'Compact')}</div></div>
      ${ADMIN ? `<div class="set-sec"><h3>Backup</h3><p class="muted">Snapshot every atlas (labels, venues, notes, raw harvest) into the repo, commit, and push if a remote is set. The weekly <span class="mono">dot save</span> does the same.</p>
        <div class="marks-row"><button class="btn primary" id="backupBtn">Back up now</button><span class="muted" id="backupInfo" style="font-size:12.5px">…</span></div></div>` : ''}`;
    if (ADMIN) {
      fetch('api/backup').then((r) => r.json()).then((b) => {
        const [when, msg] = (b.last_commit || '').split('|');
        $('#backupInfo').innerHTML = `last commit ${esc((when || '').slice(0, 16))} — ${esc(msg || '')}<br>${b.remote ? 'remote: <span class="mono">' + esc(b.remote) + '</span>' : '<b>no git remote yet</b> — backups stay on this machine'}${b.dirty ? ' · uncommitted changes' : ''}`;
      }).catch(() => {});
      $('#backupBtn').onclick = () => runJob('backup', '', 'Backup started');
    }
    $('#bgCustom').oninput = debounce((e) => saveSettings({ bg_custom: e.target.value, background: 'custom' }), 250);
    bindUnlock();
    if ($('#lockBtn')) $('#lockBtn').onclick = lockDevice;
    const pf = $('#pwForm');
    if (pf) pf.onsubmit = async (e) => {
      e.preventDefault();
      if (pf.new.value !== pf.confirm.value) return toast('The two new passwords differ');
      const r = await fetch('api/password', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current: pf.current ? pf.current.value : '', new: pf.new.value }) });
      const j = await r.json().catch(() => ({}));
      if (r.ok) { toast('Password saved'); pf.reset(); } else toast(j.error || 'Could not save the password');
    };
  }
  async function unlock(key) {
    KEY = (key || '').trim();
    const who = await fetch('api/whoami').then((r) => r.json()).catch(() => ({}));
    if (who.admin && !who.local) {
      try { localStorage.setItem('atlas-admin-key', KEY); } catch (_) {}
      ADMIN = true; toast('Editing unlocked on this device');
      SETTINGS = await fetch('api/settings').then((r) => r.json()); applySettings();
    } else if (!who.admin) { KEY = ''; try { localStorage.removeItem('atlas-admin-key'); } catch (_) {} toast('Wrong key'); }
    return ADMIN;
  }
  function lockDevice() { KEY = ''; try { localStorage.removeItem('atlas-admin-key'); } catch (_) {} ADMIN = false; toast('Locked on this device'); renderSettings(); if (S.view === 'atlases') renderHub(); }
  const unlockHtml = () => `<form class="unlock" id="unlockForm"><input name="key" type="password" autocomplete="off" placeholder="admin key" required><button class="btn primary">Unlock editing</button></form>
    <p class="muted" style="font-size:12px;margin:6px 0 0">On the hub machine run <span class="mono">./atlas admin-key</span> — it prints the key and a one-time link for this device.</p>`;
  function bindUnlock() {
    const f = $('#unlockForm'); if (!f) return;
    f.onsubmit = async (e) => { e.preventDefault(); if (await unlock(f.key.value)) { if ($('#settings').classList.contains('open')) renderSettings(); if (S.view === 'atlases') renderHub(); } };
  }
  function openSettings() { renderSettings(); const d = $('#settings'); d.classList.add('open'); d.setAttribute('aria-hidden', 'false'); }
  function closeSettings() { const d = $('#settings'); d.classList.remove('open'); d.setAttribute('aria-hidden', 'true'); }
  $('#settings').addEventListener('click', (e) => {
    if (e.target.closest('[data-close]')) return closeSettings();
    const b = e.target.closest('[data-set]');
    if (b && !b.disabled) {
      const patch = { [b.dataset.set]: b.dataset.val };
      if (b.dataset.set === 'density') { try { localStorage.removeItem('atlas-dense'); } catch (_) {} S.dense = b.dataset.val === 'compact'; if (S.view === 'library') updateLibrary(); }
      saveSettings(patch);
    }
  });
  $('#settingsBtn').addEventListener('click', openSettings);

  // ───────── hub: atlas switcher ─────────
  function renderSwitcher() {
    const cur = ATLASES.find((a) => a.id === A);
    $('#atlasBtn').innerHTML = `<span class="ab-t">${esc(cur?.title || META.title || 'Atlases')}</span><span class="ab-s">${esc(cur?.subtitle || META.subtitle || 'choose an atlas')}</span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 10l5 5 5-5"/></svg>`;
    $('#atlasMenu').innerHTML = ATLASES.map((a) => `<a class="am-item${a.id === A ? ' on' : ''}${a.built ? '' : ' off'}" href="${a.built ? `#/${esc(a.id)}/library` : '#/atlases'}">
        <span class="am-t">${esc(a.title || a.id)}${SETTINGS.default_atlas === a.id ? '<span class="am-def">default</span>' : ''}</span>
        <span class="am-s">${esc(a.subtitle || '')}</span><span class="am-n">${a.built ? fmt(a.n_core) + ' core papers' : 'not built yet'}</span></a>`).join('') +
      `<div class="am-foot"><a href="#/atlases" id="amNew">＋ New atlas</a><a href="#/atlases">Manage atlases</a><a href="#/guide">Guide</a><button id="amSettings">Settings…</button></div>`;
  }
  function closeMenus() { $('#atlasMenu').classList.remove('open'); $('#atlasBtn').setAttribute('aria-expanded', 'false'); }
  $('#atlasBtn').addEventListener('click', (e) => { e.stopPropagation(); const m = $('#atlasMenu'); const o = !m.classList.contains('open'); m.classList.toggle('open', o); $('#atlasBtn').setAttribute('aria-expanded', String(o)); });
  $('#atlasMenu').addEventListener('click', (e) => {
    if (e.target.closest('#amSettings')) { closeMenus(); openSettings(); return; }
    if (e.target.closest('#amNew')) { hubUi.focusNew = true; hubUi.newOpen = true; if (S.view === 'atlases') { e.preventDefault(); renderHub(); } }
    if (e.target.closest('a')) closeMenus();
  });
  document.addEventListener('click', (e) => { if (!e.target.closest('.switcher')) closeMenus(); });


  // ───────── guide (renders docs/GUIDE.md) ─────────
  let GUIDE_MD = null;
  function mdInline(t) {
    let s = esc(t);
    s = s.replace(/&lt;!--.*?--&gt;/g, '');
    s = s.replace(/&lt;b&gt;(.*?)&lt;\/b&gt;/g, '<b>$1</b>');
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, a, h) => `<a href="${h}"${h.startsWith('#') ? '' : ' target="_blank" rel="noopener"'}>${a}</a>`);
    return s;
  }
  function renderMd(src) {
    const L = src.replace(/\r/g, '').split('\n');
    const out = [], toc = [];
    const isBlock = (l) => /^(#{1,3}\s|```|>|\||\s*([-*]|\d+\.)\s)/.test(l);
    let i = 0, n = 0;
    while (i < L.length) {
      const l = L[i];
      if (!l.trim()) { i++; continue; }
      if (/^\s*```/.test(l)) {
        const buf = []; i++;
        while (i < L.length && !/^\s*```/.test(L[i])) buf.push(L[i++]);
        i++; out.push(`<pre class="cmd">${esc(buf.join('\n'))}</pre>`); continue;
      }
      const h = l.match(/^(#{1,3})\s+(.*?)\s*(?:<!--\s*#([\w-]+)\s*-->)?\s*$/);
      if (h) {
        const lvl = h[1].length, id = h[3] || `g-${++n}`;
        if (lvl === 2) toc.push([id, h[2]]);
        out.push(`<h${lvl} class="g-h${lvl}" id="g-${id}">${mdInline(h[2])}</h${lvl}>`); i++; continue;
      }
      if (/^>/.test(l)) {
        const buf = []; while (i < L.length && /^>/.test(L[i])) buf.push(L[i++].replace(/^>\s?/, ''));
        out.push(`<blockquote>${mdInline(buf.join(' '))}</blockquote>`); continue;
      }
      if (/^\|/.test(l)) {
        const rows = []; while (i < L.length && /^\|/.test(L[i])) rows.push(L[i++]);
        const cells = (r) => r.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
        out.push(`<div class="g-table"><table class="qtable"><thead><tr>${cells(rows[0]).map((c) => `<th>${mdInline(c)}</th>`).join('')}</tr></thead><tbody>${rows.slice(2).map((r) => `<tr>${cells(r).map((c) => `<td>${mdInline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
        continue;
      }
      if (/^([-*]|\d+\.)\s/.test(l)) {
        const ordered = /^\d+\./.test(l), items = [];
        let cur = null, brk = false;
        while (i < L.length) {
          const x = L[i];
          if (!x.trim()) {
            let j = i + 1; while (j < L.length && !L[j].trim()) j++;
            if (j < L.length && (/^\s{2,}\S/.test(L[j]) || /^([-*]|\d+\.)\s/.test(L[j]))) { brk = true; i = j; continue; }
            break;
          }
          const top = x.match(/^([-*]|\d+\.)\s+(.*)$/);
          if (top) { cur = { text: top[2], sub: [], after: [] }; items.push(cur); brk = false; i++; continue; }
          const sb = x.match(/^\s{2,}([-*]|\d+\.)\s+(.*)$/);
          if (sb && cur) { cur.sub.push(sb[2]); i++; continue; }
          if (/^\s{2,}\S/.test(x) && cur) {
            if (/^\s*```/.test(x)) break;
            if (cur.sub.length || brk) cur.after.push(x.trim()); else cur.text += ' ' + x.trim();
            brk = false; i++; continue;
          }
          break;
        }
        const tag = ordered ? 'ol' : 'ul';
        out.push(`<${tag}>${items.map((it) => `<li>${mdInline(it.text)}${it.sub.length ? `<ul>${it.sub.map((x) => `<li>${mdInline(x)}</li>`).join('')}</ul>` : ''}${it.after.map((x) => `<p>${mdInline(x)}</p>`).join('')}</li>`).join('')}</${tag}>`);
        continue;
      }
      const buf = []; while (i < L.length && L[i].trim() && !isBlock(L[i])) buf.push(L[i++].trim());
      out.push(`<p>${mdInline(buf.join(' '))}</p>`);
    }
    return { html: out.join('\n'), toc };
  }
  async function renderGuide(section) {
    document.title = 'Guide · SurveyAtlas';
    $('#stamp').textContent = 'Guide';
    $('#app').onclick = null;
    if (GUIDE_MD === null) {
      $('#app').innerHTML = '<div class="loading"><div class="spinner"></div><span>Loading the guide…</span></div>';
      try { GUIDE_MD = await _fetch('guide.md').then((r) => { if (!r.ok) throw 0; return r.text(); }); }
      catch (_) { $('#app').innerHTML = '<div class="empty"><h3>Guide not found</h3><p>docs/GUIDE.md is missing from the repo.</p></div>'; return; }
    }
    try { localStorage.setItem('atlas-guide-seen', '1'); } catch (_) {}
    const { html, toc } = renderMd(GUIDE_MD);
    const def = ATLASES.find((a) => a.id === SETTINGS.default_atlas) || ATLASES.find((a) => a.built);
    $('#app').innerHTML = `
      <div class="guide">
        <aside class="g-toc"><div class="g-toc-inner"><h4>Guide</h4>${toc.map(([id, t]) => `<a href="#/guide/${id}" data-sec="${id}">${mdInline(t)}</a>`).join('')}
          <div class="g-toc-foot">Source: <span class="mono">docs/GUIDE.md</span></div></div></aside>
        <article class="card g-body">
          <div class="g-quick">${def ? `<a class="btn primary" href="#/${esc(def.id)}/library">Open ${esc(def.title || def.id)}</a>` : ''}
            <a class="btn" href="#/atlases">Manage / new atlas</a><button class="btn" id="gSettings">Settings…</button>
            <span class="muted">${ADMIN ? (ADMIN_LOCAL ? 'You are on the hub machine — editing enabled.' : 'Editing unlocked on this device.') : 'Viewing only — see “Using it from other devices” to unlock editing.'}</span></div>
          ${html}
        </article>
      </div>`;
    $('#gSettings').onclick = openSettings;
    const go = (id, smooth) => { const el = $(`#g-${CSS.escape(id)}`); if (el) el.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' }); $$('.g-toc a').forEach((a) => a.classList.toggle('on', a.dataset.sec === id)); };
    $('.g-toc').onclick = (e) => { const a = e.target.closest('[data-sec]'); if (!a) return; e.preventDefault(); history.replaceState(null, '', `#/guide/${a.dataset.sec}`); go(a.dataset.sec, true); };
    if (section) setTimeout(() => go(section, false), 30); else window.scrollTo(0, 0);
  }

  // ───────── hub: jobs ─────────
  let jobTimer = null;
  async function refreshAtlases() {
    ATLASES = await fetch('api/atlases').then((r) => r.json());
    renderSwitcher();
  }
  async function pollJobs() {
    try { JOBS = await fetch('api/jobs').then((r) => r.json()); } catch (_) { return; }
    const running = JOBS.some((j) => j.rc === null);
    if ($('#jobs')) $('#jobs').innerHTML = jobsHtml();
    if ($('#pjob')) $('#pjob').innerHTML = jobsHtml(A);
    const done = JOBS.filter((j) => j.rc !== null && !pollJobs.seen.has(j.id));
    for (const j of done) {
      pollJobs.seen.add(j.id);
      if (pollJobs.watch.has(j.id)) {
        pollJobs.watch.delete(j.id);
        toast(`${j.kind}${j.atlas ? ' · ' + j.atlas : ''} ${j.rc === 0 ? 'finished' : 'failed (see log)'}`);
        if (j.atlas) delete CACHE[j.atlas];
        await refreshAtlases();
        if (S.view === 'atlases') renderHub();
      }
    }
    clearTimeout(jobTimer);
    if (running) jobTimer = setTimeout(pollJobs, 2000);
  }
  pollJobs.seen = new Set(); pollJobs.watch = new Set();
  async function runJob(kind, atlas, msg, extra = {}) {
    const url = atlas ? `api/atlases/${atlas}/jobs` : 'api/backup';
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind, ...extra }) });
    const j = await r.json();
    if (!r.ok) { toast(j.error || 'failed'); return; }
    pollJobs.watch.add(j.id);
    toast(msg || `${kind} started`);
    pollJobs();
  }
  const ago = (t) => { const s = Math.max(0, Date.now() / 1000 - t); return s < 60 ? `${Math.round(s)}s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`; };
  function jobsHtml(only) {
    const list = JOBS.filter((j) => !only || j.atlas === only).slice(0, only ? 3 : 12);
    if (!list.length) return `<p class="muted" style="font-size:13px;margin:0">${only ? 'No jobs for this atlas yet.' : 'No jobs yet — actions you start here (drafts, builds, updates, backups) show up with their logs.'}</p>`;
    return list.map((j) => `<details class="job"${j.rc === null ? ' open' : ''}><summary>
        <span class="job-st ${j.rc === null ? 'run' : j.rc === 0 ? 'ok' : 'bad'}">${j.rc === null ? 'running' : j.rc === 0 ? 'done' : 'failed'}</span>
        <b>${esc(j.kind)}</b>${j.atlas ? ` <span class="mono">${esc(j.atlas)}</span>` : ''}<span class="muted">${ago(j.started)}${j.ended ? ' · ' + Math.round(j.ended - j.started) + 's' : ''}</span></summary>
        <pre class="cmd job-log">${esc(j.cmd)}\n${esc(j.tail.join('\n'))}</pre></details>`).join('');
  }

  // ───────── hub: all atlases ─────────
  function renderHub() {
    document.title = 'Atlases';
    $('#stamp').textContent = `${ATLASES.length} atlas${ATLASES.length === 1 ? '' : 'es'}`;
    const spark = (by) => {
      const ys = Object.keys(by || {}).map(Number).sort((a, b) => a - b);
      if (!ys.length) return '';
      const vals = ys.map((y) => by[y]), m = Math.max(1, ...vals), bw = 100 / vals.length;
      return `<svg class="hub-spark" viewBox="0 0 100 32" preserveAspectRatio="none">${vals.map((v, i) => `<rect x="${i * bw + .8}" y="${32 - (v / m) * 30}" width="${bw - 1.6}" height="${(v / m) * 30}" rx=".8"/>`).join('')}</svg>
        <div class="hub-axis"><span>${ys[0]}</span><span>core papers per year</span><span>${ys[ys.length - 1]}</span></div>`;
    };
    const editForm = (a) => `<form class="hub-form" data-edit="${esc(a.id)}">
        <label>Title<input name="title" value="${esc(a.title || '')}" required></label>
        <label>Subtitle<input name="subtitle" value="${esc(a.subtitle || '')}"></label>
        <label>Description<textarea name="description" rows="3">${esc(a.description && a.built ? a.description : '')}</textarea></label>
        <label>Id <small>(folder + URL; changing it breaks old links)</small><input name="new_id" value="${esc(a.id)}" pattern="[a-z0-9][a-z0-9_-]{0,40}"></label>
        <div class="hub-foot"><span class="muted">display fields are written to <span class="mono">atlases/${esc(a.id)}/atlas.py</span></span><span><button type="button" class="btn" data-cancel>Cancel</button><button class="btn primary">Save</button></span></div></form>`;
    const running = (id) => JOBS.some((j) => j.rc === null && j.atlas === id);
    const builtCard = (a) => `
        <div class="card hub-card">
          <div class="hub-top"><h3>${esc(a.title || a.id)}</h3>${SETTINGS.default_atlas === a.id ? '<span class="badge now">default</span>' : ''}${ADMIN ? `<button class="icon-btn hub-edit" data-editbtn="${esc(a.id)}" title="Rename / edit">${ICON.pen}</button>` : ''}</div>
          ${hubUi.editing === a.id ? editForm(a) : `
          <div class="hub-sub">${esc(a.subtitle || '')}</div>
          <p class="hub-desc">${esc(a.description || '')}</p>
          <div class="hub-stats"><div><b>${fmt(a.n_core)}</b><span>core papers</span></div><div><b>${fmt(a.n_papers)}</b><span>in library</span></div><div><b>${a.n_core ? Math.round((a.n_venue_core || 0) / a.n_core * 100) : 0}%</b><span>with venue</span></div></div>
          ${spark(a.by_year)}
          <div class="hub-foot"><span class="muted">built ${esc((a.built_at || '').slice(0, 10))} · <span class="mono">${esc(a.id)}</span></span>
            <span>${ADMIN ? `<button class="btn" data-job="update" data-atlas="${esc(a.id)}" ${running(a.id) ? 'disabled' : ''}>${running(a.id) ? 'Running…' : 'Update now'}</button>` : ''}${SETTINGS.default_atlas === a.id ? '' : `<button class="btn" data-default="${esc(a.id)}">Set as default</button>`}<a class="btn primary" href="#/${esc(a.id)}/library">Open</a></span></div>`}
        </div>`;
    const draftCard = (a) => {
      const step = a.domain_ready ? 3 : a.has_draft ? 2 : 1;
      return `
        <div class="card hub-card unbuilt">
          <div class="hub-top"><h3>${esc(a.title || a.id)}</h3><span class="badge next">${a.domain_ready ? 'ready to build' : 'setting up'}</span>${ADMIN ? `<button class="icon-btn hub-edit" data-editbtn="${esc(a.id)}" title="Rename / edit">${ICON.pen}</button>` : ''}</div>
          ${hubUi.editing === a.id ? editForm(a) : `
          <div class="hub-sub">${esc(a.subtitle || '')} · <span class="mono">atlases/${esc(a.id)}/atlas.py</span></div>
          <ol class="steps">
            <li class="${step > 1 ? 'done' : 'cur'}"><b>Brief & draft</b> — describe the field (what's in / out, the survey's storyline); Claude drafts the domain file (queries, taxonomy, rules, landmarks). A few minutes.</li>
            <li class="${step > 2 ? 'done' : step === 2 ? 'cur' : ''}"><b>Review</b> — read <span class="mono">atlas.py</span> (scope + paradigms first) and <span class="mono">DRAFT_NOTES.md</span>; edit, then <i>Check</i> and <i>Mark ready</i>.</li>
            <li class="${step === 3 ? 'cur' : ''}"><b>First build</b> — full harvest + classification (≈ 1–2 h for ~10k candidates), then it appears here with its library.</li>
          </ol>
          ${ADMIN ? `
          <textarea class="brief" data-brief="${esc(a.id)}" rows="4" placeholder="Brief: what counts as this field, what is out, which storyline the survey should tell, key benchmarks…">${esc(a.brief || '')}</textarea>
          <div class="hub-foot"><span class="muted">${running(a.id) ? 'job running…' : ''}</span><span>
            <button class="btn danger" data-discard="${esc(a.id)}" ${running(a.id) ? 'disabled' : ''} title="Delete this atlas (it has no data yet)">Discard</button>
            <button class="btn" data-job="draft" data-atlas="${esc(a.id)}" ${running(a.id) ? 'disabled' : ''}>${a.has_draft ? 'Re-draft' : 'Draft with Claude'}</button>
            <button class="btn" data-job="check" data-atlas="${esc(a.id)}" ${running(a.id) ? 'disabled' : ''}>Check</button>
            ${a.domain_ready ? `<button class="btn primary" data-job="update-full" data-atlas="${esc(a.id)}" ${running(a.id) ? 'disabled' : ''}>Run first build</button>`
              : `<button class="btn primary" data-job="ready" data-atlas="${esc(a.id)}" ${running(a.id) ? 'disabled' : ''}>Mark ready</button>`}</span></div>`
          : `<pre class="cmd">./atlas draft ${esc(a.id)} --brief "…"\n./atlas ready ${esc(a.id)}\n./atlas update ${esc(a.id)} --full</pre>`}`}
        </div>`;
    };
    const newCard = ADMIN && !hubUi.newOpen ? `
        <div class="card hub-card hub-new"><h3>New atlas</h3><p class="hub-desc">Start a new field. It gets its own folder, domain file and library, served here next to the others. You can cancel at any point — and discard it later if it hasn't been built.</p>
          <div><button class="btn primary" id="newOpen">＋ New atlas</button> <a class="btn" href="#/guide/new">How it works</a></div></div>`
      : ADMIN ? `
        <div class="card hub-card hub-new"><h3>New atlas</h3><p class="hub-desc">Start a new field. It gets its own folder, domain file and library, served here next to the others.</p>
          <form class="hub-form" id="newAtlas">
            <label>Title<input name="title" placeholder="AgentAtlas" required></label>
            <label>Id <small>(lowercase, used in URLs)</small><input name="id" placeholder="agentatlas" pattern="[a-z0-9][a-z0-9_-]{0,40}" required></label>
            <label>Subtitle<input name="subtitle" placeholder="Embodied Agent Literature Atlas"></label>
            <label>Field<input name="field" placeholder="Embodied Agents"></label>
            <label>Brief <small>(optional now — used by “Draft with Claude”)</small><textarea name="brief" rows="3" placeholder="What is in scope, what is out, the survey's storyline, key benchmarks…"></textarea></label>
            <div class="hub-foot"><span class="muted">or: <span class="mono">./atlas new &lt;id&gt; --title …</span></span><span><button type="button" class="btn" id="newCancel">Cancel</button><button class="btn primary">Create atlas</button></span></div>
          </form></div>`
      : `<div class="card hub-card hub-new"><h3>New atlas</h3><p class="hub-desc">Creating, renaming and running atlases is locked on this device. Unlock it with the hub's admin key:</p>
          ${unlockHtml()}</div>`;
    $('#app').onclick = null;
    $('#app').innerHTML = `
      <a class="guide-banner card" href="#/guide">${ICON.book}<span><b>New here? Read the guide</b><small>What each page is for, search syntax, adding papers, creating / renaming atlases, backups and moving to a new machine.</small></span><span class="gb-go">Open →</span></a>
      <div class="page-head"><div><h1>Atlases</h1><p>Every literature atlas this hub serves. Each one is a folder under <span class="mono">atlases/</span> with its own queries, taxonomy and data; they share the engine and this site.</p></div>
        <button class="btn" id="hubSettings">Settings…</button></div>
      <div class="hub-grid">${ATLASES.map((a) => (a.built ? builtCard(a) : draftCard(a))).join('')}${newCard}</div>
      ${ADMIN ? `<div class="section"><h2>Jobs</h2><p class="sub">Background work started from this page. Logs also go to <span class="mono">hub/jobs/</span>.</p><div class="card" style="padding:12px 16px" id="jobs">${jobsHtml()}</div></div>` : ''}`;
    $('#hubSettings').onclick = openSettings;
    bindUnlock();
    if ($('#newOpen')) $('#newOpen').onclick = () => { hubUi.newOpen = true; hubUi.focusNew = true; renderHub(); };
    if ($('#newCancel')) $('#newCancel').onclick = () => { hubUi.newOpen = false; renderHub(); };
    if (hubUi.focusNew && $('#newAtlas')) { hubUi.focusNew = false; $('#newAtlas').scrollIntoView({ block: 'center' }); $('#newAtlas').title.focus(); }
    const nf = $('#newAtlas');
    if (nf) {
      nf.title.addEventListener('input', () => { if (!nf.id.dataset.touched) nf.id.value = nf.title.value.toLowerCase().replace(/[^a-z0-9]+/g, '').slice(0, 40); });
      nf.id.addEventListener('input', () => { nf.id.dataset.touched = '1'; });
      nf.onsubmit = async (e) => {
        e.preventDefault();
        const body = Object.fromEntries(new FormData(nf).entries());
        const r = await fetch('api/atlases', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const j = await r.json();
        if (!r.ok) return toast(j.error || 'could not create');
        hubUi.newOpen = false;
        ATLASES = j.atlases; renderSwitcher(); renderHub(); toast(`Created ${body.id}`);
      };
    }
    $$('form[data-edit]').forEach((f) => {
      f.onsubmit = async (e) => {
        e.preventDefault();
        const id = f.dataset.edit, body = Object.fromEntries(new FormData(f).entries());
        const r = await fetch(`api/atlases/${id}/meta`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const j = await r.json();
        if (!r.ok) return toast(j.error || 'could not save');
        delete CACHE[id]; delete CACHE[j.id];
        if (A === id) A = '';
        hubUi.editing = null;
        ATLASES = j.atlases;
        if (SETTINGS.default_atlas === id && j.id !== id) SETTINGS.default_atlas = j.id;
        renderSwitcher(); renderHub(); toast('Saved');
      };
    });
    $('#app').onclick = (e) => {
      const d = e.target.closest('[data-default]'); if (d) return saveSettings({ default_atlas: d.dataset.default });
      const ed = e.target.closest('[data-editbtn]'); if (ed) { hubUi.editing = hubUi.editing === ed.dataset.editbtn ? null : ed.dataset.editbtn; return renderHub(); }
      if (e.target.closest('[data-cancel]')) { hubUi.editing = null; return renderHub(); }
      const dc = e.target.closest('[data-discard]');
      if (dc && !dc.disabled) {
        const id = dc.dataset.discard;
        if (!confirm(`Discard atlas “${id}”? This deletes atlases/${id}/ (its brief and draft). It has no papers yet.`)) return;
        fetch(`api/atlases/${id}/delete`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
          .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
          .then(({ ok, j }) => { if (!ok) return toast(j.error || 'could not discard'); delete CACHE[id]; if (SETTINGS.default_atlas === id) SETTINGS.default_atlas = ''; ATLASES = j.atlases; renderSwitcher(); renderHub(); toast(`Discarded ${id}`); });
        return;
      }
      const jb = e.target.closest('[data-job]');
      if (jb && !jb.disabled) {
        const id = jb.dataset.atlas, kind = jb.dataset.job;
        const extra = {};
        if (kind === 'draft') { extra.brief = $(`[data-brief="${CSS.escape(id)}"]`)?.value || ''; if (!extra.brief.trim()) return toast('Write a brief first'); }
        if (kind === 'update-full' && !confirm('Run the first full build now? It harvests arXiv + OpenAlex and classifies every candidate with Claude (roughly 1–2 hours for ~10k candidates).')) return;
        return runJob(kind, id, `${kind} started for ${id}`, extra);
      }
    };
    renderSwitcher();
    if (ADMIN) pollJobs();
  }

  boot().catch((err) => {
    console.error(err);
    $('#app').innerHTML = `<div class="empty"><h3>Could not load data</h3><p>${esc(err.message)} — build an atlas with <span class="mono">./atlas update &lt;id&gt;</span> and serve with <span class="mono">./atlas serve</span>.</p></div>`;
  });
})();
