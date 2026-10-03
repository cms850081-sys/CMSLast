/* پنل من — بخشِ «مدیریت» (نقش‌محور)
   تنبل بار می‌شود (مثل clock.js). همه‌ی دکمه‌ها براساسِ caps ِ سرور ساخته می‌شوند،
   ولی امنیتِ واقعی سمتِ سرور است (hub_api.py)؛ اینجا فقط رابط است.
   قاعده: هیچ innerHTML با داده‌ی کاربر؛ فقط textContent. */
(function () {
  'use strict';
  var C, h, ic, hx, Sheet, toast, api, row, riconEl, secTitle, kv, nn, avatar, norm;
  var stack = [];

  /* ─── ابزارهای مشترک ────────────────────────────────────────── */
  function can(c) { return !!(C.S.boot && C.S.boot.caps && C.S.boot.caps.indexOf(c) > -1); }
  function feat(k) { return !!(C.S.boot && C.S.boot.features && C.S.boot.features[k]); }
  /* ─── stale-while-revalidate ───────────────────────────────────
     آخرین پاسخِ هر GET در حافظه می‌ماند: دفعه‌ی بعد صفحه «همان لحظه» با داده‌ی قبلی باز می‌شود و
     پشت‌صحنه تازه می‌شود (فقط اگر چیزی عوض شده باشد دوباره رندر می‌شود). بعد از هر نوشتن (post)
     کلِ حافظه خالی می‌شود تا هیچ‌وقت داده‌ی «قبل از ویرایش» نشان داده نشود. */
  var memo = {}, gen = 0;
  function swr(path, onData, onErr) {
    var hit = memo[path], had = hit !== undefined, sig = had ? JSON.stringify(hit) : '', g = gen;
    if (had) onData(hit);
    return api(path).then(function (d) {
      if (g !== gen) return d;               // وسطِ راه یک نوشتن انجام شد؛ این پاسخ ممکن است کهنه باشد
      memo[path] = d;
      if (!had || JSON.stringify(d) !== sig) onData(d);
      return d;
    }, function (e) { if (!had && onErr) onErr(e); });
  }
  function post(path, body) { return api(path, body || {}); }
  window.__hubOnWrite = function () { gen++; memo = {}; };   // hub.js api() صدایش می‌زند: هر نوشتنی (از هرجا) حافظه را خالی می‌کند
  /* پیش‌بارگذاریِ صفحه‌های مدیریت (همان مسیرهایی که خودِ صفحه‌ها می‌خوانند) — با تأخیر و پشتِ‌سرِهم، تا فشاری روی سرور نیاید */
  var pfAt = 0;
  function prefetch() {
    if (Date.now() - pfAt < 25000 || document.hidden) return;
    pfAt = Date.now();
    var list = [];
    if (can('comms')) list.push('/hub/api/comms/overview');
    if (can('match_edit') || can('match_delete') || can('match_create')) list.push('/hub/api/matches?scope=pending&q=');
    if (can('elo')) list.push('/hub/api/elo/leaderboard?limit=100');
    if (can('calendar')) list.push('/hub/api/calendar/month', '/hub/api/calendar/holidays');
    if (can('settings')) list.push('/hub/api/settings');
    if (can('admins_manage')) list.push('/hub/api/admin/list');
    if (can('pishva_panel')) list.push('/hub/api/requests', '/hub/api/logs?period=today&page=0');
    if (can('classes') || can('player_register') || can('match_create')) list.push('/hub/api/classes');
    (function next() {
      var path = list.shift(); if (!path) return;
      swr(path, function () {}).then(function () { setTimeout(next, 60); }, function () { setTimeout(next, 60); });
    })();
  }
  function errMsg(e) {
    if (e && e.data && e.data.message) return e.data.message;
    if (e && e.code === 403) return 'این کار برای شما مجاز نیست.';
    return 'انجام نشد؛ اتصال را بررسی کنید.';
  }
  function fail(e) { hx.err(); toast(errMsg(e)); }
  function todayTehran() {
    try { return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Tehran' }); }
    catch (e) { return new Date().toISOString().slice(0, 10); }
  }
  var RES = { white: 'برد سفید', black: 'برد سیاه', draw: 'تساوی', cancelled: 'لغو شده' };
  var STATUS = { active: 'فعال', suspended: 'تعلیق', kicked: 'اخراج', eliminated: 'حذف‌شده' };
  var FA = '۰۱۲۳۴۵۶۷۸۹';
  function fa(n) { return String(n).replace(/[0-9]/g, function (d) { return FA[d]; }); }

  /* ─── پشته‌ی شیت (چند مرحله‌ای، با «بازگشت») ────────────────── */
  /* هر مرحله یک‌بار ساخته و نگه داشته می‌شود تا با «بازگشت» از یک زیرمرحله (مثلاً انتخابِ
     بازیکن) آنچه کاربر پر کرده از بین نرود. بعد از هر تغییرِ موفق، مراحلِ زیرین «کثیف» و
     هنگامِ بازگشت از نو ساخته می‌شوند تا داده‌ی کهنه نشان ندهند. */
  function show(force) {
    var t = stack[stack.length - 1];
    if (!t.r || force) t.r = t.build() || {};
    var r = t.r;
    var body = h('div', null,
      stack.length > 1 ? h('button', { type: 'button', class: 'add-row back-row', onclick: back }, '‹ بازگشت') : null,
      r.body || null);
    Sheet.open({ title: t.title, body: body, foot: r.foot || null, onClose: function () { stack = []; } });
  }
  function push(title, build) { stack.push({ title: title, build: build }); show(); }
  function root(title, build) { stack = [{ title: title, build: build }]; show(); }
  function back() { stack.pop(); if (stack.length) show(); else Sheet.close(); }
  function refresh() { if (stack.length) show(true); }
  function dirty() { for (var i = 0; i < stack.length - 1; i++) stack[i].r = null; }
  function closeAll() { stack = []; Sheet.close(); }

  function spin() { return h('div', { class: 'spin', text: 'در حال بارگذاری…' }); }
  function empty(t) { return h('div', { class: 'empty', text: t }); }
  function errBox(retry) {
    return h('div', { class: 'empty' }, 'بارگذاری نشد. ', h('button', { type: 'button', class: 'add-row', text: 'تلاش دوباره', onclick: retry }));
  }
  /* بدنه‌ی ناهمگام: اسپینر، بعد پرشدن با نتیجه‌ی fetch */
  function lazy(src, render) {
    var box = h('div', null, spin());
    function go() {
      var shown = false;
      if (typeof src === 'string') {       // مسیرِ GET → stale-while-revalidate (داده‌ی قبلی فوری، تازه‌سازی پشت‌صحنه)
        var hit = memo[src];
        if (hit === undefined) box.replaceChildren(spin());
        swr(src, function (d) { shown = true; box.replaceChildren(render(d)); },
          function (e) { if (e && e.data && e.data.error === 'locked') return; box.replaceChildren(errBox(go)); });
        return;
      }
      box.replaceChildren(spin());
      src().then(function (d) { box.replaceChildren(render(d)); },
        function (e) { if (e && e.data && e.data.error === 'locked') return; box.replaceChildren(errBox(go)); });
    }
    go();
    return box;
  }

  function field(label, input, hint) {
    return h('div', { class: 'field' }, h('label', null, h('span', { text: label }), hint ? h('span', { text: hint }) : null), input);
  }
  function input(attrs) { return h('input', Object.assign({ class: 'inp', autocomplete: 'off' }, attrs || {})); }
  function select(options, value) {
    var s = h('select', { class: 'inp' });
    options.forEach(function (o) {
      var op = h('option', { value: o[0], text: o[1] });
      if (String(o[0]) === String(value)) op.selected = true;
      s.append(op);
    });
    return s;
  }
  function btn(label, cls, fn) { return h('button', { type: 'button', class: 'btn' + (cls ? ' ' + cls : ''), text: label, onclick: fn }); }
  /* دکمه‌ای که هنگام اجرا قفل می‌شود و دوبار زده نمی‌شود */
  function actBtn(label, cls, work, done) {
    var b = btn(label, cls);
    b.addEventListener('click', function () {
      if (b.disabled) return;
      b.disabled = true; var old = b.textContent; b.textContent = 'صبر کنید…';
      Promise.resolve().then(work).then(function (r) { hx.ok(); dirty(); if (done) done(r); },
        function (e) { fail(e); b.disabled = false; b.textContent = old; });
    });
    return b;
  }
  function footBtns() { var f = h('div', { class: 'foot-btns' }); add(f, arguments); return f; }
  function add(n, kids) { for (var i = 0; i < kids.length; i++) if (kids[i]) n.append(kids[i]); }
  function toggleRow(label, on, fn, sub) {
    var b = h('button', { type: 'button', class: 'switch' + (on ? ' on' : '') },
      h('span', null, h('span', { text: label }), sub ? h('small', { class: 'sw-sub', text: sub }) : null), h('i', { class: 'tg' }));
    var busy = false, confirmed = !!on;   // confirmed = آخرین وضعیتی که سرور تأیید کرده
    // اپتیمیستیک: کلید همان لحظه جابه‌جا می‌شود و سرور پشتِ صحنه تأیید می‌کند.
    // کلیک‌های پشتِ‌سرهم گم نمی‌شوند: بعد از هر پاسخ، اگر ظاهرِ کلید با وضعیتِ تأییدشده فرق داشت دوباره همگام می‌شود.
    // (قبلاً کلید تا پایانِ درخواست disabled بود و «گیر کرده» به نظر می‌رسید.)
    function run() {
      var target = b.classList.contains('on');
      busy = true;
      Promise.resolve(fn(target)).then(function (v) {
        busy = false;
        confirmed = (typeof v === 'boolean') ? v : target;
        if (b.classList.contains('on') !== confirmed) { run(); return; }
      }, function (e) {
        busy = false;
        b.classList.toggle('on', confirmed);   // برگرداندنِ کلید به آخرین وضعیتِ درست
        fail(e);
      });
    }
    b.addEventListener('click', function () {
      b.classList.toggle('on'); hx.sel();
      if (!busy) run();
    });
    return b;
  }
  function chipsBar(items, cur, onPick) {
    var w = h('div', { class: 'chips' });
    items.forEach(function (it) {
      w.append(h('button', { type: 'button', class: 'chip' + (cur === it[0] ? ' on' : ''), text: it[1], onclick: function () { if (cur !== it[0]) { hx.sel(); onPick(it[0]); } } }));
    });
    return w;
  }
  function confirmView(title, text, label, work, done, danger) {
    push(title, function () {
      return {
        body: h('div', { class: 'confirm' }, h('div', { class: 'text-block', text: text })),
        foot: footBtns(actBtn(label, danger === false ? '' : 'danger', work, function (r) { back(); if (done) done(r); }),
          btn('انصراف', 'soft', back))
      };
    });
  }
  function reasonView(title, label, placeholder, submit, done, min) {
    push(title, function () {
      var t = h('textarea', { class: 'inp', rows: 4, maxlength: 300, placeholder: placeholder || '' });
      return {
        body: h('div', null, field(label, t)),
        foot: footBtns(actBtn('ثبت', '', function () {
          if (t.value.trim().length < (min || 3)) { var e = new Error('x'); e.data = { message: 'متن خیلی کوتاه است.' }; throw e; }
          return submit(t.value.trim());
        }, function (r) { back(); if (done) done(r); }), btn('انصراف', 'soft', back))
      };
    });
  }

  /* ─── انتخاب‌گرِ بازیکن (جستجو‌پذیر) ─────────────────────────── */
  function pickPlayer(title, onPick, opt) {
    opt = opt || {};
    push(title, function () {
      var q = input({ type: 'search', placeholder: 'جستجوی نام یا کلاس' });
      var list = h('div', { class: 'group', style: 'margin-top:8px' });
      var note = h('div', { class: 'empty', text: '' });
      function fill() {
        var rows = (C.P().rows || []).filter(function (p) {
          if (opt.activeOnly && p.status && p.status !== 'active') return false;
          if (opt.exclude && opt.exclude.indexOf(p.id) > -1) return false;
          var s = norm(q.value); return !s || p._n.indexOf(s) > -1 || p._c.indexOf(s) > -1;
        });
        list.replaceChildren();
        note.textContent = rows.length ? (rows.length > 60 ? 'فقط ۶۰ مورد اول نشان داده می‌شود؛ جستجو کنید.' : '') : 'بازیکنی پیدا نشد.';
        rows.slice(0, 60).forEach(function (p) {
          list.append(row({ lead: avatar(p.id, p.name, 'sm'), title: p.name, sub: (p.cls || '') + ' • ' + p.elo,
            tap: function () { back(); onPick(p); } }));
        });
      }
      var t = 0;
      q.addEventListener('input', function () { clearTimeout(t); t = setTimeout(fill, 120); });
      var wrap = h('div', null, h('div', { style: 'margin:0 16px' }, q), list, note);
      if (!C.P().rows) { wrap.append(spin()); C.ensurePlayers(true).then(function () { fill(); wrap.querySelectorAll('.spin').forEach(function (s) { s.remove(); }); }); }
      else fill();
      return { body: wrap };
    });
  }
  /* دکمه‌ی انتخابِ بازیکن که نتیجه را در state نگه می‌دارد */
  function playerField(label, state, key, opt) {
    var btnEl = h('button', { type: 'button', class: 'inp pick', text: state[key] ? state[key].name : 'انتخاب بازیکن…' });
    btnEl.addEventListener('click', function () {
      var ex = opt && opt.exclude ? opt.exclude() : [];
      pickPlayer(label, function (p) { state[key] = p; btnEl.textContent = p.name; btnEl.classList.add('set'); }, { activeOnly: true, exclude: ex });
    });
    return field(label, btnEl);
  }

  var apcT = 0;
  function afterPlayerChange() {
    // رفرشِ فهرستِ بازیکنان و خانه «بعد از» نشستنِ پنلِ بازیکن (قبلاً هم‌زمان با آن و رقیبِ آن بود)
    clearTimeout(apcT);
    apcT = setTimeout(function () { C.ensurePlayers(true); C.refreshBoot(); }, 1200);
  }

  /* ─── پنلِ بازیکن: باز شدنِ فوری ─────────────────────────────────
     panelCache از نوشتن‌ها پاک «نمی‌شود» (فقط پچ می‌شود)؛ پس پنل همیشه همان لحظه با آخرین داده
     باز می‌شود و پشت‌صحنه تازه می‌شود. اگر هنوز چیزی نداریم، از ردیفِ فهرستِ بازیکنان یک سربرگِ فوری می‌سازیم. */
  var panelCache = {}, panelAt = {}, panelKeys = [];
  function putPanel(id, d) {
    if (!(id in panelCache)) { panelKeys.push(id); if (panelKeys.length > 60) delete panelCache[panelKeys.shift()]; }
    panelCache[id] = d; panelAt[id] = Date.now();
  }
  var panelFly = {};
  function fetchPanel(id) {                      // درخواستِ در حالِ پرواز به اشتراک گذاشته می‌شود (warm + باز شدن = یک درخواست)
    if (panelFly[id]) return panelFly[id];
    var pr = api('/hub/api/player/' + id + '/panel').then(function (d) { delete panelFly[id]; putPanel(id, d); return d; },
      function (e) { delete panelFly[id]; throw e; });
    return (panelFly[id] = pr);
  }
  function warmPlayer(id) {                     // با pointerdown روی ردیف صدا زده می‌شود: ~۱۰۰ms زودتر شروع
    if (Date.now() - (panelAt[id] || 0) < 8000) return;
    panelAt[id] = Date.now();
    fetchPanel(id).then(null, function () {});
  }
  function patchPanel(id, patch) {
    delete panelFly[id];              // اپتیمیستیک: بعد از اقدام، پنل همان لحظه درست نشان داده شود
    var row = C.P() && C.P().map && C.P().map.get(id);
    if (row && patch && typeof patch !== 'function') {   // ردیفِ فهرست همیشه پچ می‌شود (حتی اگر پنل هنوز کش نشده)
      if ('status' in patch) row.status = patch.status;
      if ('warnings' in patch) row.warn = patch.warnings;
      if ('elite' in patch) row.elite = patch.elite ? 1 : 0;
      if ('special' in patch) row.special = patch.special ? 1 : 0;
    }
    var d = panelCache[id]; if (!d) return;
    putPanel(id, Object.assign({}, d, typeof patch === 'function' ? patch(d) : patch));
  }
  function quickPanel(id) {                     // سربرگِ فوری از ردیفِ فهرست (پیش از رسیدنِ پنلِ کامل)
    var P = C.P(), r = P && P.map && P.map.get(id);
    if (!r) return null;
    return { id: id, name: r.name, cls: r.cls, status: r.status || 'active', warnings: r.warn || 0, w: r.w || 0, d: r.d || 0, l: r.l || 0,
      elite: !!r.elite, special: !!r.special, elo: r.elo != null ? { rating: r.elo } : null, kick_pending: false,
      warn_log: null, can: {}, _quick: true };
  }

  /* ═══════════ ثبت بازیکن ═══════════ */
  function registerPlayer() {
    push('ثبت‌نام بازیکن', function () {
      return { body: lazy('/hub/api/classes', function (d) {
        if (!d.classes.length) return empty('اول باید یک کلاس بسازید (مدیریت ← کلاس‌ها).');
        var name = input({ maxlength: 60, placeholder: 'نام و نام‌خانوادگی' });
        var cls = select(d.classes.map(function (c) { return [c.id, c.name]; }), d.classes[0].id);
        var teams = null, teamSel = null;
        var kids = [field('نام و نام‌خانوادگی', name), field('کلاس', cls)];
        var wrap = h('div', null);
        var save = actBtn('ثبت بازیکن', '', function () {
          return post('/hub/api/player/create', { name: name.value, class_id: +cls.value, team_id: teamSel && teamSel.value ? +teamSel.value : null });
        }, function (r) {
          toast('«' + r.name + '» در کلاس ' + r.cls + ' ثبت شد'); afterPlayerChange(); name.value = ''; name.focus();
        });
        add(wrap, kids);
        if (feat('team_mode') && can('teams')) {
          var slot = h('div', null); wrap.append(slot);
          api('/hub/api/teams').then(function (t) {
            if (!t.teams.length) return;
            teamSel = select([['', 'بدون تیم']].concat(t.teams.map(function (x) { return [x.id, x.name]; })), '');
            slot.append(field('تیم (اختیاری)', teamSel));
          }, function () {});
        }
        wrap.append(h('div', { style: 'margin:6px 16px 0' }, save));
        return wrap;
      }) };
    });
  }

  /* ═══════ پنلِ بازیکن (بر اساس نقش) ═══════ */
  function openPlayer(id) {
    root('', function () {
      var box = h('div', null), sig = '';
      function paint(d) { sig = JSON.stringify(d); box.replaceChildren(d._quick ? quickBody(d) : playerBody(d)); }
      var have = panelCache[id] || quickPanel(id);
      if (have) paint(have); else box.append(spin());
      fetchPanel(id).then(function (d) {
        if (JSON.stringify(d) !== sig) paint(d);
      }, function (e) {
        if (e && e.data && e.data.error === 'locked') return;
        if (!have) box.replaceChildren(errBox(function () { openPlayer(id); }));
      });
      return { body: box };
    });
  }
  function quickBody(p) {                        // سربرگ + آمار فوری؛ بقیه‌ی پنل که برسد جایگزین می‌شود
    var w = h('div', null), tags = [];
    if (p.elite) tags.push(h('span', { class: 'badge elite' }, ic('star'), ''));
    if (p.special) tags.push(h('span', { class: 'badge special' }, ic('bolt'), ''));
    if (p.status !== 'active') tags.push(h('span', { class: 'badge off', text: STATUS[p.status] || p.status }));
    w.append(h('div', { class: 'p-top' }, avatar(p.id, p.name, 'lg'), h('h3', { text: p.name }),
      p.cls ? h('div', { class: 'p-sub', text: ' ' + p.cls }) : null, tags.length ? h('div', { class: 'tags' }, tags) : null));
    w.append(h('div', { class: 'stat3' },
      h('div', null, h('b', { class: 'num', text: p.elo ? p.elo.rating : '' }), h('span', { text: '' })),
      h('div', null, h('b', { class: 'num', text: p.w + p.d + p.l }), h('span', { text: '' })),
      h('div', null, h('b', { class: 'num' + (p.warnings >= 3 ? ' bad' : ''), text: p.warnings }), h('span', { text: '' }))));
    w.append(spin());
    return w;
  }
  function playerBody(p) {
    var w = h('div', null);
    var tags = [];
    if (p.elite) tags.push(h('span', { class: 'badge elite' }, ic('star'), 'برتر'));
    if (p.special) tags.push(h('span', { class: 'badge special' }, ic('bolt'), 'ویژه'));
    if (p.status !== 'active') tags.push(h('span', { class: 'badge off', text: STATUS[p.status] || p.status }));
    if (p.kick_pending) tags.push(h('span', { class: 'badge off', text: 'درخواست اخراج در انتظار' }));
    w.append(h('div', { class: 'p-top' }, avatar(p.id, p.name, 'lg'), h('h3', { text: p.name }),
      p.cls ? h('div', { class: 'p-sub', text: 'کلاس ' + p.cls }) : null, tags.length ? h('div', { class: 'tags' }, tags) : null));
    var total = p.w + p.d + p.l;
    w.append(h('div', { class: 'stat3' },
      h('div', null, h('b', { class: 'num', text: p.elo ? p.elo.rating : '—' }), h('span', { text: 'امتیاز' })),
      h('div', null, h('b', { class: 'num', text: total }), h('span', { text: 'بازی' })),
      h('div', null, h('b', { class: 'num' + (p.warnings >= 3 ? ' bad' : ''), text: p.warnings }), h('span', { text: 'اخطار' }))));
    if (total) w.append(h('div', { class: 'wdl' },
      h('div', { class: 'wdl-strip' }, p.w ? h('i', { class: 'w', style: 'flex:' + p.w }) : null, p.d ? h('i', { class: 'd', style: 'flex:' + p.d }) : null, p.l ? h('i', { class: 'l', style: 'flex:' + p.l }) : null),
      h('div', { class: 'wdl-legend' }, h('span', null, 'برد ', nn(p.w)), h('span', null, 'تساوی ', nn(p.d)), h('span', null, 'باخت ', nn(p.l)))));

    /* عملیات (فقط چیزی که برای این نقش/دسترسی روشن است) */
    var acts = [];
    var K = p.can;
    if (K.edit) acts.push(['pencil', 'ویرایش نام/کلاس/یادداشت', 'bg-blue', function () { editPlayer(p); }]);
    if (K.predict) acts.push(['bolt', 'پیش‌بینی با حریف', 'bg-violet', function () { predictFor(p); }]);
    if (K.warn) acts.push(['alert', 'ثبت اخطار', 'bg-amber', function () {
      reasonView('اخطار برای ' + p.name, 'دلیل اخطار', 'مثلاً: بی‌احترامی در سالن', function (t) { return post('/hub/api/player/' + p.id + '/warn', { reason: t }); },
        function (r) { toast('اخطار ثبت شد (' + fa(r.warnings) + ' اخطار)'); patchPanel(p.id, { warnings: r.warnings }); afterPlayerChange(); openPlayer(p.id); });
    }]);
    if (K.kick && p.status === 'active') {
      acts.push(['lock', K.kick_direct ? 'اخراج بازیکن' : 'درخواست اخراج', 'bg-red', function () {
        var msg = K.kick_direct ? 'بازیکن «' + p.name + '» بلافاصله اخراج می‌شود.' : 'یک درخواست اخراج برای «' + p.name + '» به مدیر ارشد فرستاده می‌شود.';
        if (p.elite || p.special) msg += '\n⚠️ این بازیکن ' + (p.elite ? 'برتر' : 'ویژه') + ' است.';
        confirmView(K.kick_direct ? 'اخراج' : 'درخواست اخراج', msg, K.kick_direct ? 'اخراج کن' : 'ارسال درخواست',
          function () { return post('/hub/api/player/' + p.id + '/kick', { confirm: true }); },
          function (r) { toast(r.mode === 'direct' ? 'بازیکن اخراج شد' : 'درخواست برای مدیر ارشد ارسال شد'); patchPanel(p.id, r.mode === 'direct' ? { status: 'kicked' } : { kick_pending: true }); afterPlayerChange(); openPlayer(p.id); });
      }]);
      acts.push(['pause', 'تعلیق', 'bg-amber', function () {
        confirmView('تعلیق', '«' + p.name + '» تعلیق می‌شود و در مسابقه‌ها شرکت داده نمی‌شود.', 'تعلیق کن',
          function () { return post('/hub/api/player/' + p.id + '/status', { status: 'suspended' }); },
          function () { toast('تعلیق شد'); patchPanel(p.id, { status: 'suspended' }); afterPlayerChange(); openPlayer(p.id); });
      }]);
    }
    if (K.kick && p.status !== 'active') acts.push(['reset', 'احیا (بازگشت به فعال و پاک‌شدن اخطارها)', 'bg-green', function () {
      confirmView('احیا', '«' + p.name + '» به لیست فعال برمی‌گردد و اخطارهایش صفر می‌شود.', 'احیا کن',
        function () { return post('/hub/api/player/' + p.id + '/status', { status: 'active' }); },
        function () { toast('احیا شد'); patchPanel(p.id, { status: 'active', warnings: 0 }); afterPlayerChange(); openPlayer(p.id); }, false);
    }]);
    if (K.elite) acts.push(['star', p.elite ? 'حذف از برترین‌ها' : 'ثبت به‌عنوان برتر', 'bg-amber', function () {
      post('/hub/api/player/' + p.id + '/flags', { elite: !p.elite }).then(function () { hx.ok(); patchPanel(p.id, { elite: !p.elite }); afterPlayerChange(); openPlayer(p.id); }, fail);
    }]);
    if (K.special) acts.push(['bolt', p.special ? 'حذف از نیروهای ویژه' : 'ثبت به‌عنوان نیروی ویژه', 'bg-red', function () {
      post('/hub/api/player/' + p.id + '/flags', { special: !p.special }).then(function () { hx.ok(); patchPanel(p.id, { special: !p.special }); afterPlayerChange(); openPlayer(p.id); }, fail);
    }]);
    if (K.delete) acts.push(['trash', 'حذف کامل بازیکن', 'bg-red', function () {
      confirmView('حذف کامل', '«' + p.name + '» و همه‌ی مسابقه‌ها و سابقه‌ی اخطارهایش برای همیشه پاک می‌شود. این کار برگشت ندارد.', 'حذف کن',
        function () { return post('/hub/api/player/' + p.id + '/delete', { confirm: true }); },
        function () { toast('بازیکن حذف شد'); afterPlayerChange(); closeAll(); });
    }]);
    if (acts.length) {
      w.append(secTitle('عملیات'), h('div', { class: 'group' }, acts.map(function (a) {
        return row({ lead: riconEl(a[0], a[2]), title: a[1], tap: a[3] });
      })));
    }

    /* انضباطی */
    w.append(secTitle('سابقه‌ی اخطارها', h('small', { class: 'num', text: p.warn_log.length })));
    w.append(p.warn_log.length ? h('div', { class: 'group' }, p.warn_log.map(function (x) {
      var rm = null;
      if (x.removable) rm = h('button', { class: 'btn soft danger', style: 'margin-inline-start:auto;padding:4px 10px', text: 'حذف', onclick: function () {
        confirmView('حذفِ اخطار', 'اخطارِ «' + x.reason + '» از سابقه‌ی «' + p.name + '» حذف می‌شود و یک اخطار کم می‌شود.', 'حذف کن',
          function () { return post('/hub/api/player/' + p.id + '/warn/' + x.id + '/remove'); },
          function (r) { toast('اخطار حذف شد (' + fa(r.warnings) + ' اخطار)'); patchPanel(p.id, { warnings: r.warnings }); afterPlayerChange(); openPlayer(p.id); });
      } });
      return h('div', { class: 'mrow' }, h('span', { class: 'mres p', text: '!' }),
        h('div', { class: 'mtxt' }, h('b', { text: x.reason }), h('small', { text: x.by + ' • ' + x.at })), rm);
    })) : h('div', { class: 'group' }, empty('اخطاری ثبت نشده.')));
    if (K.warn_clear && (p.warn_log.length || p.warnings)) w.append(h('div', { style: 'margin:8px 16px' }, btn('پاک‌کردنِ همه‌ی اخطارها', 'soft', function () {
      confirmView('پاک‌کردنِ همه‌ی اخطارها', 'همه‌ی اخطارهای «' + p.name + '» پاک و شمارنده صفر می‌شود. این کار برگشت ندارد.', 'پاک کن',
        function () { return post('/hub/api/player/' + p.id + '/warn/clear'); },
        function () { toast('همه‌ی اخطارها پاک شد'); patchPanel(p.id, { warnings: 0 }); afterPlayerChange(); openPlayer(p.id); });
    })));

    if (p.best_opp || p.hard_opp) w.append(secTitle('اطلاعات رقابتی'), h('div', { class: 'group' },
      p.best_opp ? kv('بهترین حریف (بیشترین برد)', p.best_opp) : null, p.hard_opp ? kv('سخت‌ترین حریف', p.hard_opp) : null));
    if (p.notes) w.append(secTitle('یادداشت'), h('div', { class: 'group' }, h('div', { class: 'text-block', text: p.notes })));

    if (p.elo) {
      w.append(secTitle('Elo'), h('div', { class: 'group' },
        kv('عنوان', p.elo.title), kv('رتبه بین فعال‌ها', nn(p.elo.rank)), kv('بالاترین امتیاز', nn(p.elo.peak)), kv('بازی‌های محاسبه‌شده', nn(p.elo.games))));
      if (p.elo.history.length) w.append(secTitle('تاریخچه‌ی Elo'), h('div', { class: 'group' }, p.elo.history.map(function (x) {
        return h('div', { class: 'mrow' }, h('span', { class: 'mres ' + (x.change > 0 ? 'w' : x.change < 0 ? 'l' : 'd'), text: x.change > 0 ? '+' : x.change < 0 ? '−' : '=' }),
          h('div', { class: 'mtxt' }, h('b', { text: 'مقابل ' + x.opp }), h('small', { text: x.at })),
          h('b', { class: 'num', text: (x.change > 0 ? '+' : '') + x.change + ' ← ' + x.new }));
      })));
    }
    if (p.teams && p.teams.length) w.append(secTitle('تیم'), h('div', { class: 'group' }, p.teams.map(function (t) { return kv('عضو تیم', t.name); })));
    if (p.last_matches) {
      w.append(secTitle('آخرین بازی‌ها'));
      w.append(p.last_matches.length ? h('div', { class: 'group' }, p.last_matches.map(function (m) {
        var mine = m.wid === p.id ? 'white' : 'black', res;
        if (!m.res) { res = h('span', { class: 'mres p', text: '…' }); }
        else if (m.res === 'draw') res = h('span', { class: 'mres d', text: '=' });
        else if (m.res === 'cancelled') res = h('span', { class: 'mres p', text: '×' });
        else res = h('span', { class: 'mres ' + (m.res === mine ? 'w' : 'l'), text: m.res === mine ? '+' : '−' });
        var el = h(can('match_edit') || can('match_delete') ? 'button' : 'div', { class: 'mrow', type: 'button' }, res,
          h('div', { class: 'mtxt' }, h('b', { text: 'مقابل ' + (m.wid === p.id ? m.b : m.w) }), h('small', { text: m.date })));
        if (el.tagName === 'BUTTON') el.addEventListener('click', function () { matchDetail({ id: m.id, wid: m.wid, w: m.w, b: m.b, res: m.res, date: m.date }); });
        return el;
      })) : h('div', { class: 'group' }, empty('هنوز بازی‌ای ثبت نشده.')));
    }
    return w;
  }

  function editPlayer(p) {
    push('ویرایش ' + p.name, function () {
      return { body: lazy('/hub/api/classes', function (d) {
        var n = input({ maxlength: 60, value: p.name });
        var c = select(d.classes.map(function (x) { return [x.id, x.name]; }), p.class_id);
        var nt = h('textarea', { class: 'inp', rows: 3, maxlength: 400, placeholder: 'یادداشت (اختیاری)' }); nt.value = p.notes || '';
        return h('div', null, field('نام و نام‌خانوادگی', n), field('کلاس', c), field('یادداشت', nt),
          h('div', { style: 'margin:6px 16px 0' }, actBtn('ذخیره', '', function () {
            return post('/hub/api/player/' + p.id + '/edit', { name: n.value, class_id: +c.value, notes: nt.value });
          }, function () {
            toast('ذخیره شد');
            var cn = c.options[c.selectedIndex] ? c.options[c.selectedIndex].text : p.cls;
            patchPanel(p.id, { name: n.value.trim(), class_id: +c.value, cls: cn, notes: nt.value.trim() });
            var row = C.P() && C.P().map && C.P().map.get(p.id); if (row) { row.name = n.value.trim(); row.cls = cn; }
            afterPlayerChange(); back(); openPlayer(p.id);
          })));
      }) };
    });
  }

  /* ═══════════ مسابقه‌ها ═══════════ */
  function matchesList() {
    var st = { scope: 'pending', q: '' };
    root('مسابقه‌ها', function () {
      var listBox = h('div', null);
      var chipsBox = h('div', null);
      var q = input({ type: 'search', placeholder: 'جستجوی نام بازیکن', value: st.q });
      function draw() {
        chipsBox.replaceChildren(chipsBar([['pending', 'منتظر نتیجه'], ['done', 'انجام‌شده'], ['all', 'همه']], st.scope, function (k) { st.scope = k; draw(); }));
        listBox.replaceChildren(spin());
        swr('/hub/api/matches?scope=' + st.scope + '&q=' + encodeURIComponent(st.q), function (d) {
          if (!d.matches.length) { listBox.replaceChildren(empty('مسابقه‌ای پیدا نشد.')); return; }
          listBox.replaceChildren(h('div', { class: 'group' }, d.matches.map(function (m) {
            return row({ title: m.w + ' ⚔️ ' + m.b, sub: (m.res ? RES[m.res] : 'منتظر نتیجه') + ' • ' + (m.date || '') + (m.t ? ' • ' + m.t : ''),
              hot: !m.res, tap: function () { matchDetail(m); } });
          })));
        }, function () { listBox.replaceChildren(errBox(draw)); });
      }
      var t = 0;
      q.addEventListener('input', function () { clearTimeout(t); t = setTimeout(function () { st.q = q.value.trim(); draw(); }, 250); });
      draw();
      return { body: h('div', null, h('div', { style: 'margin:0 16px' }, q), chipsBox, listBox),
        foot: (can('match_create') || can('match_scan')) ? footBtns(
          can('match_create') ? btn('+ ثبت مسابقه‌ی جدید', '', function () { createMatch(); }) : null,
          can('match_scan') ? btn('📷 ثبت با عکس', 'soft', function () { scanStart(); }) : null) : null };
    });
  }

  /* ═══════════ ثبت نتیجه با عکسِ برگه (مدیر ارشد: مستقیم • مدیر مسابقات: با تأییدِ مدیر ارشد) ═══════════ */
  var SCAN_RES = [['white', 'برد سفید'], ['black', 'برد سیاه'], ['draw', 'تساوی'], ['none', 'بدون نتیجه']];

  /* کوچک‌کردنِ عکس در خودِ گوشی (حجمِ آپلود کم و خواندن سریع‌تر): ضلعِ بلند حداکثر ۱۸۰۰، JPEG */
  function prepImage(file) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file), img = new Image();
      img.onload = function () {
        var sc = Math.min(1, 1800 / Math.max(img.naturalWidth, img.naturalHeight));
        var w = Math.max(1, Math.round(img.naturalWidth * sc)), hh = Math.max(1, Math.round(img.naturalHeight * sc));
        var cv = document.createElement('canvas'); cv.width = w; cv.height = hh;
        var cx = cv.getContext('2d'); cx.fillStyle = '#fff'; cx.fillRect(0, 0, w, hh); cx.drawImage(img, 0, 0, w, hh);
        URL.revokeObjectURL(url);
        var data = cv.toDataURL('image/jpeg', 0.85);
        resolve({ data: data, url: data });
      };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error('img')); };
      img.src = url;
    });
  }

  function scanStart() {
    push('ثبت با عکس', function () {
      var shot = null, busy = false;
      var preview = h('div', { style: 'margin:12px 16px 0;text-align:center' });
      var go = h('button', { type: 'button', class: 'btn', text: 'خواندنِ برگه', disabled: true });
      function pick(capture) {
        var f = h('input', { type: 'file', accept: 'image/*', style: 'display:none' });
        if (capture) f.setAttribute('capture', 'environment');
        f.addEventListener('change', function () {
          var file = f.files && f.files[0]; if (!file) return;
          preview.replaceChildren(spin());
          prepImage(file).then(function (r) {
            shot = r; go.disabled = false;
            preview.replaceChildren(h('img', { src: r.url, style: 'max-width:100%;max-height:240px;border-radius:14px;border:1px solid rgba(255,255,255,.12)' }));
          }, function () { shot = null; go.disabled = true; preview.replaceChildren(empty('عکس باز نشد؛ عکس دیگری انتخاب کنید.')); });
        });
        document.body.appendChild(f); f.click(); setTimeout(function () { f.remove(); }, 60000);
      }
      go.addEventListener('click', function () {
        if (busy || !shot) return;
        busy = true; go.disabled = true; go.textContent = 'در حال خواندن… (تا یک دقیقه)';
        post('/hub/api/match/scan', { image: shot.data, mime: 'image/jpeg' }).then(function (rv) {
          busy = false; go.disabled = false; go.textContent = 'خواندنِ برگه'; hx.ok();
          scanReview(rv);
        }, function (e) { busy = false; go.disabled = false; go.textContent = 'خواندنِ برگه'; fail(e); });
      });
      var tips = h('div', { class: 'empty', style: 'text-align:right;padding:6px 18px', text: 'عکسِ صاف و روشن از کلِ برگه بگیرید (بدون سایه). هوش مصنوعی نتیجه‌ها را می‌خواند، ولی هیچ‌چیز بدونِ بازبینیِ شما ثبت نمی‌شود.' + (can('pishva_panel') ? '' : ' بعد از بازبینی، درخواست برای تأییدِ مدیر ارشد ارسال می‌شود و فقط با تأییدِ او ثبت می‌شود.') });
      return { body: h('div', null, tips,
        h('div', { class: 'foot-btns', style: 'margin:8px 16px 0' }, btn('📷 گرفتن عکس', 'soft', function () { pick(true); }), btn('🖼️ از گالری', 'soft', function () { pick(false); })),
        preview), foot: go };
    });
  }

  function scanReview(rv) {
    push('بازبینیِ نتایج', function () {
      var rows = rv.items.map(function (it) {
        var w = it.w.id ? { id: it.w.id, name: it.w.name } : null, b = it.b.id ? { id: it.b.id, name: it.b.name } : null;
        return { i: it.i, it: it, w: w, b: b, res: it.res === 'unknown' ? 'none' : it.res, dup: !!it.dup,
          on: !it.issues.length && !it.dup, done: false, err: '' };
      });
      var date = input({ type: 'date', value: rv.today || todayTehran() });
      var ts = (C.S.boot.tournaments || []).filter(function (t) { return t.status === 'active'; });
      var tsel = select([['', 'تورنمنتِ پیش‌فرض']].concat(ts.map(function (t) { return [t.id, t.name]; })), '');
      var list = h('div', null), cnt = h('div', { class: 'empty', style: 'padding:4px 18px' });
      var sub = btn('', '', function () {});
      var working = false, direct = rv.direct !== false;

      function ready(r) { return !r.done && r.on && r.w && r.b && r.w.id !== r.b.id; }
      function refreshFoot() {
        var n = rows.filter(ready).length, left = rows.filter(function (r) { return !r.done; }).length;
        sub.textContent = n ? (direct ? 'ثبتِ ' + fa(n) + ' مسابقه' : 'ارسال ' + fa(n) + ' مسابقه برای تأییدِ مدیر ارشد') : 'مسابقه‌ای برای ثبت انتخاب نشده';
        sub.disabled = working || !n;
        cnt.textContent = fa(rows.length) + ' ردیف خوانده شد • ' + fa(left) + ' باقی‌مانده' + (rv.date_text ? ' • تاریخِ روی برگه: ' + rv.date_text : '') + (rv.sheet_note ? ' • ' + rv.sheet_note : '');
      }
      function sideCell(r, side, label) {
        var cur = r[side], raw = r.it[side + '_raw'], cands = r.it[side].cands || [];
        var pickBtn = h('button', { type: 'button', class: 'inp pick' + (cur ? ' set' : ''), text: cur ? cur.name : (raw ? '؟ ' + raw : 'انتخاب بازیکن') });
        pickBtn.addEventListener('click', function () {
          var ex = r[side === 'w' ? 'b' : 'w']; 
          pickPlayer(label, function (p) { r[side] = { id: p.id, name: p.name }; r.on = true; draw(); }, { activeOnly: true, exclude: ex ? [ex.id] : [] });
        });
        var wrap = h('div', { style: 'margin-top:6px' }, h('div', { class: 'p-sub', style: 'font-size:12px;opacity:.7;margin-bottom:3px', text: label + (raw ? ' — روی برگه: «' + raw + '»' : '') }), pickBtn);
        if (!cur && cands.length) {
          wrap.append(h('div', { class: 'chips', style: 'margin-top:6px' }, cands.map(function (c) {
            return h('button', { type: 'button', class: 'chip', text: c.name + (c.cls ? ' (' + c.cls + ')' : ''),
              onclick: function () { r[side] = { id: c.id, name: c.name }; r.on = true; hx.sel(); draw(); } });
          })));
        }
        return wrap;
      }
      function card(r) {
        var bad = !r.w || !r.b || (r.w && r.b && r.w.id === r.b.id);
        var sel = select(SCAN_RES, r.res);
        sel.addEventListener('change', function () { r.res = sel.value; });
        var head = h('div', { style: 'display:flex;justify-content:space-between;align-items:center;gap:8px' },
          h('b', { text: '#' + fa(r.i + 1) + (r.it.raw_result ? '  ( ' + r.it.raw_result + ' )' : '') }),
          h('button', { type: 'button', class: 'chip' + (r.on ? ' on' : ''), text: r.on ? '✓ ثبت می‌شود' : 'رد شده',
            onclick: function () { r.on = !r.on; hx.sel(); draw(); } }));
        var c = h('div', { class: 'group', style: 'margin:10px 16px;padding:12px;' + (r.on ? '' : 'opacity:.55') }, head,
          sideCell(r, 'w', '⬜ سفید'), sideCell(r, 'b', '⬛ سیاه'), h('div', { style: 'margin-top:8px' }, sel));
        if (r.dup) c.append(h('div', { class: 'empty', style: 'padding:6px 0 0;color:#e0a030', text: '⚠️ این دو نفر امروز با همین رنگ مسابقه‌ی ثبت‌شده دارند (احتمالاً تکراری).' }));
        if (r.it.note) c.append(h('div', { class: 'empty', style: 'padding:6px 0 0', text: 'ℹ️ ' + r.it.note }));
        if (r.w && r.b && r.w.id === r.b.id) c.append(h('div', { class: 'empty', style: 'padding:6px 0 0;color:#e05050', text: '⛔ سفید و سیاه یک نفر است.' }));
        if (r.err) c.append(h('div', { class: 'empty', style: 'padding:6px 0 0;color:#e05050', text: '⛔ ' + r.err }));
        if (r.res === 'none' && r.it.res === 'unknown') c.append(h('div', { class: 'empty', style: 'padding:6px 0 0', text: 'نتیجه خوانا نبود؛ انتخاب کنید یا «بدون نتیجه» بماند.' }));
        return c;
      }
      function draw() {
        list.replaceChildren.apply(list, rows.filter(function (r) { return !r.done; }).map(card));
        if (!rows.some(function (r) { return !r.done; })) list.replaceChildren(empty('همه‌ی ردیف‌ها ثبت شدند.'));
        refreshFoot();
      }

      sub.addEventListener('click', function () {
        var todo = rows.filter(ready);
        if (working || !todo.length) return;
        if (!direct) {
          /* مدیر مسابقات: یک درخواستِ کامل برای مدیر ارشد؛ تا تأیید نشود هیچ مسابقه‌ای ثبت نمی‌شود */
          working = true; sub.disabled = true; sub.textContent = 'در حال ارسال…';
          post('/hub/api/match/scan/commit', {
            date: date.value, tournament_id: tsel.value ? +tsel.value : null,
            items: todo.map(function (r) { return { i: r.i, white_id: r.w.id, black_id: r.b.id, result: r.res === 'none' ? null : r.res }; })
          }).then(function (res) {
            working = false; hx.ok(); dirty();
            toast(fa(res.count || todo.length) + ' مسابقه برای تأییدِ مدیر ارشد ارسال شد'); back();
          }, function (e) { working = false; refreshFoot(); fail(e); });
          return;
        }
        working = true; sub.disabled = true; sub.textContent = 'در حال ثبت…';
        var created = 0, failedN = 0, i = 0;
        rows.forEach(function (r) { r.err = ''; });
        (function next() {
          if (i >= todo.length) {
            working = false; dirty(); C.refreshBoot(); afterPlayerChange();
            if (!failedN) { hx.ok(); toast(fa(created) + ' مسابقه ثبت شد'); back(); }
            else { hx.err(); toast(fa(created) + ' ثبت شد، ' + fa(failedN) + ' ناموفق (زیرِ همان ردیف‌ها)'); draw(); }
            return;
          }
          var chunk = todo.slice(i, i + 20); i += 20;
          post('/hub/api/match/scan/commit', {
            date: date.value, tournament_id: tsel.value ? +tsel.value : null,
            items: chunk.map(function (r) { return { i: r.i, white_id: r.w.id, black_id: r.b.id, result: r.res === 'none' ? null : r.res }; })
          }).then(function (res) {
            var bad = {}; (res.failed || []).forEach(function (f) { bad[f.i] = f.message; });
            chunk.forEach(function (r) { if (r.i in bad) { r.err = bad[r.i]; failedN++; } else { r.done = true; created++; } });
            next();
          }, function (e) {
            chunk.forEach(function (r) { r.err = errMsg(e); failedN++; });
            next();
          });
        })();
      });

      draw();
      return { body: h('div', null, cnt, h('div', { style: 'margin:0 16px' }, field('تاریخِ ثبت', date), field('تورنمنت', tsel)), list), foot: sub };
    });
  }

  function createMatch(pre) {
    push('ثبت مسابقه', function () {
      var s = { w: pre && pre.w || null, b: pre && pre.b || null };
      var date = input({ type: 'date', value: todayTehran() });
      var ts = (C.S.boot.tournaments || []).filter(function (t) { return t.status === 'active'; });
      var tsel = select([['', 'تورنمنتِ پیش‌فرض']].concat(ts.map(function (t) { return [t.id, t.name]; })), '');
      var wf = playerField('بازیکنِ سفید', s, 'w', { exclude: function () { return s.b ? [s.b.id] : []; } });
      var bf = playerField('بازیکنِ سیاه', s, 'b', { exclude: function () { return s.w ? [s.w.id] : []; } });
      if (s.w) wf.querySelector('.pick').textContent = s.w.name;
      if (s.b) bf.querySelector('.pick').textContent = s.b.name;
      return { body: h('div', null, wf, bf, field('تاریخ', date), field('تورنمنت', tsel)),
        foot: actBtn('ثبت مسابقه', '', function () {
          if (!s.w || !s.b) { var e = new Error('x'); e.data = { message: 'هر دو بازیکن را انتخاب کنید.' }; throw e; }
          return post('/hub/api/match/create', { white_id: s.w.id, black_id: s.b.id, date: date.value, tournament_id: tsel.value ? +tsel.value : null });
        }, function () { toast('مسابقه ثبت شد'); C.refreshBoot(); back(); }) };
    });
  }

  function matchDetail(m) {
    push('جزئیات مسابقه', function () {
      var canEdit = can('match_edit'), canDel = can('match_delete');
      var w = h('div', null);
      var cur = { res: m.res || null };
      var head = h('div', { class: 'group' }, kv('سفید ⬜', m.w), kv('سیاه ⬛', m.b), kv('نتیجه', cur.res ? RES[cur.res] : 'منتظر نتیجه'), kv('تاریخ', m.date || '—'));
      w.append(head);
      function setRes(v, reason) {
        return post('/hub/api/match/' + m.id + '/edit', { result: v, reason: reason || '' }).then(function () {
          hx.ok(); toast('ذخیره شد'); C.refreshBoot(); afterPlayerChange(); m.res = v; back();
        });
      }
      if (canEdit) {
        w.append(secTitle('ثبت / اصلاح نتیجه'));
        var g = h('div', { class: 'group' });
        [['white', m.w + ' برد', 'bg-green'], ['draw', 'تساوی', 'bg-blue'], ['black', m.b + ' برد', 'bg-green']].forEach(function (o) {
          g.append(row({ lead: riconEl(o[0] === 'draw' ? 'pause' : 'trophy', o[2]), title: o[1], sub: cur.res === o[0] ? 'نتیجه‌ی فعلی' : null, hot: cur.res === o[0],
            tap: function () {
              if (cur.res === o[0]) return;
              if (o[0] === 'draw') { reasonView('علت تساوی', 'علت (اختیاری، مثل «توافقی»)', 'توافقی', function (t) { return post('/hub/api/match/' + m.id + '/edit', { result: 'draw', reason: t }); }, function () { toast('ذخیره شد'); C.refreshBoot(); afterPlayerChange(); back(); }, 1); return; }
              confirmView('تأیید نتیجه', (cur.res ? 'نتیجه‌ی قبلی اصلاح و آمار/Elo دوباره محاسبه می‌شود.\n' : '') + 'نتیجه: ' + o[1], 'ثبت',
                function () { return post('/hub/api/match/' + m.id + '/edit', { result: o[0] }); }, function () { toast('ذخیره شد'); C.refreshBoot(); afterPlayerChange(); back(); }, false);
            } }));
        });
        g.append(row({ lead: riconEl('trash', 'bg-red'), title: 'لغو مسابقه', tap: function () {
          reasonView('لغو مسابقه', 'دلیل لغو', 'مثلاً: غیبت', function (t) { return post('/hub/api/match/' + m.id + '/edit', { result: 'cancelled', reason: t }); },
            function () { toast('مسابقه لغو شد'); C.refreshBoot(); afterPlayerChange(); back(); });
        } }));
        if (cur.res) g.append(row({ lead: riconEl('reset', 'bg-amber'), title: 'برگرداندن به «منتظر نتیجه»', tap: function () {
          confirmView('برگرداندن', 'نتیجه پاک و آمار دو بازیکن و Elo اصلاح می‌شود.', 'برگردان', function () { return post('/hub/api/match/' + m.id + '/edit', { result: null }); },
            function () { toast('انجام شد'); C.refreshBoot(); afterPlayerChange(); back(); }, false);
        } }));
        w.append(g);
        var d = input({ type: 'date', value: m.date || todayTehran() });
        w.append(secTitle('تاریخ'), field('تاریخ مسابقه', d),
          h('div', { style: 'margin:0 16px' }, actBtn('ذخیره‌ی تاریخ', 'soft', function () { return post('/hub/api/match/' + m.id + '/edit', { date: d.value }); },
            function () { toast('تاریخ ذخیره شد'); m.date = d.value; C.refreshBoot(); })));
      }
      var foot = null;
      if (canDel) foot = btn('حذف مسابقه', 'danger', function () {
        confirmView('حذف مسابقه', 'این مسابقه حذف می‌شود' + (m.res ? ' و اثرش از آمار و Elo برداشته می‌شود' : '') + '. (در پیگیری اقدامات ثبت می‌شود.)', 'حذف کن',
          function () { return post('/hub/api/match/' + m.id + '/delete', { confirm: true }); },
          function () { toast('مسابقه حذف شد'); C.refreshBoot(); afterPlayerChange(); back(); });
      });
      return { body: w, foot: foot };
    });
  }

  /* ═══════════ پیش‌بینی ═══════════ */
  function predictFor(p) { predict(p ? { id: p.id, name: p.name } : null); }
  function predict(pre) {
    root('پیش‌بینی مسابقه', function () {
      var s = { w: pre || null, b: null };
      var out = h('div', null);
      var wf = playerField('بازیکنِ سفید', s, 'w', { exclude: function () { return s.b ? [s.b.id] : []; } });
      var bf = playerField('بازیکنِ سیاه', s, 'b', { exclude: function () { return s.w ? [s.w.id] : []; } });
      if (s.w) wf.querySelector('.pick').textContent = s.w.name;
      var go = actBtn('پیش‌بینی کن', '', function () {
        if (!s.w || !s.b) { var e = new Error('x'); e.data = { message: 'دو بازیکن را انتخاب کنید.' }; throw e; }
        return api('/hub/api/predict?white=' + s.w.id + '&black=' + s.b.id);
      }, function (d) {
        var total = d.h2h.white + d.h2h.black + d.h2h.draw;
        out.replaceChildren(secTitle('احتمال پیروزی'),
          h('div', { class: 'group pred' },
            h('div', { class: 'pred-names' }, h('b', { text: '⬜ ' + d.white.name }), h('b', { text: d.black.name + ' ⬛' })),
            h('div', { class: 'pred-bar' }, h('i', { class: 'a', style: 'flex:' + Math.max(d.p_white, 1) }, h('span', { class: 'num', text: d.p_white + '٪' })),
              h('i', { class: 'b', style: 'flex:' + Math.max(d.p_black, 1) }, h('span', { class: 'num', text: d.p_black + '٪' }))),
            kv('Elo سفید', d.white.elo + ' — ' + d.white.title), kv('Elo سیاه', d.black.elo + ' — ' + d.black.title),
            kv('رویارویی‌های قبلی', total ? d.white.name + ' ' + fa(d.h2h.white) + ' | ' + d.black.name + ' ' + fa(d.h2h.black) + ' | تساوی ' + fa(d.h2h.draw) : 'اولین رویارویی این دو')),
          can('match_create') ? h('div', { style: 'margin:14px 16px 0' }, btn('ثبت این مسابقه', 'soft', function () { createMatch({ w: s.w, b: s.b }); })) : null);
      });
      return { body: h('div', null, wf, bf, h('div', { style: 'margin:4px 16px 0' }, go), out) };
    });
  }

  /* ═══════════ Elo ═══════════ */
  function eloBoard() {
    root('جدول Elo', function () {
      return { body: lazy('/hub/api/elo/leaderboard?limit=100', function (d) {
        if (!d.rows.length) return empty('هنوز امتیازی محاسبه نشده؛ بعد از ثبت اولین نتیجه پر می‌شود.');
        return h('div', { class: 'group' }, d.rows.map(function (r, i) {
          return row({ lead: h('span', { class: 'rank num' + (i < 3 ? ' top' : ''), style: 'width:24px;text-align:center', text: i + 1 }),
            title: r.name, sub: (r.cls ? r.cls + ' • ' : '') + r.title + ' • ' + r.games + ' بازی', end: h('span', { class: 'elo num', text: r.elo }),
            tap: function () { if (can('players_view')) openPlayer(r.id); } });
        }));
      }) };
    });
  }

  /* ═══════════ کلاس‌ها ═══════════ */
  var STYLES = [['none', 'بی‌رنگ'], ['primary', 'آبی'], ['success', 'سبز'], ['danger', 'قرمز']];
  function classesView() {
    root('کلاس‌ها', function () {
      return { body: lazy('/hub/api/classes', function (d) {
        var w = h('div', null);
        w.append(h('div', { class: 'group' }, d.classes.length ? d.classes.map(function (c) {
          return row({ title: 'کلاس ' + c.name, sub: c.count + ' بازیکن', tap: function () { classDetail(c); } });
        }) : [empty('کلاسی ثبت نشده.')]));
        return w;
      }), foot: btn('+ کلاس جدید', '', function () {
        push('کلاس جدید', function () {
          var n = input({ maxlength: 30, placeholder: 'مثلاً ۹۰۱' });
          return { body: field('نام کلاس', n), foot: actBtn('ثبت', '', function () { return post('/hub/api/class/create', { name: n.value }); },
            function () { toast('کلاس ثبت شد'); back(); refresh(); }) };
        });
      }) };
    });
  }
  function classDetail(c) {
    push('کلاس ' + c.name, function () {
      var n = input({ maxlength: 30, value: c.name });
      var st = select(STYLES, c.style);
      var w = h('div', null, field('نام کلاس', n),
        h('div', { style: 'margin:0 16px 12px' }, actBtn('ذخیره‌ی نام', 'soft', function () { return post('/hub/api/class/' + c.id + '/rename', { name: n.value }); },
          function () { toast('نام ذخیره شد'); c.name = n.value.trim(); })),
        field('رنگ دکمه‌ی کلاس در ربات', st),
        h('div', { style: 'margin:0 16px 12px' }, actBtn('ذخیره‌ی رنگ', 'soft', function () { return post('/hub/api/class/' + c.id + '/style', { style: st.value }); },
          function () { toast('رنگ ذخیره شد'); c.style = st.value; })));
      return { body: w, foot: btn('حذف کلاس', 'danger', function () {
        confirmView('حذف کلاس', c.count ? 'این کلاس ' + fa(c.count) + ' بازیکن دارد؛ اول باید آن‌ها را به کلاس دیگری منتقل کنید. برای امتحان ادامه بدهید.' : 'کلاس «' + c.name + '» حذف می‌شود.', 'حذف کن',
          function () { return post('/hub/api/class/' + c.id + '/delete'); }, function () { toast('کلاس حذف شد'); back(); back(); classesView(); });
      }) };
    });
  }

  window.__HubManageP1 = { init: function (core) {
    C = core; h = core.h; ic = core.ic; hx = core.hx; Sheet = core.Sheet; toast = core.toast; api = core.api;
    row = core.row; riconEl = core.riconEl; secTitle = core.secTitle; kv = core.kv; nn = core.nn; avatar = core.avatar; norm = core.norm;
    return { can: can, feat: feat, push: push, root: root, back: back, refresh: refresh, dirty: dirty, closeAll: closeAll, lazy: lazy, swr: swr, prefetch: prefetch, warmPlayer: warmPlayer, empty: empty, spin: spin,
      field: field, input: input, select: select, btn: btn, actBtn: actBtn, footBtns: footBtns, toggleRow: toggleRow, chipsBar: chipsBar,
      confirmView: confirmView, reasonView: reasonView, pickPlayer: pickPlayer, post: post, fail: fail, errBox: errBox, fa: fa,
      registerPlayer: registerPlayer, openPlayer: openPlayer, matchesList: matchesList, scanStart: scanStart, createMatch: createMatch, matchDetail: matchDetail,
      predict: predict, eloBoard: eloBoard, classesView: classesView, afterPlayerChange: afterPlayerChange, todayTehran: todayTehran, add: add };
  } };
})();
