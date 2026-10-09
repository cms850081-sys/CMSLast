/* «خلاصه در پنل» — صفحه‌ی تمام‌صفحه‌ی خلاصه‌ی روزِ مدیر ارشد (سبکِ استوری، شبیهِ Samsung Daily Brief)
   • تنبل بار می‌شود (مثلِ weather.js) و فقط با دکمه‌ی «🖥️ خلاصه در پنل» یا کارتِ خانه باز می‌شود
   • داده از /hub/api/brief؛ نسخه‌ی قبلی از localStorage فوری نشان داده می‌شود و پشتِ صحنه تازه می‌شود
   • «نکته‌های رهگشا» ناهمزمان از /hub/api/brief/ai می‌آید؛ تا آن موقع (یا اگر نیامد) نکته‌های محلی نمایش داده می‌شود
   • هیچ innerHTML با داده‌ی کاربر؛ فقط textContent. انیمیشن‌ها فقط transform/opacity/stroke */
(function () {
  'use strict';

  var h, ic, hx, tg, toast, LS, api, goHome;
  var K = 'hub:brief1';
  var NS = 'http://www.w3.org/2000/svg';
  var root, stage, bgBox, progEl, hitEl, loadEl, ttlEl, playBtn;
  var D = null, built = false, shown = false, aiState = 0, aiLines = null, lastSig = '';
  var slides = [], bgs = [], segs = [], idx = 0, auto = true, paused = false, holdT = 0, outT = 0;
  var AQ_COL = ['#2fb344', '#d9b800', '#f08c00', '#e03131', '#9c36b5', '#7a1f2b'];
  var WXI = { clear: ['clear-day', 'clear-night'], partly: ['partly-day', 'partly-night'], cloud: ['cloud', 'cloud'], drizzle: ['drizzle', 'drizzle'],
    rain: ['rain', 'rain'], storm: ['storm', 'storm'], snow: ['snow', 'snow'], fog: ['fog', 'fog'] };
  var WXC = { clear: ['#1c7ed6', '#4dabf7', '#a5d8ff'], partly: ['#2f7fc9', '#6aaee6', '#b7d6ef'], cloud: ['#4b6580', '#7b92a8', '#aebdca'],
    drizzle: ['#3f566d', '#667f96', '#97aabb'], rain: ['#2f4156', '#51667c', '#7c8fa3'], storm: ['#1f2633', '#3a4458', '#586175'],
    snow: ['#5f7a96', '#93abc2', '#d3dfea'], fog: ['#6b7783', '#929ca6', '#c5ccd3'] };
  var WXN = { clear: ['#070b1f', '#101a3d', '#1d2b5c'], partly: ['#0b1230', '#17224a', '#27355f'], cloud: ['#0e1422', '#1b2335', '#2c3548'],
    drizzle: ['#0c121d', '#18212f', '#273141'], rain: ['#080d16', '#121a27', '#1f2937'], storm: ['#05070d', '#0d121c', '#171e2b'],
    snow: ['#16213a', '#2a3a58', '#46587a'], fog: ['#1a1f27', '#2b323c', '#414a56'] };
  var PART_C = { 'بامداد': ['#1b1f4b', '#6a4c93', '#ff9e7d'], 'صبح': ['#2b6cb0', '#63a4e8', '#ffd9a0'], 'ظهر': ['#1c7ed6', '#4dabf7', '#a5d8ff'],
    'عصر': ['#3b2a6b', '#e2663d', '#ffb765'], 'شب': ['#070b1f', '#141c45', '#2b3a7a'] };
  var ROLE = { tournament_manager: 'مسئول مسابقات', security_manager: 'مسئول انتظامات' };

  /* ─── ابزارها ─────────────────────────────────────────────── */
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function rnd(a, b) { return a + Math.random() * (b - a); }
  function reduce() { try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; } }
  function faS(t) { return String(t).replace(/[0-9]/g, function (d) { return '۰۱۲۳۴۵۶۷۸۹'[+d]; }); }
  function fa(n) { return Number(n).toLocaleString('fa-IR', { useGrouping: false, maximumFractionDigits: 1 }); }
  function svg(tag, attrs) { var n = document.createElementNS(NS, tag); for (var k in attrs) n.setAttribute(k, attrs[k]); return n; }
  function div(cls, kids) { return h.apply(null, ['div', { class: cls }].concat(kids || [])); }
  function sp(txt, cls) { return h('span', { class: cls || null, text: txt }); }
  function countTo(el, to, ms) {
    to = Number(to) || 0;
    if (reduce() || !to) { el.textContent = fa(to); return; }
    var t0 = performance.now();
    (function step(t) {
      var p = clamp((t - t0) / ms, 0, 1), e = 1 - Math.pow(1 - p, 3);
      el.textContent = fa(Math.round(to * e));
      if (p < 1) requestAnimationFrame(step);
    })(t0);
  }
  function cu(v, cls) { var n = h('span', { class: 'num ' + (cls || ''), text: fa(0) }); n.setAttribute('data-cu', v == null ? 0 : v); return n; }
  function rel(s) {
    try {
      var d = new Date(String(s).replace(' ', 'T')), sec = (Date.now() - d.getTime()) / 1000;
      if (!isFinite(sec) || sec < 0) return '';
      var f = new Intl.RelativeTimeFormat('fa', { numeric: 'auto' });
      if (sec < 3600) return f.format(-Math.max(1, Math.round(sec / 60)), 'minute');
      if (sec < 86400) return f.format(-Math.round(sec / 3600), 'hour');
      return f.format(-Math.round(sec / 86400), 'day');
    } catch (e) { return ''; }
  }

  /* ─── ساختِ اسلایدها ─────────────────────────────────────── */
  var cnt = 0;
  function R(n) { n.classList.add('rv'); n.style.setProperty('--i', cnt++); return n; }
  function slide(id, dur, theme) {
    cnt = 0;
    var el = h('section', { class: 'bf-s', 'data-id': id });
    return { id: id, el: el, dur: dur, theme: theme, fns: [] };
  }
  function head(title, chip) { return R(h('p', { class: 'bf-h1' }, sp(title), chip ? sp(chip, 'chip') : null)); }

  function skyFor(d) {
    var hr = d.hour + d.minute / 60, day = hr >= 6 && hr < 18;
    var p = day ? (hr - 6) / 12 : ((hr + 6) % 24) / 12;
    var orb = h('div', { class: 'orb ' + (day ? 'sun' : 'moon') });
    orb.style.setProperty('--oy', Math.round((1 - Math.sin(clamp(p, 0, 1) * Math.PI)) * 46) + 'px');
    var box = div('bf-sky', [orb]);
    var i, c;
    for (i = 0; i < 4; i++) { c = h('i', { class: 'cloud' }); c.style.cssText = '--y:' + Math.round(rnd(14, 46)) + '%;--t:' + Math.round(rnd(30, 56)) + 's;--dl:-' + Math.round(rnd(0, 40)) + 's;opacity:' + rnd(.5, 1).toFixed(2); box.append(c); }
    if (!day || d.hour < 7) for (i = 0; i < 34; i++) { c = h('i', { class: 'star' }); c.style.cssText = 'left:' + rnd(2, 98).toFixed(1) + '%;top:' + rnd(3, 55).toFixed(1) + '%;--t:' + rnd(2, 5).toFixed(1) + 's;--dl:-' + rnd(0, 4).toFixed(1) + 's'; box.append(c); }
    return box;
  }

  function sIntro() {
    var s = slide('intro', 6500, PART_C[D.part] || PART_C['صبح']);
    var greet = D.part === 'صبح' ? 'صبح بخیر' : D.part === 'ظهر' ? 'ظهر بخیر' : D.part === 'عصر' ? 'عصر بخیر' : 'شب بخیر';
    var w = D.weather && D.weather.now;
    s.el.append(skyFor(D),
      R(div('hello', [h('span', { class: 'part', text: D.weekday + ' · ' + D.date }), h('h1', { class: 'bf-lead', text: greet + '، ' + D.name }),
        h('p', { class: 'date', text: 'این هم خلاصه‌ی روزِ تو؛ همه‌چیز در یک نگاه.' })])),
      R(div('kpis', [
        div('kpi', [h('b', null, cu(D.stats.matches)), sp('مسابقه ' + (D.window.label || 'دیشب'))]),
        div('kpi', [h('b', null, cu(D.stats.pending)), sp('منتظر نتیجه')]),
        div('kpi', [h('b', null, w && w.temp != null ? cu(Math.round(w.temp), 'tp') : sp('—')), sp('دمای الان')])])),
      R(h('p', { class: 'hint-tap', text: '‹ برای ادامه بزن' })));
    return s;
  }

  function localTips() {
    var t = [], st = D.stats, w = D.weather, td = w && w.today;
    if (st.pending > 0) t.push(st.pending + ' مسابقه هنوز منتظر ثبت نتیجه است' + (D.pending.length ? ' (' + D.pending.slice(0, 2).map(function (p) { return p.cls; }).join('، ') + ')' : '') + '.');
    if (st.matches > 0) t.push(fa(st.matches) + ' مسابقه ' + (D.window.label || 'دیشب') + ' ثبت شده است.');
    if (td && td.pop >= 50) t.push('امروز احتمال بارش ' + fa(td.pop) + '٪ است؛ چتر را فراموش نکن.');
    else if (td && td.tmax != null && td.tmax >= 36) t.push('هوا گرم می‌شود (تا ' + fa(td.tmax) + '°)؛ آب همراهت باشد.');
    else if (td && td.tmin != null && td.tmin <= 3) t.push('هوا سرد است (کمینه ' + fa(td.tmin) + '°)؛ گرم بپوش.');
    if (w && w.air && w.air.level >= 3) t.push('آلودگی هوا بالاست (' + w.air.label + ')؛ ماسک بزن.');
    var warn = D.players.filter(function (p) { return p.warn > 0; });
    if (warn.length) t.push(warn[0].name + ' اخطار فعال دارد.');
    if (st.tasks > 0) t.push(fa(st.tasks) + ' وظیفه‌ی باز در انتظار پیگیری است.');
    if (!t.length) t.push('امروز خبرِ نگران‌کننده‌ای نیست؛ روزِ آرامی پیش رو داری.');
    return t.slice(0, 4);
  }
  function sAi() {
    var s = slide('ai', 8500, ['#150d24', '#2d1b55', '#7a3cff']);
    var box = div('tips', []); box.style.cssText = 'display:flex;flex-direction:column;gap:10px';
    s.box = box;
    fillTips(box);
    s.el.append(R(div('', [div('ai-orb', [h('i', null, ic('bolt'))])])), head('نکته‌های رهگشا', aiLines ? 'هوشمند' : 'تحلیل…'),
      R(h('p', { class: 'bf-lead', text: 'مهم‌ترین‌های امروز' })), box, R(h('p', { class: 'ai-note', text: aiLines ? 'تحلیل‌شده توسط رهگشا' : 'در حال تحلیل داده‌های امروز…' })));
    return s;
  }
  function fillTips(box) {
    box.replaceChildren();
    var lines = aiLines || localTips();
    lines.forEach(function (t, i) {
      var n = h('div', { class: 'tip rv' }, h('em', { text: fa(i + 1) }), h('span', { text: t }));
      n.style.setProperty('--i', 3 + i); box.append(n);
    });
    if (!aiLines && aiState === 1) { var k = h('div', { class: 'tip sk rv' }); k.style.setProperty('--i', 3 + lines.length); box.append(k); }
  }

  var RES = { white: ['سفید برد', 'b-white'], black: ['سیاه برد', 'b-black'], draw: ['تساوی', 'b-draw'], cancelled: ['لغو', 'b-cancelled'], none: ['بدون نتیجه', 'b-none'] };
  function sMatches() {
    var s = slide('matches', 7500, ['#1d1b3a', '#43286f', '#c4121f']), st = D.stats;
    s.el.append(head('♟ مسابقه‌های ' + (D.window.label || 'دیشب')));
    if (!st.matches) {
      s.el.append(R(h('p', { class: 'bf-big' }, cu(0))), R(h('p', { class: 'bf-lead', text: 'مسابقه‌ای ثبت نشده' })),
        R(h('p', { class: 'bf-sub', text: 'در این بازه نتیجه‌ای در سیستم ثبت نشده است.' })));
      return s;
    }
    var segs_ = [['white', '#f4f5f8', 'سفید برد'], ['black', '#9aa3b8', 'سیاه برد'], ['draw', '#ffb347', 'تساوی']];
    var other = st.matches - st.white - st.black - st.draw;
    var tot = st.matches, off = 0, svgR = svg('svg', { viewBox: '0 0 120 120' });
    svgR.append(svg('circle', { class: 'bgc', cx: 60, cy: 60, r: 50, pathLength: 100 }));
    var legend = div('legend', []);
    segs_.concat(other > 0 ? [['x', 'rgba(255,255,255,.4)', 'بدون نتیجه/لغو']] : []).forEach(function (g) {
      var n = g[0] === 'x' ? other : st[g[0]];
      if (!n) return;
      var len = Math.max(2, n / tot * 100 - (tot > n ? 1.2 : 0));
      var c = svg('circle', { class: 'seg', cx: 60, cy: 60, r: 50, pathLength: 100, stroke: g[1], 'stroke-dashoffset': -off });
      c.style.setProperty('--len', len.toFixed(2)); svgR.append(c); off += n / tot * 100;
      var sw = h('i'); sw.style.background = g[1];
      legend.append(div('', [sw, sp(g[2]), h('b', { text: fa(n) })]));
    });
    var mid = div('mid', [h('div', null, h('b', null, cu(tot)), sp('مسابقه'))]);
    s.el.append(R(div('ring-w', [div('ring', [svgR, mid]), legend])));
    var list = div('mlist', []), max = 5;
    D.matches.slice(0, max).forEach(function (m) {
      var r = RES[m.result] || RES.none;
      list.append(R(div('mrow', [div('pl', [sp(m.wn), h('small', { text: m.wc })]), sp('در برابر', 'vs'), div('pl', [sp(m.bn), h('small', { text: m.bc })]), sp(r[0], 'badge ' + r[1])])));
    });
    if (D.matches.length > max) list.append(R(h('p', { class: 'more', text: '+ ' + fa(D.matches.length - max) + ' مسابقه‌ی دیگر' })));
    s.el.append(list);
    return s;
  }

  function sPending() {
    var s = slide('pending', 7000, ['#3d1f0b', '#a04a10', '#f2a33a']), st = D.stats;
    s.el.append(head('منتظر ثبت نتیجه'),
      R(h('p', { class: 'bf-big' }, cu(st.pending), h('small', null, h('span', { class: 'hg', text: '⏳' })))),
      R(h('p', { class: 'bf-sub', text: 'کلاس‌های زیر امروز باید مسابقه‌هایشان را برگزار و نتیجه را ثبت کنند.' })));
    D.pending.slice(0, 4).forEach(function (g) {
      var c = div('card pc', [h('h4', null, sp(g.cls), sp(fa(g.items.length) + ' مسابقه'))]);
      g.items.slice(0, 3).forEach(function (p) { c.append(h('div', { class: 'pair', text: p.w + '  ⟷  ' + p.b })); });
      if (g.items.length > 3) c.append(h('div', { class: 'pair', text: '+ ' + fa(g.items.length - 3) + ' مورد دیگر' }));
      s.el.append(R(c));
    });
    return s;
  }

  function sPlayers() {
    var s = slide('players', 7500, ['#0f2a3f', '#1a5d77', '#38b2ac']);
    s.el.append(head('بازیکنانِ درگیر', fa(D.players.length) + ' نفر'), R(h('p', { class: 'bf-lead', text: 'چه کسانی به میدان رفتند؟' })));
    D.players.slice(0, 5).forEach(function (p) {
      var tot = (p.w + p.d + p.l) || 1;
      var seg = function (c, v) { var i = h('i', { class: c }); i.style.setProperty('--f', String(v)); i.style.setProperty('--i', cnt); return i; };
      var av = div('av', [sp(Array.from(p.name || '؟')[0]), p.elite ? sp('⭐', 'st') : null]);
      s.el.append(R(div('card plc', [
        div('top', [av, div('nm', [sp(p.name), h('small', { text: (p.cls || '—') + (p.warn ? ' · ' + fa(p.warn) + ' اخطار' : '') })]),
          h('span', { class: 'num', style: 'margin-inline-start:auto;font-weight:800', text: fa(p.w) + ' - ' + fa(p.d) + ' - ' + fa(p.l) })]),
        div('wdl', [seg('w', p.w / tot), seg('d', p.d / tot), seg('l', p.l / tot)]),
        div('wdl-l', [sp('برد'), sp('تساوی'), sp('باخت')])])));
    });
    if (D.players.length > 5) s.el.append(R(h('p', { class: 'more', text: '+ ' + fa(D.players.length - 5) + ' نفر دیگر' })));
    return s;
  }

  function sWeather() {
    var w = D.weather, n = w.now, t = w.today || {}, kind = WXI[n.kind] ? n.kind : 'cloud', day = !!n.is_day;
    var s = slide('wx', 8500, (day ? WXC : WXN)[kind]);
    var icoSvg = svg('svg', { class: 'wx-ic', viewBox: '0 0 48 48' });
    icoSvg.append(svg('use', { href: '#wi-' + WXI[kind][day ? 0 : 1] }));
    var sky = div('bf-sky', []);
    var i, p;
    if (kind === 'rain' || kind === 'drizzle' || kind === 'storm' || kind === 'snow') {
      for (i = 0; i < (kind === 'snow' ? 24 : 30); i++) {
        p = h('i', { class: 'pt' + (kind === 'snow' ? ' snow' : '') });
        p.style.cssText = '--x:' + rnd(0, 100).toFixed(1) + '%;--t:' + (kind === 'snow' ? rnd(3.5, 6) : rnd(.7, 1.3)).toFixed(2) + 's;--dl:-' + rnd(0, 5).toFixed(2) + 's';
        sky.append(p);
      }
    }
    for (i = 0; i < 3; i++) { p = h('i', { class: 'cloud' }); p.style.cssText = '--y:' + Math.round(rnd(12, 40)) + '%;--t:' + Math.round(rnd(34, 60)) + 's;--dl:-' + Math.round(rnd(0, 40)) + 's'; sky.append(p); }
    s.el.append(sky, head('🌤 هوای ' + (w.city || 'سرپل‌ذهاب'), w.stale ? 'قدیمی' : null));
    s.el.append(R(div('wx-hero', [icoSvg, h('div', null, h('div', { class: 'wx-t' }, cu(Math.round(n.temp == null ? 0 : n.temp)), h('sup', { text: '°' })),
      h('div', { class: 'wx-l', text: n.label || '' }),
      n.feels != null ? h('div', { class: 'wx-fl' }, sp('احساس‌شده '), h('span', { class: 'num', text: fa(Math.round(n.feels)) + '°' })) : null)])));
    if (t.tmin != null && t.tmax != null) {
      var pos = clamp((n.temp - t.tmin) / Math.max(1, t.tmax - t.tmin), 0, 1) * 100;
      var trk = div('trk', [div('mk', [])]); trk.firstChild.style.setProperty('--p', pos.toFixed(1) + '%');
      s.el.append(R(div('card', [h('div', { class: 'trk-l' }, h('span', { text: fa(t.tmin) + '°' }), h('span', { text: 'محدوده‌ی امروز' }), h('span', { text: fa(t.tmax) + '°' })), trk])));
    }
    var cells = [];
    if (t.pop != null) cells.push(['احتمال بارش', fa(t.pop) + '٪']);
    if (t.uv != null) cells.push(['شاخص UV', fa(t.uv)]);
    if (t.sunrise) cells.push(['طلوع', faS(t.sunrise)]);
    if (t.sunset) cells.push(['غروب', faS(t.sunset)]);
    if (n.hum != null) cells.push(['رطوبت', fa(n.hum) + '٪']);
    if (n.wind != null) cells.push(['باد', fa(n.wind) + ' km/h']);
    if (cells.length) s.el.append(R(div('wx-grid', cells.slice(0, 4).map(function (c) { return div('wx-c', [h('small', { text: c[0] }), h('b', { class: 'num', text: c[1] })]); }))));
    if (w.air && w.air.aqi != null) {
      var aq = clamp(w.air.aqi, 0, 300) / 300 * 100, col = AQ_COL[clamp(w.air.level || 0, 0, 5)];
      var g = svg('svg', { class: 'gauge', viewBox: '0 0 110 62' });
      g.append(svg('path', { class: 'tr', d: 'M10 56A45 45 0 0 1 100 56', pathLength: 100 }));
      var fl = svg('path', { class: 'fl', d: 'M10 56A45 45 0 0 1 100 56', pathLength: 100, stroke: col }); fl.style.setProperty('--len', aq.toFixed(1)); g.append(fl);
      s.el.append(R(div('card', [h('div', { class: 'trk-l', style: 'direction:rtl' }, h('span', { text: 'کیفیت هوا' }), h('span', null, sp(w.air.label + '  '), h('span', { class: 'num', text: 'AQI ' + fa(w.air.aqi) }))), g])));
    }
    if (w.advice && w.advice[0] && w.advice[0].text) s.el.append(R(h('div', { class: 'adv', text: '👕 ' + w.advice[0].text })));
    return s;
  }

  function sTimetable() {
    var tt = D.timetable, s = slide('tt', 7000, ['#0e2a1f', '#1f6b4f', '#7bd88f']);
    s.el.append(head('📚 برنامه‌ی درسیِ امروز', tt.day));
    if (tt.holiday) { s.el.append(R(h('div', { class: 'holi', text: '🎉' })), R(h('p', { class: 'bf-lead', style: 'text-align:center', text: tt.note })));
      return s; }
    if (!tt.rows.length) { s.el.append(R(h('p', { class: 'bf-lead', text: 'برنامه‌ای ثبت نشده' })), R(h('p', { class: 'bf-sub', text: tt.note }))); return s; }
    s.el.append(R(h('p', { class: 'bf-lead', text: 'زنگ‌ها به ترتیب' })));
    tt.rows.forEach(function (r) {
      var per = div('per', r.lessons.map(function (l, k) { var c = h('span', null, h('em', { text: fa(k + 1) }), l); c.style.setProperty('--k', k); return c; }));
      s.el.append(R(div('card tt', [h('h4', { text: r.cls }), per])));
    });
    return s;
  }

  function sPeople() {
    var s = slide('people', 7000, ['#201436', '#4b2a7b', '#b35cff']);
    s.el.append(head('👥 مدیران و وظایف'));
    if (D.visits.length) {
      s.el.append(R(h('p', { class: 'sec', text: 'آخرین بازدید مدیران' })));
      D.visits.slice(0, 4).forEach(function (v) {
        s.el.append(R(div('vis', [div('av', [sp(Array.from(v.name || '؟')[0])]), div('nm', [sp(v.name), h('small', { text: ROLE[v.role] || 'مدیر' })]), sp(rel(v.last_active) || '—', 'when')])));
      });
    }
    if (D.tasks.length) {
      s.el.append(R(h('p', { class: 'sec', text: 'وظیفه‌های باز' })));
      D.tasks.slice(0, 4).forEach(function (t) { s.el.append(R(div('tk', [div('cb', []), div('', [sp(t.title), h('small', { text: 'برای ' + t.who })])]))); });
    }
    return s;
  }

  function sEnd() {
    var s = slide('end', 0, ['#2a0a10', '#a11019', '#ff6a74']);
    var box = div('bf-sky', []), cols = ['#ffd23f', '#ffffff', '#4dabf7', '#ff6a74', '#69db7c'], i, c;
    for (i = 0; i < 22; i++) { c = h('i', { class: 'spark' }); c.style.cssText = '--x:' + rnd(0, 100).toFixed(1) + '%;--c:' + cols[i % 5] + ';--t:' + rnd(3.5, 6).toFixed(1) + 's;--dl:-' + rnd(0, 6).toFixed(1) + 's;--dx:' + Math.round(rnd(-50, 50)) + 'px'; box.append(c); }
    s.el.append(box, R(div('end', [h('div', { class: 'big-e', text: '🌟' }), h('h2', { class: 'bf-lead', text: 'روزِ خوبی داشته باشی، ' + D.name }), h('p', { class: 'bf-sub', text: 'خلاصه‌ی امروز تمام شد.' }),
      div('end-b', [h('button', { type: 'button', text: 'بازگشت به پنل', onclick: function () { hx.tap(); goHome(); } }),
        h('button', { type: 'button', class: 'g', text: 'پخش دوباره', onclick: function () { hx.tap(); show(0, 1); } })])])));
    return s;
  }

  /* ─── ساختِ کلِ صحنه ─────────────────────────────────────── */
  function build(keep) {
    stage.replaceChildren(); bgBox.replaceChildren(); progEl.replaceChildren();
    slides = []; bgs = []; segs = [];
    var list = [sIntro(), sAi()];
    list.push(sMatches());
    if (D.stats.pending > 0) list.push(sPending());
    if (D.players.length) list.push(sPlayers());
    if (D.weather) list.push(sWeather());
    list.push(sTimetable());
    if (D.visits.length || D.tasks.length) list.push(sPeople());
    list.push(sEnd());
    slides = list;
    slides.forEach(function (s) {
      stage.append(s.el);
      var bg = h('div', { class: 'bf-bg' });
      bg.style.background = 'linear-gradient(168deg,' + s.theme[0] + ' 0%,' + s.theme[1] + ' 58%,' + s.theme[2] + ' 130%)';
      bgBox.append(bg); bgs.push(bg);
      var b = h('b'), sg = h('i', null, b);
      sg.addEventListener('animationend', function (e) { if (e.target === b && sg.classList.contains('cur') && shown && !paused && auto) next(); });
      progEl.append(sg); segs.push(sg);
    });
    built = true;
    idx = clamp(keep || 0, 0, slides.length - 1);
    show(idx, 1, true);
  }

  function show(i, dir, silent) {
    if (!slides.length) return;
    i = clamp(i, 0, slides.length - 1);
    var old = slides[idx];
    if (old && i !== idx) {
      old.el.classList.remove('on', 'fromprev'); old.el.classList.add('out'); old.el.classList.toggle('prev', dir < 0);
      (function (e) { setTimeout(function () { e.classList.remove('out', 'prev'); }, 480); })(old.el);
    }
    idx = i;
    var cur = slides[i];
    cur.el.classList.remove('on', 'out', 'prev', 'fromprev'); void cur.el.offsetWidth;
    if (dir < 0) cur.el.classList.add('fromprev');
    cur.el.classList.add('on');
    bgs.forEach(function (b, k) { b.classList.toggle('on', k === i); });
    segs.forEach(function (sg, k) {
      sg.classList.remove('done', 'cur'); void sg.offsetWidth;
      if (k < i) sg.classList.add('done');
      else if (k === i && cur.dur) { sg.style.setProperty('--d', cur.dur + 'ms'); sg.classList.add('cur'); }
      else if (k === i) sg.classList.add('done');
    });
    root.classList.toggle('bf-manual', !auto || reduce());
    var cuEls = cur.el.querySelectorAll('[data-cu]');
    for (var k = 0; k < cuEls.length; k++) (function (e) { e.textContent = fa(0); setTimeout(function () { countTo(e, e.getAttribute('data-cu'), 1000); }, 260); })(cuEls[k]);
    if (!silent) hx.sel();
  }
  function next() { if (idx < slides.length - 1) show(idx + 1, 1); }
  function prev() { if (idx > 0) show(idx - 1, -1); }

  /* ─── لمس: ضربه/نگه‌داشتن/کشیدن ─────────────────────────── */
  var px = 0, py = 0, pt = 0, long_ = false;
  function setPause(v) { paused = v; root.classList.toggle('bf-paused', v); }
  function onDown(e) {
    px = e.clientX; py = e.clientY; pt = Date.now(); long_ = false;
    clearTimeout(holdT); holdT = setTimeout(function () { long_ = true; setPause(true); }, 220);
  }
  function onUp(e) {
    clearTimeout(holdT);
    var dx = e.clientX - px, dy = e.clientY - py;
    if (long_) { long_ = false; setPause(false); if (Math.abs(dx) < 40) return; }
    if (Math.abs(dy) > 90 && Math.abs(dy) > Math.abs(dx) && dy > 0) { goHome(); return; }
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy)) { if (dx < 0) next(); else prev(); return; }
    if (Math.abs(dx) < 14 && Math.abs(dy) < 14) {
      var r = hitEl.getBoundingClientRect(), x = (e.clientX - r.left) / r.width;
      if (x < .5) next(); else prev();            // راست‌به‌چپ: لمسِ سمتِ چپ = بعدی
    }
  }
  function onCancel() { clearTimeout(holdT); if (long_) { long_ = false; setPause(false); } }

  /* ─── داده ───────────────────────────────────────────────── */
  function sig(d) { try { return JSON.stringify([d.stats, d.matches.length, d.pending.length, d.weather && d.weather.now && d.weather.now.temp, d.tasks.length]); } catch (e) { return String(Date.now()); } }
  function setLoad(state, msg) {
    loadEl.classList.toggle('hide', state === 'hide');
    if (state === 'hide') return;
    loadEl.replaceChildren(h('div', null,
      state === 'load' ? h('div', { class: 'sp' }) : null,
      h('h3', { text: state === 'load' ? 'در حال آماده‌سازی خلاصه…' : 'خلاصه بارگذاری نشد' }),
      state === 'err' ? h('p', { class: 'bf-sub', text: msg || 'اتصال را بررسی کنید.' }) : null,
      state === 'err' ? h('button', { type: 'button', text: 'تلاش دوباره', onclick: function () { load(); } }) : null,
      state === 'err' ? h('button', { type: 'button', style: 'background:rgba(255,255,255,.2);color:#fff;margin-inline-start:8px', text: 'بازگشت', onclick: goHome }) : null));
  }
  function load() {
    var cached = LS.get(K);
    if (cached && cached.ok && !D) { D = cached; lastSig = sig(D); build(0); setLoad('hide'); } else if (!D) setLoad('load');
    api('/hub/api/brief').then(function (d) {
      if (!d || !d.ok) throw new Error('bad');
      var changed = sig(d) !== lastSig;
      D = d; lastSig = sig(d); LS.set(K, d);
      if (!built || changed) build(built ? idx : 0);
      setLoad('hide');
      askAi();
    }).catch(function (e) {
      if (D) return;
      if (e && e.code === 403) { toast('این بخش فقط برای مدیر ارشد است'); goHome(); return; }
      setLoad('err');
    });
  }
  function askAi() {
    if (aiState === 1 || aiState === 2) return;
    aiState = 1;
    var s = slides.filter(function (x) { return x.id === 'ai'; })[0]; if (s) fillTips(s.box);
    api('/hub/api/brief/ai', {}).then(function (r) {
      if (r && r.ok && r.text && r.text.length) { aiLines = r.text; aiState = 2; } else aiState = 3;
    }).catch(function () { aiState = 3; }).then(function () {
      var s2 = slides.filter(function (x) { return x.id === 'ai'; })[0];
      if (!s2) return;
      fillTips(s2.box);
      var chip = s2.el.querySelector('.chip'), note = s2.el.querySelector('.ai-note');
      if (chip) chip.textContent = aiLines ? 'هوشمند' : 'خلاصه‌ی داده‌ها';
      if (note) note.textContent = aiLines ? 'تحلیل‌شده توسط رهگشا' : 'تحلیل هوشمند الان در دسترس نیست؛ این‌ها از روی داده‌هاست.';
    });
  }

  /* ─── API ماژول ──────────────────────────────────────────── */
  function mount(el, ctx) {
    h = ctx.h; ic = ctx.ic; hx = ctx.hx; tg = ctx.tg; toast = ctx.toast; LS = ctx.LS; api = ctx.api; goHome = ctx.goHome;
    bgBox = div('bf-bgs', []); stage = div('bf-stage', []); progEl = div('bf-prog', []);
    ttlEl = div('bf-ttl', [h('i', { class: 'dot' }), sp('خلاصه‌ی امروز')]);
    playBtn = h('button', { class: 'bf-btn', type: 'button', 'aria-label': 'پخش خودکار', onclick: function () {
      auto = !auto; hx.tap(); playBtn.replaceChildren(ic(auto ? 'pause' : 'play'));
      root.classList.toggle('bf-manual', !auto || reduce());
      if (auto && slides[idx]) show(idx, 1, true);
    } }, ic('pause'));
    var closeBtn = h('button', { class: 'bf-btn', type: 'button', 'aria-label': 'بستن', onclick: function () { hx.tap(); goHome(); } }, ic('close'));
    hitEl = div('bf-hit', [h('i'), h('i')]);
    loadEl = div('bf-load', []);
    root = div('bf', [bgBox, stage, div('bf-top', [progEl, div('bf-bar', [ttlEl, div('bf-btns', [playBtn, closeBtn])])]), hitEl, loadEl]);
    hitEl.addEventListener('pointerdown', onDown);
    hitEl.addEventListener('pointerup', onUp);
    hitEl.addEventListener('pointercancel', onCancel);
    hitEl.addEventListener('pointerleave', onCancel);
    el.replaceChildren(root);
    if (reduce()) { auto = false; root.classList.add('bf-manual'); playBtn.replaceChildren(ic('play')); }
    document.addEventListener('visibilitychange', function () { if (shown) setPause(document.hidden); });
    setLoad('load');
    load();
  }
  function onShow() {
    shown = true; setPause(false);
    if (built && slides[idx]) { show(idx, 1, true); load(); }
  }
  function onHide() { shown = false; setPause(true); clearTimeout(holdT); }

  window.HubBrief = { mount: mount, onShow: onShow, onHide: onHide };
})();
