/* سی ام اس — مینی‌اپ مدیران (بدونِ فریم‌ورک؛ کوچک و سریع)
   قواعدِ کارایی: هیچ innerHTML با داده‌ی کاربر (همه با textContent)، لیست‌های بلند
   تکه‌تکه رندر می‌شوند، فقط transform/opacity انیمیت می‌شود، داده‌ها ابتدا از
   کشِ محلی نشان داده می‌شوند و در پس‌زمینه تازه می‌شوند. ساعت (clock.js) تنبل بار می‌شود. */
(function () {
  'use strict';

  var tg = window.Telegram && window.Telegram.WebApp;
  var doc = document;
  var V = window.__V || '0';
  var NS = 'http://www.w3.org/2000/svg';

  /* ─── ابزارها ─────────────────────────────────────────────── */
  function $(s, r) { return (r || doc).querySelector(s); }

  function add(node, kids) {
    for (var i = 0; i < kids.length; i++) {
      var c = kids[i];
      if (c == null || c === false) continue;
      if (Array.isArray(c)) add(node, c);
      else node.append(c.nodeType ? c : doc.createTextNode(String(c)));
    }
  }
  function h(tag, attrs) {
    var n = doc.createElement(tag);
    if (attrs) for (var k in attrs) {
      var v = attrs[k];
      if (v == null || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k === 'style') n.style.cssText = v;
      else if (k.slice(0, 2) === 'on') n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : v);
    }
    add(n, Array.prototype.slice.call(arguments, 2));
    return n;
  }
  function ic(name, cls) {
    var s = doc.createElementNS(NS, 'svg');
    s.setAttribute('class', 'ic' + (cls ? ' ' + cls : ''));
    var u = doc.createElementNS(NS, 'use');
    u.setAttribute('href', '#i-' + name);
    s.appendChild(u);
    return s;
  }
  function num(n) { return String(n == null ? 0 : n); }
  function nn(n) { return h('span', { class: 'num', text: num(n) }); }

  var hx = {
    tap: function () { try { tg.HapticFeedback.impactOccurred('light'); } catch (e) {} },
    sel: function () { try { tg.HapticFeedback.selectionChanged(); } catch (e) {} },
    ok: function () { try { tg.HapticFeedback.notificationOccurred('success'); } catch (e) {} },
    warn: function () { try { tg.HapticFeedback.notificationOccurred('warning'); } catch (e) {} },
    err: function () { try { tg.HapticFeedback.notificationOccurred('error'); } catch (e) {} }
  };

  var LS = {
    get: function (k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  };

  function norm(s) {
    return String(s || '').toLowerCase()
      .replace(/[يى]/g, 'ی').replace(/ك/g, 'ک')
      .replace(/[\u064B-\u065F\u0670\u200c\u200d\u200e\u200f]/g, '')
      .replace(/[٠-٩]/g, function (d) { return d.charCodeAt(0) - 1632; })
      .replace(/[۰-۹]/g, function (d) { return d.charCodeAt(0) - 1776; })
      .replace(/\s+/g, ' ').trim();
  }

  /* زمان: سرور ISO ِ «ساده» می‌فرستد؛ تفاضل‌ها را نسبت به ساعتِ خودِ سرور (now) می‌گیریم */
  function pd(iso) { return new Date(String(iso).replace(' ', 'T').replace(/(\.\d{3})\d+/, '$1')); }
  var relFmt = window.Intl && Intl.RelativeTimeFormat ? new Intl.RelativeTimeFormat('fa', { numeric: 'auto' }) : null;
  function rel(iso, nowIso) {
    try {
      var s = (pd(nowIso) - pd(iso)) / 1000;
      if (!isFinite(s)) return '';
      if (s < 60) return 'همین حالا';
      if (!relFmt) return '';
      if (s < 3600) return relFmt.format(-Math.round(s / 60), 'minute');
      if (s < 86400) return relFmt.format(-Math.round(s / 3600), 'hour');
      return relFmt.format(-Math.round(s / 86400), 'day');
    } catch (e) { return ''; }
  }
  function jdate(iso, opts) {
    try { return new Intl.DateTimeFormat('fa-IR-u-ca-persian', opts || { year: 'numeric', month: 'long', day: 'numeric' }).format(pd(iso)); }
    catch (e) { return ''; }
  }

  var PAL = [['#ff885e', '#ff516a'], ['#ffcd6a', '#ffa85c'], ['#82b1ff', '#665fff'], ['#a0de7e', '#54cb68'],
             ['#53edd6', '#28c9b7'], ['#72d5fd', '#2a9ef1'], ['#e0a2f3', '#d669ed']];
  function avatar(id, name, size, src) {
    var c = PAL[Math.abs(+id || 0) % PAL.length];
    var t = String(name || '').trim();
    var n = h('div', { class: 'av' + (size ? ' ' + size : ''), style: 'background:linear-gradient(135deg,' + c[0] + ',' + c[1] + ')' },
      t ? Array.from(t)[0].toUpperCase() : '؟');
    if (src) {
      var im = h('img', { src: src, alt: '', loading: 'lazy', decoding: 'async' });
      im.addEventListener('error', function () { im.remove(); });
      n.append(im);
    }
    return n;
  }

  /* ─── تلگرام ───────────────────────────────────────────────── */
  var HERO_HEX = '#b3121c';
  function initTelegram() {
    if (!tg) return;
    try { tg.ready(); } catch (e) {}
    try { tg.expand(); } catch (e) {}
    try { tg.disableVerticalSwipes(); } catch (e) {}
    // تمام‌صفحه‌ی واقعی (مثل ویب‌چس): هدر تلگرام جمع می‌شود. فقط در Bot API
    // 8.0+ موجوده، expand() بالا به‌عنوان fallback همیشه اجرا شده.
    // تمام‌صفحه‌ی واقعی (مثل ویب‌چس): هدر تلگرام جمع می‌شود. فقط در Bot API
    // 8.0+ موجوده؛ expand() بالا به‌عنوان fallback همیشه اجرا شده. متغیرهای
    // --tg-content-safe-area-inset-* را خودِ تلگرام ست می‌کند و hub.css از
    // قبل از همان‌ها استفاده می‌کند، پس نیازی به کدِ اضافه نیست.
    try {
      if (tg.isVersionAtLeast && tg.isVersionAtLeast('8.0') && typeof tg.requestFullscreen === 'function') {
        tg.requestFullscreen();
      }
    } catch (e) {}
    try { tg.BackButton.onClick(function () { var f = backStack[backStack.length - 1]; if (f) f(); }); } catch (e) {}
    try { tg.onEvent('themeChanged', chrome); } catch (e) {}
    chrome();
  }
  function chrome() {
    if (!tg) return;
    try { tg.setHeaderColor(cur === 'home' ? HERO_HEX : 'bg_color'); } catch (e) {}
    try { tg.setBackgroundColor('bg_color'); } catch (e) {}
    try { tg.setBottomBarColor('bottom_bar_bg_color'); } catch (e) {}
  }
  var backStack = [];
  function pushBack(fn) { backStack.push(fn); try { tg.BackButton.show(); } catch (e) {} }
  function popBack() { backStack.pop(); if (!backStack.length) { try { tg.BackButton.hide(); } catch (e) {} } }

  var toastT = 0;
  function toast(msg) {
    var t = $('#toast');
    $('span', t).textContent = msg;
    t.classList.add('on');
    clearTimeout(toastT);
    toastT = setTimeout(function () { t.classList.remove('on'); }, 2200);
  }

  /* ─── شبکه ─────────────────────────────────────────────────── */
  function api(path, body) {
    var opt = { headers: { 'X-Tg-Init-Data': (tg && tg.initData) || '' } };
    if (body) { opt.method = 'POST'; opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    return fetch(path, opt).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return null; }).then(function (data) {
          var e = new Error('http'); e.code = r.status; e.data = data;
          if (r.status === 403 && data && data.error === 'locked') gate(403, data);
          throw e;
        });
      }
      return r.json().then(function (j) { if (body && window.__hubOnWrite) window.__hubOnWrite(); return j; });
    });
  }

  /* ─── وضعیت ───────────────────────────────────────────────── */
  var S = { boot: null };
  function can(c) { return !!(S.boot && S.boot.caps && S.boot.caps.indexOf(c) > -1); }
  function canAny() { for (var i = 0; i < arguments.length; i++) if (can(arguments[i])) return true; return false; }
  var MATCH_CAPS = ['match_create', 'match_edit', 'match_delete', 'predictions'];
  function hasMatchCaps() { return canAny.apply(null, MATCH_CAPS); }
  var uid = (tg && tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.id) || 0;
  var K_BOOT = 'hub:boot:' + uid, K_PL = 'hub:players:' + uid;
  var cur = 'home';
  var built = {};
  var scrollPos = {};

  /* ─── شیتِ پایین ──────────────────────────────────────────── */
  var Sheet = (function () {
    var root = $('#sheet-root'), sh = $('.sheet', root), body = $('.sheet-body', root), foot = $('.sheet-foot', root);
    var ttl = $('.sheet-title', root), grab = $('.grab', root), bd = $('.backdrop', root);
    var isOpen = false, onClose = null, timer = 0;

    function open(o) {
      clearTimeout(timer);
      ttl.textContent = o.title || '';
      body.replaceChildren(o.body);
      foot.replaceChildren();
      if (o.foot) foot.append(o.foot);
      body.scrollTop = 0;
      onClose = o.onClose || null;
      if (!isOpen) {
        isOpen = true;
        void sh.offsetHeight;
        root.classList.add('on');
        pushBack(close);
      }
    }
    function close() {
      if (!isOpen) return;
      isOpen = false;
      root.classList.remove('on');
      popBack();
      var cb = onClose; onClose = null;
      timer = setTimeout(function () { body.replaceChildren(); foot.replaceChildren(); }, 380);
      if (cb) cb();
    }
    bd.addEventListener('click', close);

    var sy = 0, dy = 0, drag = false, t0 = 0;
    grab.addEventListener('pointerdown', function (e) {
      drag = true; sy = e.clientY; dy = 0; t0 = performance.now();
      root.classList.add('drag');
      try { grab.setPointerCapture(e.pointerId); } catch (x) {}
    });
    grab.addEventListener('pointermove', function (e) {
      if (!drag) return;
      dy = Math.max(0, e.clientY - sy);
      sh.style.transform = 'translateY(' + dy + 'px)';
    });
    function end() {
      if (!drag) return;
      drag = false;
      root.classList.remove('drag');
      var v = dy / Math.max(1, performance.now() - t0);
      sh.style.transform = '';
      if (dy > 110 || v > 0.6) close();
    }
    grab.addEventListener('pointerup', end);
    grab.addEventListener('pointercancel', end);

    return { open: open, close: close, set: function (n) { body.replaceChildren(n); },
             get isOpen() { return isOpen; } };
  })();

  /* ─── نقش‌ها و اجزای مشترک ────────────────────────────────── */
  function roleChip(m) {
    var isP = m.role === 'pishva';
    return h('span', { class: 'me-role' + (isP ? '' : ' plain') }, isP ? ic('crown') : null, m.role_label || 'مدیر');
  }
  function kv(k, v) { return h('div', { class: 'kv' }, h('span', { text: k }), h('b', null, v)); }
  function secTitle(t, extra) { return h('div', { class: 'sec-title' }, h('span', { text: t }), extra || null); }
  function row(opts) {
    var el = h(opts.tap ? 'button' : 'div', { class: 'row', onclick: opts.tap || null, type: opts.tap ? 'button' : null },
      opts.lead || null,
      h('div', { class: 'r-main' },
        h('div', { class: 'r-t' }, opts.title),
        opts.sub != null ? h('div', { class: 'r-s' + (opts.hot ? ' hot' : '') }, opts.sub) : null),
      opts.end || opts.tap ? h('div', { class: 'r-end' }, opts.end || null, opts.tap ? ic('chev', 'chev') : null) : null);
    return el;
  }
  function riconEl(name, bg) { return h('div', { class: 'r-ic ' + bg }, ic(name)); }

  /* ─── خانه ────────────────────────────────────────────────── */
  function renderHome() {
    var B = S.boot; if (!B) return;
    var sm = B.summary, m = sm.matches;
    $('#hello').textContent = 'سلام، ' + (B.me.name || 'مدیر');
    var sub;
    if (m.pending > 0) sub = m.pending + ' مسابقه منتظر ثبت نتیجه است' + (m.done_today ? ' و امروز ' + m.done_today + ' نتیجه ثبت شده.' : '.');
    else if (m.done_today > 0) sub = 'همه‌چیز به‌روز است؛ امروز ' + m.done_today + ' نتیجه ثبت شده.';
    else sub = 'همه‌چیز به‌روز است. مسابقه‌ی بازی نداریم.';
    $('#hello-sub').textContent = hasMatchCaps() ? sub : 'به پنل مدیریت خوش آمدید.';

    var body = $('#home-body');
    var frag = doc.createDocumentFragment();

    /* میانبرها */
    function q(label, icon, cls, fn) {
      return h('button', { type: 'button', onclick: fn }, h('span', { class: 'q-ic ' + (cls || '') }, ic(icon)), label);
    }
    var hasP = canAny('players_view', 'player_register'), hasT = hasMatchCaps();
    var quick = [];
    if (hasP) quick.push(q('بازیکنان', 'users', '', function () { go('players', { f: 'all' }); }));
    if (hasT) quick.push(q('مسابقات', 'trophy', '', function () { go('tours'); }));
    if (hasP) quick.push(q('برترین‌ها', 'crown', 'gold', function () { go('players', { f: 'elite' }); }));
    if (hasP) quick.push(q('نیروهای ویژه', 'bolt', '', function () { go('players', { f: 'special' }); }));
    if (!quick.length) quick.push(q('مدیریت', 'gear', '', function () { go('manage'); }));
    if (can('calendar')) quick.push(q('تقویم', 'calendar', '', function () { withManage(function (mm) { mm.calendarView(); }); }));
    if (can('comms')) quick.push(q('مخابرات', 'chat', '', function () { withManage(function (mm) { mm.commsView(); }); }));
    frag.append(h('nav', { class: 'quick', 'aria-label': 'میانبرها' }, quick.slice(0, 4)));
    frag.append(weatherCard());

    /* خلاصه */
    var pending = m.pending > 0
      ? m.pending + ' مسابقه باز' + (m.oldest_pending_days ? '، قدیمی‌ترینش ' + m.oldest_pending_days + ' روز' : '')
      : 'مسابقه‌ی بازی نداریم';
    var sumRows = [];
    if (hasT) {
      sumRows.push(row({ lead: riconEl('hourglass', 'bg-amber'), title: 'منتظر ثبت نتیجه', sub: pending, hot: m.pending > 0, tap: function () { go('tours'); } }),
        row({ lead: riconEl('trophy', 'bg-blue'), title: 'مسابقات فعال',
              sub: sm.tournaments.active + ' فعال از ' + sm.tournaments.total, hot: sm.tournaments.active > 0, tap: function () { go('tours'); } }));
    }
    sumRows.push(row({ lead: riconEl('users', 'bg-green'), title: 'بازیکنان',
              sub: sm.players.active + ' فعال، ' + sm.players.elite + ' برتر، ' + sm.players.special + ' ویژه',
              tap: hasP ? function () { go('players', { f: 'all' }); } : null }));
    frag.append(secTitle('خلاصه'), h('div', { class: 'group' }, sumRows));

    /* روندها */
    if (hasT && B.trend && B.trend.days.length) frag.append(secTitle('روند ۷ روز اخیر', h('small', { text: 'نتیجه‌های ثبت‌شده' })), trendCard(B.trend));

    /* برترین‌ها */
    if (B.top && B.top.length) {
      frag.append(secTitle('برترین‌ها', h('button', { type: 'button', text: 'همه', onclick: function () { go('players', { f: 'all', sort: 'elo' }); } })),
        h('div', { class: 'group' }, B.top.slice(0, 3).map(function (p, i) {
          return row({
            lead: h('div', { style: 'position:relative' }, avatar(p.id, p.name, 'sm')),
            title: p.name,
            sub: p.cls || (p.elite ? 'بازیکن برتر' : ''),
            end: p.elo != null ? h('span', { class: 'elo num' }, num(p.elo)) : (p.wins != null ? h('span', { class: 'num' }, p.wins + ' برد') : null),
            tap: function () { openPlayer(p.id, p); }
          });
        })));
    }
    body.replaceChildren(frag);
  }

  function trendCard(t) {
    var days = t.days, max = 1;
    days.forEach(function (d) { if (d.c > max) max = d.c; });
    var wk = window.Intl ? new Intl.DateTimeFormat('fa-IR', { weekday: 'short' }) : null;
    var bars = h('div', { class: 'bars' });
    days.forEach(function (d, i) {
      var fill = h('i', { class: i === days.length - 1 ? 'now' : '' });
      var label = i === days.length - 1 ? 'امروز' : (wk ? wk.format(new Date(d.d + 'T12:00:00')) : d.d.slice(5));
      bars.append(h('div', { class: 'bar' }, h('b', { class: 'num', text: d.c ? d.c : '' }), h('div', { class: 'plot' }, fill), h('span', { text: label })));
      var s = d.c ? Math.max(.08, d.c / max) : .03;
      requestAnimationFrame(function () { requestAnimationFrame(function () { fill.style.setProperty('--s', s); }); });
    });
    var mx = t.mix, tot = mx.white + mx.black + mx.draw;
    var mix = h('div', { class: 'mix' });
    if (tot > 0) {
      mix.append(
        h('div', { class: 'mix-strip' },
          mx.white ? h('i', { class: 'c-white', style: 'flex:' + mx.white }) : null,
          mx.draw ? h('i', { class: 'c-draw', style: 'flex:' + mx.draw }) : null,
          mx.black ? h('i', { class: 'c-black', style: 'flex:' + mx.black }) : null),
        h('div', { class: 'mix-legend' },
          h('span', null, h('i', { class: 'c-white' }), 'برد سفید ', nn(mx.white)),
          h('span', null, h('i', { class: 'c-draw' }), 'تساوی ', nn(mx.draw)),
          h('span', null, h('i', { class: 'c-black' }), 'برد سیاه ', nn(mx.black))),
        h('div', { class: 'r-s', style: 'margin-top:8px;text-align:center', text: 'ترکیب نتیجه‌ها در ۳۰ روز اخیر' }));
    } else {
      mix.append(h('div', { class: 'r-s', style: 'text-align:center', text: 'در ۳۰ روز اخیر نتیجه‌ای ثبت نشده است.' }));
    }
    return h('div', { class: 'group trend' }, bars, mix);
  }

  /* ─── بازیکنان ────────────────────────────────────────────── */
  var P = { rows: null, map: null, t: 0, f: 'all', q: '', sort: 'elo', shown: 0, view: [], loading: null, ui: null };
  var CHUNK = 40;

  function setPlayers(d) {
    P.rows = d.rows.map(function (r) {
      var o = {}; d.cols.forEach(function (c, i) { o[c] = r[i]; });
      o._n = norm(o.name); o._c = norm(o.cls);
      return o;
    });
    P.map = new Map(P.rows.map(function (p) { return [p.id, p]; }));
    P.t = Date.now();
  }
  function ensurePlayers(force) {
    if (P.loading) return P.loading;
    if (P.rows && !force && Date.now() - P.t < 30000) return Promise.resolve();
    P.loading = api('/hub/api/players').then(function (d) {
      setPlayers(d); LS.set(K_PL, d);
      if (built.players) refreshPlayers();
    }).catch(function () { if (built.players && !P.rows) showPlayersError(); })
      .then(function () { P.loading = null; });
    return P.loading;
  }

  function buildPlayers() {
    var root = $('#tab-players');
    if (!can('elo')) P.sort = 'name';
    var input = h('input', { type: 'search', placeholder: 'جستجوی نام یا کلاس', enterkeyhint: 'search', autocomplete: 'off', 'aria-label': 'جستجو' });
    var chips = h('div', { class: 'chips' });
    var list = h('div', { class: 'plist' });
    var more = h('div', { class: 'more' });
    var stick = h('div', { class: 'stick' },
      h('div', { class: 'title-row' }, h('h1', { class: 'page-title', text: 'بازیکنان', style: 'padding-bottom:12px' }),
        can('player_register') ? h('button', { type: 'button', class: 'add-fab', 'aria-label': 'ثبت‌نام بازیکن', onclick: function () { withManage(function (m) { m.registerPlayer(); }); } }, ic('plus')) : null),
      h('div', { class: 'search' }, ic('search'), input), chips);
    root.replaceChildren(stick, list, more);
    P.ui = { input: input, chips: chips, list: list, more: more };

    var t = 0;
    input.addEventListener('input', function () {
      clearTimeout(t);
      t = setTimeout(function () { P.q = norm(input.value); applyPlayers(); }, 120);
    });
    new IntersectionObserver(function (es) {
      if (es[0].isIntersecting && P.shown < P.view.length) renderPlayerChunk();
    }, { rootMargin: '600px' }).observe(more);
    built.players = true;

    if (!P.rows) {
      var cached = LS.get(K_PL);
      if (cached) setPlayers(cached);
    }
    if (P.rows) applyPlayers(); else list.replaceChildren(h('div', { class: 'spin', text: 'در حال بارگذاری…' }));
    ensurePlayers(true);
  }
  function refreshPlayers() { if (P.ui) applyPlayers(); }
  function showPlayersError() {
    P.ui.list.replaceChildren(h('div', { class: 'empty' }, 'بارگذاری نشد. ', h('button', { class: 'add-row', text: 'تلاش دوباره', onclick: function () { ensurePlayers(true); } })));
  }
  function applyPlayers() {
    if (!P.rows) return;
    var a = P.rows, elite = 0, special = 0;
    P.rows.forEach(function (p) { if (p.elite) elite++; if (p.special) special++; });
    if (P.f === 'elite') a = a.filter(function (p) { return p.elite; });
    else if (P.f === 'special') a = a.filter(function (p) { return p.special; });
    if (P.q) a = a.filter(function (p) { return p._n.indexOf(P.q) > -1 || p._c.indexOf(P.q) > -1; });
    if (P.sort === 'name') a = a.slice().sort(function (x, y) { return x.name.localeCompare(y.name, 'fa'); });
    P.view = a; P.shown = 0;

    function chip(key, label, n) {
      return h('button', { type: 'button', class: 'chip' + (P.f === key ? ' on' : ''), onclick: function () { if (P.f !== key) { P.f = key; hx.sel(); applyPlayers(); } } },
        label, h('small', { class: 'num', text: n }));
    }
    P.ui.chips.replaceChildren(
      chip('all', 'همه', P.rows.length), chip('elite', 'برترین‌ها', elite), chip('special', 'نیروهای ویژه', special),
      can('elo') ? h('button', { type: 'button', class: 'chip', onclick: function () { P.sort = P.sort === 'elo' ? 'name' : 'elo'; hx.sel(); applyPlayers(); } },
        ic('sort', ''), P.sort === 'elo' ? 'بر اساس امتیاز' : 'بر اساس نام') : null);
    P.ui.chips.querySelectorAll('.ic').forEach(function (s) { s.style.width = '16px'; s.style.height = '16px'; });

    P.ui.list.replaceChildren();
    if (!a.length) {
      P.ui.list.append(h('div', { class: 'empty', text: P.q ? 'بازیکنی با این نام پیدا نشد.' : 'در این بخش هنوز بازیکنی نیست.' }));
      return;
    }
    renderPlayerChunk();
  }
  function renderPlayerChunk() {
    var showRank = P.sort === 'elo' && P.f === 'all' && !P.q;
    var frag = doc.createDocumentFragment();
    var end = Math.min(P.view.length, P.shown + CHUNK);
    for (var i = P.shown; i < end; i++) frag.append(playerRow(P.view[i], showRank ? i + 1 : 0));
    P.shown = end;
    P.ui.list.append(frag);
  }
  function badges(p) {
    return [p.elite ? h('span', { class: 'badge elite' }, ic('star'), 'برتر') : null,
            p.special ? h('span', { class: 'badge special' }, ic('bolt'), 'ویژه') : null,
            p.status && p.status !== 'active' ? h('span', { class: 'badge off', text: 'غیرفعال' }) : null];
  }
  function playerRow(p, rank) {
    var r = h('button', { type: 'button', class: 'row', onclick: function () { openPlayer(p.id); } },
      rank ? h('span', { class: 'rank num' + (rank <= 3 ? ' top' : ''), text: rank }) : null,
      avatar(p.id, p.name, 'sm'),
      h('div', { class: 'r-main' },
        h('div', { class: 'r-t', text: p.name }),
        h('div', { class: 'r-s' }, p.cls ? p.cls + '، ' : '', nn(p.w + p.d + p.l), ' بازی')),
      h('div', { class: 'r-end', style: 'flex-direction:column;align-items:flex-end;gap:3px' },
        p.elo != null ? h('span', { class: 'elo num', text: p.elo }) : null,
        (p.elite || p.special || (p.status && p.status !== 'active')) ? h('span', { style: 'display:flex;gap:4px' }, badges(p)) : null));
    if (M && can('players_view')) r.addEventListener('pointerdown', function () { M.warmPlayer(p.id); }, { passive: true });
    return h('div', { class: 'prow' }, r);
  }

  function openPlayer(id, fallback) {
    if (can('players_view')) { withManage(function (m) { m.openPlayer(id); }); return; }
    var p = P.map && P.map.get(id);
    if (!p && fallback) p = { id: id, name: fallback.name, cls: fallback.cls, elo: fallback.elo || 1200, w: 0, d: 0, l: 0, warn: 0, elite: fallback.elite ? 1 : 0, special: fallback.special ? 1 : 0, games: fallback.games || 0, status: 'active', _partial: true };
    if (!p) { ensurePlayers().then(function () { if (P.map && P.map.get(id)) openPlayer(id); }); return; }

    var extra = h('div', null, h('div', { class: 'spin', text: 'در حال بارگذاری…' }));
    var total = p.w + p.d + p.l;
    var body = h('div', null,
      h('div', { class: 'p-top' }, avatar(p.id, p.name, 'lg'), h('h3', { text: p.name }),
        p.cls ? h('div', { class: 'p-sub', text: p.cls }) : null,
        (p.elite || p.special || (p.status && p.status !== 'active')) ? h('div', { class: 'tags' }, badges(p)) : null),
      h('div', { class: 'stat3' },
        h('div', null, h('b', { class: 'num', text: p.elo }), h('span', { text: 'امتیاز' })),
        h('div', null, h('b', { class: 'num', text: total }), h('span', { text: 'بازی' })),
        h('div', null, h('b', { class: 'num', text: p.warn || 0 }), h('span', { text: 'اخطار' }))),
      total ? h('div', { class: 'wdl' },
        h('div', { class: 'wdl-strip' }, p.w ? h('i', { class: 'w', style: 'flex:' + p.w }) : null, p.d ? h('i', { class: 'd', style: 'flex:' + p.d }) : null, p.l ? h('i', { class: 'l', style: 'flex:' + p.l }) : null),
        h('div', { class: 'wdl-legend' }, h('span', null, 'برد ', nn(p.w)), h('span', null, 'تساوی ', nn(p.d)), h('span', null, 'باخت ', nn(p.l)))) : null,
      extra);
    Sheet.open({ title: '', body: body });

    api('/hub/api/player/' + id).then(function (d) {
      var kids = [];
      if (d.elo) {
        kids.push(secTitle('رتبه‌بندی'), h('div', { class: 'group' },
          kv('رتبه بین بازیکنان فعال', nn(d.rank)), kv('بالاترین امتیاز', nn(d.elo.peak))));
      }
      kids.push(secTitle('آخرین بازی‌ها'));
      if (!d.matches.length) kids.push(h('div', { class: 'group' }, h('div', { class: 'empty', text: 'هنوز بازی‌ای ثبت نشده است.' })));
      else kids.push(h('div', { class: 'group' }, d.matches.map(function (m) { return matchRow(m, id); })));
      extra.replaceChildren.apply(extra, kids);
    }).catch(function () { extra.replaceChildren(h('div', { class: 'empty', text: 'جزئیات بازی‌ها بارگذاری نشد.' })); });
  }

  function matchRow(m, pid) {
    var end, res;
    if (pid != null) {
      var mine = m.wid === pid ? 'white' : 'black';
      if (!m.res) { res = h('span', { class: 'mres p' }, ic('hourglass', '')); res.firstChild.style.cssText = 'width:15px;height:15px'; }
      else if (m.res === 'draw') res = h('span', { class: 'mres d', text: '=' });
      else if (m.res === 'cancelled') res = h('span', { class: 'mres p', text: '×' });
      else res = h('span', { class: 'mres ' + (m.res === mine ? 'w' : 'l'), text: m.res === mine ? '+' : '−' });
      var opp = m.wid === pid ? m.b : m.w;
      return h('div', { class: 'mrow' }, res,
        h('div', { class: 'mtxt' }, h('b', { text: 'مقابل ' + opp }), h('small', { text: (m.t || 'بدون مسابقه') + (m.date ? '، ' + m.date : '') })));
    }
    var score = { white: '1 – 0', black: '0 – 1', draw: '½ – ½', cancelled: 'لغو' }[m.res] || null;
    var editable = m.id != null && canAny('match_edit', 'match_delete');
    return h(editable ? 'button' : 'div', { class: 'mrow', type: editable ? 'button' : null, onclick: editable ? function () { withManage(function (mm) { mm.matchDetail(m); }); } : null },
      h('div', { class: 'mtxt' }, h('b', { text: m.w + ' در برابر ' + m.b }), h('small', { text: m.date || '' })),
      score ? h('b', { class: 'num', style: 'font-weight:700', text: score }) : h('span', { class: 'badge off', text: 'در انتظار' }));
  }

  /* ─── مسابقات ────────────────────────────────────────────── */
  var T = { f: null, ui: null };
  function buildTours() {
    var root = $('#tab-tours');
    var chips = h('div', { class: 'chips' });
    var list = h('div', { style: 'margin-top:8px' });
    root.replaceChildren(h('h1', { class: 'page-title', text: 'مسابقات' }), chips, list);
    T.ui = { chips: chips, list: list };
    built.tours = true;
    renderTours();
  }
  function renderTours() {
    if (!T.ui || !S.boot) return;
    var all = S.boot.tournaments || [];
    var act = all.filter(function (t) { return t.status === 'active'; });
    var fin = all.filter(function (t) { return t.status !== 'active'; });
    if (!T.f) T.f = act.length || !all.length ? 'active' : 'all';
    function chip(k, label, n) {
      return h('button', { type: 'button', class: 'chip' + (T.f === k ? ' on' : ''), onclick: function () { T.f = k; hx.sel(); renderTours(); } }, label, h('small', { class: 'num', text: n }));
    }
    T.ui.chips.replaceChildren(chip('active', 'فعال', act.length), chip('done', 'پایان‌یافته', fin.length), chip('all', 'همه', all.length));
    var a = T.f === 'active' ? act : T.f === 'done' ? fin : all;
    if (!a.length) { T.ui.list.replaceChildren(h('div', { class: 'empty', text: 'مسابقه‌ای در این بخش نیست.' })); return; }
    var g = h('div', { class: 'group' });
    a.forEach(function (t) {
      var p = t.total ? t.done / t.total : 0;
      var isAct = t.status === 'active';
      g.append(h('button', { type: 'button', class: 'tcard', onclick: function () { openTournament(t); } },
        h('div', { class: 't-head' }, h('i', { class: 'dot' + (isAct ? '' : ' off') }), h('div', { class: 't-name', text: t.name })),
        h('div', { class: 't-meta' }, nn(t.done), ' از ', nn(t.total), ' بازی انجام شده', t.created ? '، شروع ' + jdate(t.created) : ''),
        h('div', { class: 'prog' + (p >= 1 && t.total ? ' done' : ''), style: '--p:' + p.toFixed(3) }, h('i'))));
    });
    T.ui.list.replaceChildren(g);
  }
  function openTournament(t) {
    var extra = h('div', null, h('div', { class: 'spin', text: 'در حال بارگذاری…' }));
    var body = h('div', null,
      h('div', { class: 'stat3', style: 'margin-top:6px' },
        h('div', null, h('b', { class: 'num', text: t.total }), h('span', { text: 'کل بازی‌ها' })),
        h('div', null, h('b', { class: 'num', text: t.done }), h('span', { text: 'انجام‌شده' })),
        h('div', null, h('b', { class: 'num', text: Math.max(0, t.total - t.done) }), h('span', { text: 'باقی‌مانده' }))),
      extra);
    var tfoot = null;
    if (can('match_create')) tfoot = h('button', { type: 'button', class: 'btn', text: '+ ثبت مسابقه‌ی جدید', onclick: function () { withManage(function (mm) { mm.createMatch(); }); } });
    Sheet.open({ title: t.name, body: body, foot: tfoot });
    api('/hub/api/tournament/' + t.id).then(function (d) {
      var kids = [];
      if (d.standings.length) {
        kids.push(secTitle('جدول امتیاز', h('small', { text: 'برد ۱، تساوی ½' })),
          h('div', { class: 'group' }, d.standings.map(function (s, i) {
            return h('div', { class: 'mrow' },
              h('span', { class: 'rank num' + (i < 3 ? ' top' : ''), style: 'width:22px;text-align:center;font-size:13px;font-weight:600;color:' + (i < 3 ? 'var(--gold)' : 'var(--hint)'), text: i + 1 }),
              h('div', { class: 'mtxt' }, h('b', { text: s.name }), h('small', null, nn(s.p), ' بازی: ', nn(s.w), ' برد، ', nn(s.d), ' تساوی، ', nn(s.l), ' باخت')),
              h('b', { class: 'num', style: 'font-size:17px', text: s.pts % 1 ? s.pts.toFixed(1) : s.pts }));
          })));
      }
      kids.push(secTitle('بازی‌ها', h('small', { text: 'ابتدا منتظر نتیجه' })));
      kids.push(d.matches.length ? h('div', { class: 'group' }, d.matches.map(function (m) { return matchRow(m, null); }))
        : h('div', { class: 'group' }, h('div', { class: 'empty', text: 'بازی‌ای ثبت نشده است.' })));
      extra.replaceChildren.apply(extra, kids);
    }).catch(function () { extra.replaceChildren(h('div', { class: 'empty', text: 'جزئیات مسابقه بارگذاری نشد.' })); });
  }

  /* ─── پروفایل ─────────────────────────────────────────────── */
  function buildMe() { built.me = true; renderMe(); }

  function profileBody(m, opts) {
    var line = [m.title, m.city].filter(Boolean).join('، ');
    var kids = [
      h('div', { class: 'me-head', style: opts.sheet ? 'padding-top:6px' : '' },
        avatar(m.id, m.name, 'lg', m.avatar),
        h('div', { class: 'me-name', text: m.name || 'مدیر' }),
        roleChip(m),
        line ? h('div', { class: 'me-line', text: line }) : null)
    ];
    if (opts.edit) kids.push(h('div', { class: 'me-actions' }, h('button', { type: 'button', class: 'btn', onclick: openEdit }, ic('pencil'), 'ویرایش پروفایل')));

    kids.push(secTitle('درباره'));
    kids.push(h('div', { class: 'group' }, m.bio
      ? h('div', { class: 'text-block', text: m.bio })
      : h('div', { class: 'text-block muted', text: opts.edit ? 'هنوز توضیحی ننوشته‌اید. با «ویرایش پروفایل» چند خط درباره‌ی خودتان و کارتان بنویسید.' : 'این مدیر هنوز توضیحی ننوشته است.' })));

    if (m.details && m.details.length) {
      kids.push(secTitle('جزئیات'), h('div', { class: 'group' }, m.details.map(function (d) { return kv(d.k, d.v); })));
    }
    var act = [];
    var now = S.boot && S.boot.now;
    if (opts.edit && m.my) {
      act.push(kv('نتیجه‌های ثبت‌شده در ۷ روز اخیر', nn(m.my.week)), kv('کل نتیجه‌های ثبت‌شده', nn(m.my.total)));
    }
    if (m.joined_at) act.push(kv('عضویت', jdate(m.joined_at)));
    if (m.last_active && now) { var r = rel(m.last_active, now); if (r) act.push(kv('آخرین فعالیت', r)); }
    if (act.length) kids.push(secTitle(opts.edit ? 'فعالیت من' : 'فعالیت'), h('div', { class: 'group' }, act));
    return kids;
  }

  function renderMe() {
    if (!S.boot) return;
    var B = S.boot, me = B.me;
    var kids = profileBody(me, { edit: true });
    var mates = B.team.filter(function (t) { return t.id !== me.id; });
    if (mates.length) {
      kids.push(secTitle('تیم مدیران', h('small', { class: 'num', text: mates.length })));
      kids.push(h('div', { class: 'group' }, mates.map(function (t) {
        return row({ lead: avatar(t.id, t.name, 'sm', t.avatar), title: t.name || 'مدیر',
          sub: t.title || t.role_label, tap: function () { openMate(t); } });
      })));
    }
    $('#tab-me').replaceChildren.apply($('#tab-me'), kids);
    /* آیکونِ تب */
    var mi = $('#me-ic'); mi.replaceChildren();
    mi.append(doc.createTextNode(me.name ? Array.from(me.name)[0].toUpperCase() : ''));
    var im = h('img', { src: me.avatar, alt: '', decoding: 'async' });
    im.addEventListener('error', function () { im.remove(); });
    mi.append(im);
  }
  function openMate(t) {
    Sheet.open({ title: '', body: h('div', null, profileBody(t, { sheet: true })) });
  }

  function openEdit() {
    var me = S.boot.me;
    function field(label, max, input) {
      var cnt = h('span', { class: 'num', text: '' });
      function upd() { cnt.textContent = input.value.length + '/' + max; }
      input.addEventListener('input', upd); upd();
      return h('div', { class: 'field' }, h('label', null, h('span', { text: label }), cnt), input);
    }
    var iName = h('input', { class: 'inp', maxlength: 40, value: me.name || '', autocomplete: 'off' });
    var iTitle = h('input', { class: 'inp', maxlength: 40, value: me.title || '', placeholder: 'مثلاً مسئول مسابقات', autocomplete: 'off' });
    var iCity = h('input', { class: 'inp', maxlength: 30, value: me.city || '', autocomplete: 'off' });
    var iBio = h('textarea', { class: 'inp', maxlength: 300, rows: 4, placeholder: 'چند خط درباره‌ی خودتان و کارتان' });
    iBio.value = me.bio || '';

    var drows = h('div', { style: 'margin:0 16px' });
    function addRow(k, v) {
      var ik = h('input', { class: 'inp', maxlength: 20, placeholder: 'عنوان', value: k || '' });
      var iv = h('input', { class: 'inp', maxlength: 60, placeholder: 'مقدار', value: v || '' });
      var r = h('div', { class: 'drow' }, ik, iv, h('button', { type: 'button', 'aria-label': 'حذف', onclick: function () { r.remove(); sync(); } }, ic('trash')));
      drows.append(r); sync();
    }
    var addBtn = h('button', { type: 'button', class: 'add-row', onclick: function () { addRow('', ''); } }, '+ افزودن جزئیات');
    function sync() { addBtn.style.display = drows.children.length >= 5 ? 'none' : ''; }
    (me.details || []).forEach(function (d) { addRow(d.k, d.v); });

    var save = h('button', { type: 'button', class: 'btn' }, 'ذخیره تغییرات');
    save.addEventListener('click', function () {
      var details = [];
      drows.querySelectorAll('.drow').forEach(function (r) {
        var i = r.querySelectorAll('input'); var k = i[0].value.trim(), v = i[1].value.trim();
        if (k && v) details.push({ k: k, v: v });
      });
      save.disabled = true; save.textContent = 'در حال ذخیره…';
      api('/hub/api/profile', { display_name: iName.value, title: iTitle.value, city: iCity.value, bio: iBio.value, details: details })
        .then(function (d) {
          var p = d.profile;
          ['title', 'city', 'bio', 'details'].forEach(function (k) { me[k] = p[k]; });
          me.name = p.name || me.name;
          var self = S.boot.team.find(function (t) { return t.id === me.id; });
          if (self) { self.name = me.name; ['title', 'city', 'bio', 'details'].forEach(function (k) { self[k] = p[k]; }); }
          LS.set(K_BOOT, S.boot);
          hx.ok(); Sheet.close(); toast('پروفایل ذخیره شد');
          renderMe(); renderHome();
        })
        .catch(function (e) {
          hx.err(); save.disabled = false; save.textContent = 'ذخیره تغییرات';
          toast(e.code === 429 ? 'کمی صبر کنید و دوباره امتحان کنید' : 'ذخیره نشد؛ اتصال را بررسی کنید');
        });
    });

    Sheet.open({
      title: 'ویرایش پروفایل',
      body: h('div', null,
        field('نام نمایشی', 40, iName), field('سمت', 40, iTitle), field('شهر', 30, iCity), field('درباره‌ی من', 300, iBio),
        h('div', { class: 'field', style: 'margin-bottom:6px' }, h('label', null, h('span', { text: 'جزئیات (تا ۵ مورد، مثل تخصص یا ساعت حضور)' }))),
        drows, h('div', { style: 'margin:0 20px' }, addBtn)),
      foot: save
    });
  }

  /* ─── ساعت (تنبل) ─────────────────────────────────────────── */
  var clockState = 0; // 0 نه، 1 در حال بارگذاری، 2 آماده
  function loadClock(cb) {
    if (clockState === 2) return cb && cb();
    if (clockState === 1) return;
    clockState = 1;
    var s = doc.createElement('script');
    s.src = 'clock.js?v=' + V;
    s.onload = function () { clockState = 2; window.HubClock.mount($('#tab-clock'), { h: h, ic: ic, hx: hx, tg: tg, Sheet: Sheet, toast: toast, LS: LS }); if (cb) cb(); };
    s.onerror = function () { clockState = 0; toast('بارگذاری ساعت ناموفق بود'); };
    doc.head.append(s);
  }

  /* ─── آب‌وهوا (تنبل) ───────────────────────────────────────── */
  var wxState = 0; // 0 نه، 1 در حال بارگذاری، 2 آماده
  function loadWeather() {
    if (wxState === 2) return;
    if (wxState === 1) return;
    wxState = 1;
    if (!doc.getElementById('wx-css')) {
      var l = doc.createElement('link'); l.id = 'wx-css'; l.rel = 'stylesheet'; l.href = 'weather.css?v=' + V; doc.head.append(l);
    }
    var s = doc.createElement('script');
    s.src = 'weather.js?v=' + V;
    s.onload = function () {
      wxState = 2;
      window.HubWeather.mount($('#tab-weather'), { h: h, ic: ic, hx: hx, tg: tg, toast: toast, LS: LS, api: api, goHome: function () { go('home'); } });
      if (cur === 'weather' && window.HubWeather.onShow) window.HubWeather.onShow();
    };
    s.onerror = function () { wxState = 0; toast('بارگذاری آب‌وهوا ناموفق بود'); };
    doc.head.append(s);
  }
  function loadWeatherLater() {
    if (wxState) return;
    var a = doc.createElement('link'); a.rel = 'prefetch'; a.as = 'script'; a.href = 'weather.js?v=' + V; doc.head.append(a);
    var b = doc.createElement('link'); b.rel = 'prefetch'; b.as = 'style'; b.href = 'weather.css?v=' + V; doc.head.append(b);
  }

  /* کارتِ آب‌وهوا در خانه: صحنه‌ی مخصوصِ هر وضعیت (SVG) + چیپ‌های مرتبط با همان وضعیت.
     فوراً از کشِ محلی رنگ می‌شود، بعد در پس‌زمینه تازه می‌شود. حرکت‌ها بسیار ملایم و فقط transform/opacity‌اند. */
  var WX_G = {
    clear: [['#1c7ed6', '#74c0fc'], ['#0b1230', '#27355f']], partly: [['#2f7fc9', '#8fc1ea'], ['#0b1230', '#2c3a66']],
    cloud: [['#4b6580', '#8ea3b6'], ['#0e1422', '#2c3548']], drizzle: [['#3f566d', '#7e93a7'], ['#0c121d', '#273141']],
    rain: [['#2f4156', '#657b92'], ['#080d16', '#1f2937']], storm: [['#1f2633', '#4a556a'], ['#05070d', '#171e2b']],
    snow: [['#5f7a96', '#b4c7d8'], ['#16213a', '#46587a']], fog: [['#6b7783', '#aab3bb'], ['#1a1f27', '#414a56']]
  };
  var WXM_CLOUD = 'M14 37a8.5 8.5 0 0 1-.8-16.9A11.5 11.5 0 0 1 35 18a8.6 8.6 0 0 1 1 19z';
  var WXM_MOON_K = { '🌑': [0, 0], '🌒': [.25, 0], '🌓': [.5, 0], '🌔': [.75, 0], '🌕': [1, 0], '🌖': [.75, 1], '🌗': [.5, 1], '🌘': [.25, 1] };
  function wxmGrad(d) {
    var n = d.now, night = !n.is_day, t = n.temp == null ? 20 : n.temp, dust = !!(d.air && d.air.dust >= 100);
    if (dust && (n.kind === 'clear' || n.kind === 'partly' || n.kind === 'cloud')) return night ? ['#2b1e12', '#6c4f35'] : ['#9a6a35', '#dba35c'];
    if (n.kind === 'clear' && night) return WX_G.clear[1];
    if (n.kind === 'clear' || n.kind === 'partly') {
      if (t >= 36) return ['#c92a2a', '#ff922b'];
      if (t >= 31) return ['#e8590c', '#ffb454'];
      if (t <= 3) return ['#4a89c8', '#b6dcf7'];
    }
    return (WX_G[n.kind] || WX_G.clear)[night ? 1 : 0];
  }
  function wxmCloud(x, y, s, fill, op) {
    return '<g transform="translate(' + x + ' ' + y + ') scale(' + s + ')" opacity="' + (op || 1) + '"><path d="' + WXM_CLOUD + '" fill="' + fill + '"/></g>';
  }
  function wxmSun(cx, cy, r, dim) {
    var rays = '', i;
    for (i = 0; i < 12; i++) rays += '<path d="M' + cx + ' ' + (cy - r - 7) + 'v-' + (i % 2 ? 6 : 10) + '" transform="rotate(' + (i * 30) + ' ' + cx + ' ' + cy + ')"/>';
    return '<g opacity="' + (dim ? .45 : 1) + '"><circle cx="' + cx + '" cy="' + cy + '" r="' + (r + 18) + '" fill="#ffe27a" opacity=".16"/><circle cx="' + cx + '" cy="' + cy + '" r="' + (r + 9) + '" fill="#ffe27a" opacity=".26"/>' +
      '<g class="wm-rot" style="transform-origin:' + cx + 'px ' + cy + 'px" stroke="#fff1a8" stroke-width="2.6" stroke-linecap="round" opacity=".75">' + rays + '</g>' +
      '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="#ffd43b"/></g>';
  }
  function wxmMoon(emoji, cx, cy, r) {
    var m = WXM_MOON_K[emoji] || [1, 0], k = m[0], lit = '';
    if (k >= 1) lit = '<circle cx="0" cy="0" r="' + r + '" fill="#f3f0df"/>';
    else if (k > 0) lit = '<path d="M0 ' + (-r) + 'A' + r + ' ' + r + ' 0 0 1 0 ' + r + 'A' + (r * Math.abs(1 - 2 * k)).toFixed(1) + ' ' + r + ' 0 0 ' + (k > .5 ? 1 : 0) + ' 0 ' + (-r) + 'Z" fill="#f3f0df"/>';
    if (m[1]) lit = '<g transform="scale(-1 1)">' + lit + '</g>';
    return '<g transform="translate(' + cx + ' ' + cy + ')"><circle r="' + (r + 12) + '" fill="#c8d6ff" opacity=".12"/><circle r="' + r + '" fill="#252d52" opacity=".9"/>' + lit +
      '<g fill="#8f93a8" opacity=".28"><circle cx="' + (-r * .3) + '" cy="' + (-r * .3) + '" r="' + (r * .16) + '"/><circle cx="' + (r * .25) + '" cy="' + (r * .2) + '" r="' + (r * .2) + '"/></g></g>';
  }
  function wxmStars() {
    var p = [[14, 20, 1.6], [34, 8, 1.2], [92, 14, 1.5], [128, 30, 1.2], [112, 8, 1], [20, 62, 1.1], [136, 74, 1.4], [100, 100, 1]], s = '';
    p.forEach(function (q) { s += '<circle cx="' + q[0] + '" cy="' + q[1] + '" r="' + q[2] + '"/>'; });
    return '<g fill="#fff" opacity=".7">' + s + '</g>';
  }
  function wxmDrops(n, x0, y0, step, col) {
    var s = '', i;
    for (i = 0; i < n; i++) s += '<path class="wm-d" style="animation-delay:' + (-i * .27).toFixed(2) + 's" d="M' + (x0 + i * step) + ' ' + y0 + 'l-3 8" stroke="' + col + '" stroke-width="2.6" stroke-linecap="round"/>';
    return s;
  }
  function wxmThermo(x, y) {   // دماسنجِ داغ (برای هوای گرم)
    return '<g transform="translate(' + x + ' ' + y + ')"><rect x="-5" y="0" width="10" height="38" rx="5" fill="#fff" opacity=".9"/><circle cx="0" cy="44" r="9" fill="#fff" opacity=".9"/>' +
      '<rect x="-2.2" y="8" width="4.4" height="34" rx="2.2" fill="#e03131"/><circle cx="0" cy="44" r="6" fill="#e03131"/>' +
      '<g stroke="#fff" stroke-width="2" stroke-linecap="round" opacity=".8"><path d="M9 8h6M9 16h4M9 24h6"/></g></g>';
  }
  function wxmCrystal(x, y) {  // کریستالِ یخ (برای هوای سرد)
    var l = '', i; for (i = 0; i < 3; i++) l += '<path d="M0 -12V12" transform="rotate(' + (i * 60) + ')"/>';
    return '<g transform="translate(' + x + ' ' + y + ')" stroke="#fff" stroke-width="2.4" stroke-linecap="round" opacity=".85">' + l + '</g>';
  }
  function wxmHeat() {
    var s = '', i;
    for (i = 0; i < 3; i++) s += '<path d="M46 ' + (96 + i * 9) + 'q9 -6 18 0t18 0t18 0t18 0" opacity="' + (.38 - i * .08) + '"/>';
    return '<g fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round">' + s + '</g>';
  }
  function wxmScene(d) {
    var n = d.now, k = n.kind, day = n.is_day, t = n.temp == null ? 20 : n.temp, dust = !!(d.air && d.air.dust >= 100), s = '';
    var hot = day && t >= 31, cold = t <= 3;
    if (k === 'clear') {
      if (day) s = wxmSun(66, 52, 17, dust) + (hot ? wxmHeat() + wxmThermo(124, 20) : '') + (cold ? wxmCrystal(122, 32) : '');
      else s = wxmStars() + wxmMoon(d.moon, 74, 56, 26);
    } else if (k === 'partly') {
      s = (day ? wxmSun(50, 40, 13) : wxmStars() + wxmMoon(d.moon, 52, 42, 18)) + wxmCloud(34, 40, 2.3, '#f1f4f8') + (hot ? wxmThermo(126, 22) : '');
    } else if (k === 'cloud') {
      s = wxmCloud(48, 14, 2, '#dfe5ec', .6) + wxmCloud(20, 42, 2.5, '#f1f4f8', .95);
    } else if (k === 'drizzle' || k === 'rain') {
      s = wxmCloud(24, 12, 2.6, k === 'rain' ? '#aab6c4' : '#d7dee6') + wxmDrops(k === 'rain' ? 6 : 4, 44, 92, 14, '#8fd0ff');
    } else if (k === 'storm') {
      s = wxmCloud(24, 8, 2.7, '#6c7788') + '<path class="wm-bolt" d="M72 68l-12 20h9l-4 16 17-24h-10z" fill="#ffd43b"/>' + wxmDrops(3, 44, 96, 26, '#8fd0ff');
    } else if (k === 'snow') {
      var fl = '', i, xs = [38, 56, 74, 92, 110], ys = [96, 108, 94, 106, 98];
      for (i = 0; i < 5; i++) fl += '<circle class="wm-f" style="animation-delay:' + (-i * .8).toFixed(1) + 's" cx="' + xs[i] + '" cy="' + ys[i] + '" r="2.6" fill="#fff"/>';
      s = wxmCloud(24, 12, 2.6, '#eef2f7') + fl + (cold ? wxmCrystal(126, 30) : '');
    } else if (k === 'fog') {
      s = '<g fill="#fff"><rect x="14" y="34" width="110" height="9" rx="4.5" opacity=".55"/><rect x="30" y="54" width="100" height="9" rx="4.5" opacity=".42"/><rect x="10" y="74" width="96" height="9" rx="4.5" opacity=".5"/><rect x="36" y="94" width="88" height="9" rx="4.5" opacity=".3"/></g>';
    } else s = wxmCloud(30, 30, 2.6, '#f1f4f8');
    if (dust && day) s += '<g stroke="#fff" stroke-width="2.2" stroke-linecap="round" opacity=".4"><path d="M46 86h60M60 98h70M52 110h66"/></g>';
    return '<svg viewBox="0 0 150 124" preserveAspectRatio="xMinYMid meet" aria-hidden="true">' + s + '</svg>';
  }
  function wxmChips(d) {
    var n = d.now, t0 = d.days[0] || {}, a = d.air, c = [], i;
    var mp = Math.max.apply(null, (d.hours || []).slice(0, 12).map(function (x) { return x.pop; }).concat([0]));
    var feels = n.feels == null ? n.temp : n.feels, uv = t0.uv || 0, dust = !!(a && (a.aqi > 100 || a.dust > 100));
    if (n.kind === 'storm') { c.push('رعدوبرق؛ بیرون نرو'); if (mp) c.push('بارش ' + mp + '٪'); }
    else if (n.kind === 'snow') { c.push('برف می‌بارد'); c.push('لباسِ گرم'); }
    else if (mp >= 40 || n.kind === 'rain' || n.kind === 'drizzle') { c.push('بارش ' + mp + '٪'); c.push(mp >= 60 ? 'چتر ببر' : 'چترِ کوچک'); }
    else if (dust) { c.push('گردوغبار'); c.push('ماسک بزن'); }
    else if (n.kind === 'fog') { c.push('دیدِ کم'); c.push('احتیاط در رانندگی'); }
    else if (n.is_day && feels >= 31) { if (uv >= 6) c.push('UV ' + Math.round(uv)); c.push('آب همراهت باشه'); c.push('لباسِ نخیِ روشن'); }
    else if (feels != null && feels <= 3) { c.push('خیلی سرده'); c.push('کاپشنِ گرم'); }
    else if (n.is_day && uv >= 6) { c.push('UV ' + Math.round(uv)); c.push('کلاه و ضدآفتاب'); }
    else if (!n.is_day) { c.push(feels != null && feels <= 12 ? 'شبِ خنک' : 'شبِ مطبوع'); c.push(mp < 30 ? 'آسمانِ آرام' : 'بارش ' + mp + '٪'); }
    else { c.push(feels != null && feels <= 15 ? 'ژاکت بردار' : 'هوای مطبوع'); c.push(mp < 30 ? 'چتر لازم نیست' : 'بارش ' + mp + '٪'); }
    if (feels != null && c.length < 3) c.push('حس ' + feels + '°');
    return c.slice(0, 3);
  }
  function weatherCard() {
    var el = h('button', { type: 'button', class: 'wxm', 'aria-label': 'آب‌وهوای سرپل‌ذهاب', onclick: function () { hx.tap(); go('weather'); } });
    function paintMini(d) {
      var n = d.now, g = wxmGrad(d);
      el.style.setProperty('--w1', g[0]); el.style.setProperty('--w2', g[1]);
      var art = h('div', { class: 'wxm-art', 'aria-hidden': 'true' }); art.innerHTML = wxmScene(d);
      el.replaceChildren(art,
        h('div', { class: 'wxm-t' },
          h('small', { text: 'سرپل‌ذهاب · الان' }),
          h('div', { class: 'wxm-row' }, h('b', { dir: 'ltr', text: (n.temp == null ? '—' : n.temp) + '°' }), h('span', { text: n.label })),
          h('div', { class: 'wxm-chips' }, wxmChips(d).map(function (t) { return h('i', { text: t }); }))),
        h('span', { class: 'wx-go' }, ic('chev')));
    }
    var c = LS.get('hub:wx');
    if (c && c.d && c.d.now) paintMini(c.d);
    else el.append(h('div', { class: 'wxm-t' }, h('small', { text: 'آب‌وهوای سرپل‌ذهاب' }), h('div', { class: 'wxm-chips' }, h('i', { text: 'در حال دریافت…' }))));
    if (!c || !c.at || Date.now() - c.at > 300000) {
      api('/hub/api/weather').then(function (d) { LS.set('hub:wx', { d: d, at: Date.now() }); paintMini(d); }).catch(function () {});
    }
    return el;
  }

  /* ─── بخشِ مدیریت (تنبل) ───────────────────────────────────── */
  var M = null, mLoading = false, mQueue = [];
  function loadScript(src) {
    return new Promise(function (ok, no) {
      var s = doc.createElement('script'); s.src = src + '?v=' + V; s.onload = ok; s.onerror = no; doc.head.append(s);
    });
  }
  function withManage(fn) {
    if (M) { fn(M); return; }
    mQueue.push(fn);
    if (mLoading) return;
    mLoading = true;
    loadScript('manage.js').then(function () { return loadScript('manage2.js'); }).then(function () {
      M = window.__HubManageP2.init(core, window.__HubManageP1.init(core));
      mLoading = false;
      var q = mQueue; mQueue = []; q.forEach(function (f) { f(M); });
    }, function () { mLoading = false; mQueue = []; toast('بارگذاری بخش مدیریت ناموفق بود'); });
  }
  function buildManage() { built.manage = true; withManage(function (m) { m.renderTab(); }); }
  var core = {
    h: h, ic: ic, hx: hx, tg: tg, Sheet: Sheet, toast: toast, api: api, S: S, row: row, riconEl: riconEl, secTitle: secTitle,
    kv: kv, nn: nn, avatar: avatar, norm: norm, go: function (n, o) { go(n, o); }, cur: function () { return cur; },
    P: function () { return P; }, ensurePlayers: function (f) { return ensurePlayers(f); },
    refreshBoot: function () { return refresh(false); }
  };

  /* ─── ناوبری ─────────────────────────────────────────────── */
  var panels = { home: $('#tab-home'), players: $('#tab-players'), tours: $('#tab-tours'), manage: $('#tab-manage'), clock: $('#tab-clock'), weather: $('#tab-weather'), me: $('#tab-me') };
  var bar = $('#tabbar'), pill = $('#pill');
  /* حباب قرمزِ زیرِ تبِ فعال.
     قبلاً با getBoundingClientRect اندازه‌گیری می‌شد؛ آن مقدار «transform» را هم شامل می‌شود:
     وقتی دکمه با :active کوچک (scale .88) بود یا نوار هنوز در انیمیشنِ ورودِ خودش بود، حباب
     باریک و کج (وسطِ دو دکمه) می‌ماند. offsetLeft/offsetWidth از transform تأثیر نمی‌گیرند. */
  function movePill() {
    var b = $('button.on', bar);
    if (!b || b.hidden || !b.offsetWidth) return;
    var t = 'translateX(' + b.offsetLeft + 'px)', w = b.offsetWidth + 'px';
    var first = !pill.style.transform;
    if (first) pill.style.transition = 'none';          // اولین جایگذاری بدونِ «پرواز» از گوشه
    else if (pill.style.transform !== t) { pill.classList.remove('go'); void pill.offsetWidth; pill.classList.add('go'); }
    pill.style.width = w;
    pill.style.transform = t;
    if (first) { void pill.offsetWidth; pill.style.transition = ''; }
  }
  function go(name, opts) {
    if (name === 'players' && opts) { if (opts.f) P.f = opts.f; if (opts.sort) P.sort = opts.sort; }
    if (cur === name) { if (name === 'players' && built.players) applyPlayers(); return; }
    scrollPos[cur] = window.scrollY;
    panels[cur].hidden = true;
    var prev = cur;
    cur = name;
    doc.body.dataset.tab = name;
    panels[name].hidden = false;
    bar.querySelectorAll('button').forEach(function (b) { b.classList.toggle('on', b.dataset.go === (name === 'weather' ? 'home' : name)); });
    movePill();
    bar.querySelectorAll('button.pop').forEach(function (x) { x.classList.remove('pop'); });
    if (!built[name]) {
      if (name === 'players') buildPlayers();
      else if (name === 'tours') buildTours();
      else if (name === 'me') buildMe();
      else if (name === 'manage') buildManage();
      else if (name === 'clock') loadClock();
      else if (name === 'weather') loadWeather();
    } else if (name === 'players') { applyPlayers(); ensurePlayers(); }
    else if (name === 'manage' && M) M.renderTab();
    window.scrollTo(0, scrollPos[name] || 0);
    chrome();
    hx.sel();
    // همگام‌سازی: با هر جابه‌جاییِ تب، اگر داده‌ی خانه بیش از ۲۰ ثانیه قدیمی است، بی‌صدا تازه‌اش کن
    if (S.boot && Date.now() - lastFetch > 20000) refresh(false);
    if (prev === 'clock' && window.HubClock && window.HubClock.onHide) window.HubClock.onHide();
    if (name === 'clock' && window.HubClock && window.HubClock.onShow) window.HubClock.onShow();
    if (prev === 'weather') { popBack(); if (window.HubWeather && window.HubWeather.onHide) window.HubWeather.onHide(); }
    if (name === 'weather') { pushBack(function () { go('home'); }); if (window.HubWeather && window.HubWeather.onShow) window.HubWeather.onShow(); }
  }
  bar.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-go]'); if (b) go(b.dataset.go);
  });
  window.addEventListener('resize', movePill);

  /* ─── دروازه‌ها (خطا/عدمِ دسترسی) ─────────────────────────── */
  function gateCrest() {
    return h('span', { class: 'crest sm', role: 'img', 'aria-label': 'لوگوی CMS' });
  }
  var lockTimer = 0;
  function gate(kind, info) {
    bar.hidden = true;
    try { if (tg && tg.BackButton) tg.BackButton.hide(); } catch (e) {}
    try { Sheet.close(); } catch (e) {}
    var locked = kind === 403 && info && info.error === 'locked';
    var msg = locked ? [info.title, info.message]
      : kind === 403 ? ['دسترسی ندارید', 'شما نتوانستید از مراحل امنیتی عبور کنید. به‌نظر می‌رسد دسترسی شما در ربات تأیید نشده یا فعالیت سامانه‌ی مدیریتیِ مجازی توسط CSF غیرفعال شده است. لطفاً پس از اطمینان از دسترسی خود، با پشتیبانی در ارتباط باشید.']
      : kind === 401 ? ['از داخل تلگرام باز کنید', 'این صفحه فقط با دکمه‌ی «CMS» در چتِ ربات کار می‌کند.']
      : ['اتصال برقرار نشد', 'اینترنت را بررسی کنید و دوباره امتحان کنید.'];
    $('#app').replaceChildren(h('div', { class: 'gate' + (locked ? ' locked' : '') }, gateCrest(), h('h2', { text: msg[0] }), h('p', { text: msg[1] }),
      locked ? h('p', { class: 'gate-live', text: 'به‌محضِ بازشدن، خودکار وارد می‌شوید.' }) : null,
      kind !== 401 && kind !== 403 ? h('button', { class: 'btn', type: 'button', text: 'تلاش دوباره', onclick: function () { location.reload(); } }) : null));
    if (locked && !lockTimer) {
      /* هر ۱۰ ثانیه بررسی کن؛ اگر قفل باز شد، صفحه را دوباره بارگذاری کن */
      lockTimer = setInterval(function () {
        fetch('/hub/api/bootstrap', { headers: { 'X-Tg-Init-Data': (tg && tg.initData) || '' } }).then(function (r) { if (r.ok) location.reload(); }).catch(function () {});
      }, 10000);
    }
  }

  /* ─── نمایشِ تب‌ها براساسِ دسترسیِ همین مدیر ───────────────── */
  function applyCaps() {
    if (!S.boot) return;
    var show = {
      home: true,
      players: canAny('players_view', 'player_register'),
      tours: hasMatchCaps(),
      manage: canAny('player_register', 'match_create', 'match_edit', 'match_delete', 'predictions', 'elo', 'comms', 'classes', 'teams',
                     'calendar', 'admins_manage', 'settings', 'pishva_panel'),
      clock: true, weather: true, me: true
    };
    var n = 0;
    bar.querySelectorAll('button[data-go]').forEach(function (b) {
      var ok = show[b.dataset.go] !== false;
      b.hidden = !ok; if (ok) n++;
    });
    bar.classList.toggle('six', n >= 6);
    bar.classList.remove('pending');   // تا قبل از رسیدنِ دسترسی‌ها نوار پنهان است (نه «فقط سه دکمه»)
    if (!show[cur]) go('home');
    movePill();
    requestAnimationFrame(movePill);
  }

  /* ─── راه‌اندازی ─────────────────────────────────────────── */
  function renderAll() {
    applyCaps();
    renderHome();
    if (built.manage && M) M.renderTab();
    if (built.me) renderMe(); else { /* آیکونِ تبِ پروفایل را همین حالا پر کن */ renderMeIconOnly(); }
    if (built.tours) renderTours();
  }
  function renderMeIconOnly() {
    var me = S.boot.me, mi = $('#me-ic'); mi.replaceChildren();
    mi.append(doc.createTextNode(me.name ? Array.from(me.name)[0].toUpperCase() : ''));
    var im = h('img', { src: me.avatar, alt: '', decoding: 'async' });
    im.addEventListener('error', function () { im.remove(); });
    mi.append(im);
  }

  var lastFetch = 0;
  function refresh(first) {
    return api('/hub/api/bootstrap').then(function (d) {
      S.boot = d; lastFetch = Date.now(); LS.set(K_BOOT, d); renderAll();
      var pf = function () { if (d.caps && d.caps.length) withManage(function (m) { m.prefetch(); }); };
      if (window.requestIdleCallback) window.requestIdleCallback(pf); else setTimeout(pf, 800);
    }).catch(function (e) {
      if (e.code === 401 || e.code === 403) { gate(e.code, e.data); return; }
      if (first && !S.boot) gate(0);
    });
  }

  function start() {
    initTelegram();
    var cached = LS.get(K_BOOT);
    if (cached && cached.me) { S.boot = cached; renderAll(); }
    refresh(true).then(function () {
      var idle = window.requestIdleCallback || function (f) { setTimeout(f, 800); };
      idle(function () { if (!P.rows && canAny('players_view', 'player_register', 'match_create', 'match_edit')) { var c = LS.get(K_PL); if (c) setPlayers(c); ensurePlayers(true); } });
      idle(function () { loadClockLater(); loadWeatherLater(); });
    });
    doc.addEventListener('visibilitychange', function () {
      if (doc.hidden) return;
      if (S.boot && Date.now() - lastFetch > 15000) refresh(false);
      if (P.rows && canAny('players_view', 'player_register', 'match_create', 'match_edit')) ensurePlayers();
    });
    requestAnimationFrame(movePill);
    setTimeout(movePill, 250);
    if (window.ResizeObserver) {
      var ro = new ResizeObserver(movePill);
      ro.observe(bar);
      bar.querySelectorAll('button[data-go]').forEach(function (x) { ro.observe(x); });
    }
    if (doc.fonts && doc.fonts.ready) doc.fonts.ready.then(movePill);
    bar.addEventListener('animationend', function (e) { if (e.target === bar) movePill(); });
    window.addEventListener('orientationchange', function () { setTimeout(movePill, 200); });
    if (tg && tg.onEvent) { try { tg.onEvent('viewportChanged', movePill); } catch (e) {} }
  }
  function loadClockLater() { /* پیش‌بارگیریِ فایل بدونِ نمایش */
    if (clockState) return;
    var l = doc.createElement('link'); l.rel = 'prefetch'; l.as = 'script'; l.href = 'clock.js?v=' + V; doc.head.append(l);
  }

  start();
})();
