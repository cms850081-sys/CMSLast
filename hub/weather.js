/* بخشِ آب‌وهوا (سرپل‌ذهاب) — تنبل بار می‌شود، مثلِ clock.js.
   • صحنه‌ی زنده: آسمانِ متغیر با ساعتِ روز، خورشید/ماهِ واقعی، ابر، باران، برف، رعدوبرق، مه، گردوغبار، ستاره‌ی شبانه
   • کانواس فقط وقتی تبِ آب‌وهوا باز و صفحه دیدنی‌ست می‌چرخد (باتری!) و با «کاهش حرکت» سبک می‌شود
   • داده از /hub/api/weather؛ نسخه‌ی قبلی از localStorage فوری نشان داده می‌شود و در پس‌زمینه تازه می‌شود */
(function () {
  'use strict';

  var h, ic, hx, tg, toast, LS, api, goHome;
  var K = 'hub:wx';
  var D = null, loading = false, lastAt = 0, shown = false, aiBusy = false, typeT = 0, R = {};
  var NS = 'http://www.w3.org/2000/svg';

  /* ─── ثابت‌ها ─────────────────────────────────────────────── */
  var SKY = {
    clear: [['#1c7ed6', '#4dabf7', '#a5d8ff'], ['#070b1f', '#101a3d', '#1d2b5c']],
    partly: [['#2f7fc9', '#6aaee6', '#b7d6ef'], ['#0b1230', '#17224a', '#27355f']],
    cloud: [['#4b6580', '#7b92a8', '#aebdca'], ['#0e1422', '#1b2335', '#2c3548']],
    drizzle: [['#3f566d', '#667f96', '#97aabb'], ['#0c121d', '#18212f', '#273141']],
    rain: [['#2f4156', '#51667c', '#7c8fa3'], ['#080d16', '#121a27', '#1f2937']],
    storm: [['#1f2633', '#3a4458', '#586175'], ['#05070d', '#0d121c', '#171e2b']],
    snow: [['#5f7a96', '#93abc2', '#d3dfea'], ['#16213a', '#2a3a58', '#46587a']],
    fog: [['#6b7783', '#929ca6', '#c5ccd3'], ['#1a1f27', '#2b323c', '#414a56']]
  };
  var AQ_COL = ['#2fb344', '#d9b800', '#f08c00', '#e03131', '#9c36b5', '#7a1f2b'];
  var DIRS = ['شمال', 'شمال‌شرق', 'شرق', 'جنوب‌شرق', 'جنوب', 'جنوب‌غرب', 'غرب', 'شمال‌غرب'];
  var WD = ['یکشنبه', 'دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه', 'شنبه'];

  /* ─── ابزارها ─────────────────────────────────────────────── */
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function rnd(a, b) { return a + Math.random() * (b - a); }
  function reduce() { try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; } }
  function svg(tag, attrs, kids) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    (kids || []).forEach(function (c) { n.appendChild(c); });
    return n;
  }
  function raw(html) { var d = document.createElement('div'); d.innerHTML = html; return d.firstChild; } // فقط رشته‌های ثابتِ همین فایل
  function tehranNow() { var n = new Date(); return new Date(n.getTime() + n.getTimezoneOffset() * 60000 + 12600000); }
  function mins(hhmm) { if (!hhmm) return null; var p = hhmm.split(':'); return +p[0] * 60 + +p[1]; }
  function nowMin() { var t = tehranNow(); return t.getHours() * 60 + t.getMinutes(); }
  function fmtLen(m) { return Math.floor(m / 60) + ' ساعت و ' + (m % 60) + ' دقیقه'; }
  function tempHue(t) { return Math.round(clamp((42 - t) / 47, 0, 1) * 240); } // سرد=آبی، گرم=قرمز
  function tcol(t) { return 'hsl(' + tempHue(t) + ',85%,58%)'; }
  function dayName(iso, i) {
    if (i === 0) return 'امروز';
    if (i === 1) return 'فردا';
    var d = new Date(iso + 'T12:00:00');
    return WD[d.getDay()];
  }
  function dayFa(iso) {
    try { return new Date(iso + 'T12:00:00').toLocaleDateString('fa-IR-u-ca-persian', { day: 'numeric', month: 'long' }); } catch (e) { return ''; }
  }
  function countTo(el, to, ms) {
    if (reduce() || to == null) { el.textContent = to == null ? '—' : to; return; }
    var t0 = performance.now();
    (function step(t) {
      var p = clamp((t - t0) / ms, 0, 1), e = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(to * e);
      if (p < 1) requestAnimationFrame(step);
    })(t0);
  }

  /* ─── آیکون‌های آب‌وهوا (SVG درون‌خطی) ─────────────────────── */
  function cloudP(c) { return '<path d="M14 37a8.5 8.5 0 0 1-.8-16.9A11.5 11.5 0 0 1 35 18a8.6 8.6 0 0 1 1 19z" fill="' + c + '"/>'; }
  var SUN = '<circle cx="24" cy="24" r="8" fill="#ffd43b"/><g stroke="#ffd43b" stroke-width="3" stroke-linecap="round"><path d="M24 7v5M24 36v5M7 24h5M36 24h5M12 12l3.5 3.5M32.5 32.5 36 36M12 36l3.5-3.5M32.5 15.5 36 12"/></g>';
  var MOON = '<path d="M30 8a16 16 0 1 0 10 28A13 13 0 0 1 30 8z" fill="#e9ecef"/>';
  function wicon(kind, day, px) {
    var body, W = '#eef2f7', G = '#9aa7b6';
    switch (kind) {
      case 'clear': body = day ? SUN : MOON; break;
      case 'partly': body = (day ? '<g transform="translate(-6 -7) scale(.75)">' + SUN + '</g>' : '<g transform="translate(-5 -8) scale(.7)">' + MOON + '</g>') + '<g transform="translate(3 3)">' + cloudP(W) + '</g>'; break;
      case 'drizzle': body = cloudP(W) + '<g stroke="#74c0fc" stroke-width="2.4" stroke-linecap="round"><path d="M16 41v2M24 41v3M32 41v2"/></g>'; break;
      case 'rain': body = cloudP(G) + '<g stroke="#4dabf7" stroke-width="3" stroke-linecap="round"><path d="M16 40l-2 5M24 40l-2 5M32 40l-2 5"/></g>'; break;
      case 'storm': body = cloudP('#7b8794') + '<path d="M25 33l-6 9h5l-2 6 8-10h-5z" fill="#ffd43b"/>'; break;
      case 'snow': body = cloudP(W) + '<g fill="#fff"><circle cx="16" cy="42" r="2"/><circle cx="24" cy="45" r="2"/><circle cx="32" cy="42" r="2"/></g>'; break;
      case 'fog': body = cloudP('#cfd6de') + '<g stroke="#adb5bd" stroke-width="3" stroke-linecap="round"><path d="M10 41h28M14 46h22"/></g>'; break;
      default: body = cloudP(W);
    }
    return raw('<svg class="wx-wi" viewBox="0 0 48 48" width="' + (px || 36) + '" height="' + (px || 36) + '" aria-hidden="true">' + body + '</svg>');
  }

  /* ─── موتورِ جلوه‌ها (کانواس) ──────────────────────────────── */
  var fx = { cv: null, c: null, w: 0, h: 0, dpr: 1, parts: [], stars: [], dust: [], kind: '', day: 1, wind: 0, raf: 0, last: 0, flash: 0, bolt: null, nextBolt: 0, shoot: null, nextShoot: 0 };

  function fxSize() {
    if (!fx.cv || !fx.cv.parentNode) return;
    var r = fx.cv.parentNode.getBoundingClientRect();
    fx.dpr = Math.min(2, window.devicePixelRatio || 1);
    fx.w = Math.max(1, r.width); fx.h = Math.max(1, r.height);
    fx.cv.width = Math.round(fx.w * fx.dpr); fx.cv.height = Math.round(fx.h * fx.dpr);
    fx.c.setTransform(fx.dpr, 0, 0, fx.dpr, 0, 0);
  }
  function fxSetup(kind, day, wind, dust) {
    fx.kind = kind; fx.day = day; fx.wind = wind || 0;
    fxSize();
    var f = reduce() ? 0.3 : 1, W = fx.w, H = fx.h, i, n;
    fx.parts = []; fx.stars = []; fx.dust = [];
    if (kind === 'rain' || kind === 'storm' || kind === 'drizzle') {
      var sc = kind === 'drizzle' ? 0.6 : 1;
      n = Math.round((kind === 'storm' ? 150 : kind === 'rain' ? 110 : 55) * f);
      for (i = 0; i < n; i++) fx.parts.push({ x: rnd(-60, W + 60), y: rnd(0, H), l: rnd(10, 24) * sc, v: rnd(11, 19) * sc, a: rnd(0.25, 0.6) });
    } else if (kind === 'snow') {
      n = Math.round(80 * f);
      for (i = 0; i < n; i++) fx.parts.push({ x: rnd(0, W), y: rnd(0, H), r: rnd(1.2, 3.6), v: rnd(0.6, 1.8), ph: rnd(0, 6.28), a: rnd(0.5, 0.95) });
    }
    if (!day && (kind === 'clear' || kind === 'partly')) {
      n = Math.round(80 * f);
      for (i = 0; i < n; i++) fx.stars.push({ x: rnd(0, W), y: rnd(0, H * 0.7), r: rnd(0.4, 1.5), ph: rnd(0, 6.28), sp: rnd(0.0008, 0.003) });
    }
    if (dust) {
      n = Math.round(60 * f);
      for (i = 0; i < n; i++) fx.dust.push({ x: rnd(0, W), y: rnd(0, H), l: rnd(20, 60), v: rnd(2, 5), a: rnd(0.08, 0.22), t: rnd(1, 2.2) });
    }
    fx.nextBolt = performance.now() + rnd(2500, 6000);
    fx.nextShoot = performance.now() + rnd(3000, 8000);
  }
  function makeBolt() {
    var x = rnd(fx.w * 0.15, fx.w * 0.85), y = 0, pts = [[x, y]], endY = fx.h * rnd(0.45, 0.7);
    while (y < endY) { x += rnd(-22, 22); y += rnd(14, 30); pts.push([x, y]); }
    return pts;
  }
  function fxFrame(t) {
    fx.raf = 0;
    if (!shown || document.hidden) return;
    fx.raf = requestAnimationFrame(fxFrame);
    if (t - fx.last < 28) return;
    fx.last = t;
    var c = fx.c, W = fx.w, H = fx.h, i, p;
    c.clearRect(0, 0, W, H);
    var slant = clamp(fx.wind / 30, 0, 1.6) * 0.35 + 0.12;

    if (fx.stars.length) {
      c.fillStyle = '#fff';
      for (i = 0; i < fx.stars.length; i++) {
        p = fx.stars[i];
        c.globalAlpha = 0.3 + 0.7 * Math.abs(Math.sin(t * p.sp + p.ph));
        c.beginPath(); c.arc(p.x, p.y, p.r, 0, 6.283); c.fill();
      }
      c.globalAlpha = 1;
      if (!reduce() && fx.kind === 'clear') {
        if (!fx.shoot && t > fx.nextShoot) fx.shoot = { x: rnd(W * 0.3, W), y: rnd(0, H * 0.25), life: 0 };
        if (fx.shoot) {
          var s = fx.shoot; s.life += 1; s.x -= 11; s.y += 6;
          var a = 1 - s.life / 26;
          if (a <= 0) { fx.shoot = null; fx.nextShoot = t + rnd(6000, 13000); }
          else {
            var g = c.createLinearGradient(s.x, s.y, s.x + 70, s.y - 38);
            g.addColorStop(0, 'rgba(255,255,255,' + a + ')'); g.addColorStop(1, 'rgba(255,255,255,0)');
            c.strokeStyle = g; c.lineWidth = 1.6; c.beginPath(); c.moveTo(s.x, s.y); c.lineTo(s.x + 70, s.y - 38); c.stroke();
          }
        }
      }
    }
    if (fx.kind === 'rain' || fx.kind === 'storm' || fx.kind === 'drizzle') {
      c.strokeStyle = 'rgba(205,225,255,.5)'; c.lineWidth = fx.kind === 'drizzle' ? 0.9 : 1.2; c.beginPath();
      for (i = 0; i < fx.parts.length; i++) {
        p = fx.parts[i];
        c.moveTo(p.x, p.y); c.lineTo(p.x - p.l * slant, p.y + p.l);
        p.y += p.v; p.x -= p.v * slant;
        if (p.y > H) { p.y = -p.l; p.x = rnd(-20, W + 80); }
      }
      c.stroke();
      if (fx.kind === 'storm' && !reduce()) {
        if (t > fx.nextBolt) { fx.flash = 1; fx.bolt = makeBolt(); fx.nextBolt = t + rnd(4000, 9500); if (hx) hx.tap(); }
        if (fx.flash > 0.02) {
          c.fillStyle = 'rgba(235,240,255,' + (fx.flash * 0.45) + ')'; c.fillRect(0, 0, W, H);
          if (fx.bolt && fx.flash > 0.35) {
            c.strokeStyle = 'rgba(255,255,255,' + fx.flash + ')'; c.lineWidth = 2.2; c.shadowColor = '#cfe0ff'; c.shadowBlur = 14;
            c.beginPath(); fx.bolt.forEach(function (q, j) { if (j) c.lineTo(q[0], q[1]); else c.moveTo(q[0], q[1]); }); c.stroke(); c.shadowBlur = 0;
          }
          fx.flash *= 0.86;
        }
      }
    } else if (fx.kind === 'snow') {
      c.fillStyle = '#fff';
      for (i = 0; i < fx.parts.length; i++) {
        p = fx.parts[i];
        p.y += p.v; p.x += Math.sin(t * 0.001 + p.ph) * 0.45 - fx.wind * 0.012;
        if (p.y > H + 6) { p.y = -6; p.x = rnd(0, W); }
        c.globalAlpha = p.a; c.beginPath(); c.arc(p.x, p.y, p.r, 0, 6.283); c.fill();
      }
      c.globalAlpha = 1;
    }
    if (fx.dust.length) {
      c.lineCap = 'round';
      for (i = 0; i < fx.dust.length; i++) {
        p = fx.dust[i];
        c.strokeStyle = 'rgba(222,178,112,' + p.a + ')'; c.lineWidth = p.t;
        c.beginPath(); c.moveTo(p.x, p.y); c.lineTo(p.x + p.l, p.y + p.l * 0.06); c.stroke();
        p.x += p.v + fx.wind * 0.05;
        if (p.x > W + 70) { p.x = -p.l - 10; p.y = rnd(0, H); }
      }
    }
  }
  function fxStart() { if (!fx.raf && shown && !document.hidden) fx.raf = requestAnimationFrame(fxFrame); }
  function fxStop() { if (fx.raf) cancelAnimationFrame(fx.raf); fx.raf = 0; }

  /* ─── ساخت صحنه‌ی هیرو ─────────────────────────────────────── */
  function buildHero() {
    var cv = h('canvas', { class: 'wx-cv', 'aria-hidden': 'true' });
    fx.cv = cv; fx.c = cv.getContext('2d');
    R.sun = h('div', { class: 'wx-sun' }, h('i', { class: 'wx-rays' }), h('i', { class: 'wx-disc' }));
    R.moon = h('div', { class: 'wx-moon', text: '🌙' });
    R.temp = h('span', { class: 'n', dir: 'ltr', text: '–' });
    R.label = h('div', { class: 'wx-label' });
    R.sub = h('div', { class: 'wx-sub' });
    R.head = h('div', { class: 'wx-headline' });
    R.refresh = h('button', { type: 'button', class: 'wx-btn', 'aria-label': 'به‌روزرسانی', onclick: function () { hx.tap(); load(true); } }, ic('reset'));
    var back = h('button', { type: 'button', class: 'wx-btn', 'aria-label': 'بازگشت', onclick: function () { hx.tap(); goHome(); } }, ic('chev'));
    R.city = h('div', { class: 'wx-city' }, h('b', { text: 'سرپل‌ذهاب' }), R.upd = h('small', { text: '' }));
    R.hero = h('header', { class: 'wx-hero' },
      h('div', { class: 'wx-fxl', 'aria-hidden': 'true' },
        R.sun, R.moon,
        h('i', { class: 'wx-cloud c1' }), h('i', { class: 'wx-cloud c2' }), h('i', { class: 'wx-cloud c3' }), h('i', { class: 'wx-cloud c4' }),
        h('i', { class: 'wx-fog f1' }), h('i', { class: 'wx-fog f2' }), h('i', { class: 'wx-fog f3' })),
      cv,
      h('div', { class: 'wx-bar' }, back, R.city, R.refresh),
      h('div', { class: 'wx-main' },
        h('div', { class: 'wx-temp' }, R.temp, h('span', { class: 'deg', text: '°' })),
        R.label, R.sub),
      R.head);
    return R.hero;
  }

  function placeCelestial(d) {
    var today = d.days[0] || {}, sr = mins(today.sunrise), ss = mins(today.sunset), nm = nowMin();
    var day = d.now.is_day;
    if (day && sr != null && ss != null) {
      var p = clamp((nm - sr) / Math.max(1, ss - sr), 0, 1);
      R.sun.style.left = (26 + 48 * p) + '%';
      R.sun.style.top = (58 - 40 * Math.sin(Math.PI * p)) + '%';
    } else { R.sun.style.left = '50%'; R.sun.style.top = '30%'; }
    R.moon.textContent = d.moon || '🌙';
  }

  /* ─── اجزای بدنه ──────────────────────────────────────────── */
  function sec(title, extra, i) {
    return h('div', { class: 'sec-title wx-in', style: '--i:' + i }, h('span', { text: title }), extra || null);
  }
  function tile(cls, i, kids) {
    return h('div', { class: 'wx-tile wx-in ' + cls, style: '--i:' + i }, kids);
  }
  function tt(icon, text) { return h('div', { class: 'wx-tt' }, h('span', { text: icon }), h('span', { text: text })); }

  function hourlyCard(d) {
    var hs = d.hours.slice(0, 24), CW = 62, n = hs.length;
    if (!n) return null;
    var tmin = Math.min.apply(null, hs.map(function (x) { return x.temp; })), tmax = Math.max.apply(null, hs.map(function (x) { return x.temp; }));
    var span = Math.max(1, tmax - tmin), Hh = 74, pts = hs.map(function (x, i) { return [i * CW + CW / 2, 22 + (1 - (x.temp - tmin) / span) * 34]; });
    var path = pts.map(function (q, i) {
      if (!i) return 'M' + q[0] + ' ' + q[1];
      var pr = pts[i - 1], mx = (pr[0] + q[0]) / 2;
      return 'C' + mx + ' ' + pr[1] + ' ' + mx + ' ' + q[1] + ' ' + q[0] + ' ' + q[1];
    }).join(' ');
    var curve = svg('svg', { class: 'wx-curve', width: n * CW, height: Hh, viewBox: '0 0 ' + (n * CW) + ' ' + Hh }, [
      svg('defs', {}, [svg('linearGradient', { id: 'wxg', x1: 0, x2: 0, y1: 0, y2: 1 }, [svg('stop', { offset: 0, 'stop-color': 'currentColor', 'stop-opacity': 0.28 }), svg('stop', { offset: 1, 'stop-color': 'currentColor', 'stop-opacity': 0 })])]),
      svg('path', { d: path + ' L' + pts[n - 1][0] + ' ' + Hh + ' L' + pts[0][0] + ' ' + Hh + ' Z', fill: 'url(#wxg)' }),
      svg('path', { d: path, class: 'wx-line', fill: 'none', 'stroke-width': 2.4, 'stroke-linecap': 'round' })
    ]);
    pts.forEach(function (q, i) {
      curve.appendChild(svg('circle', { cx: q[0], cy: q[1], r: 3.4, class: 'wx-dot' }));
      var tx = svg('text', { x: q[0], y: q[1] - 9, 'text-anchor': 'middle', class: 'wx-pt' }); tx.textContent = hs[i].temp + '°'; curve.appendChild(tx);
    });
    var cols = hs.map(function (x, i) {
      return h('div', { class: 'wx-hc' + (i === 0 ? ' now' : ''), style: 'width:' + CW + 'px' },
        h('span', { class: 'wx-hh', text: i === 0 ? 'الان' : x.h }),
        wicon(x.kind, x.day, 30),
        h('span', { class: 'wx-hp' + (x.pop >= 30 ? ' wet' : ''), text: x.pop >= 10 ? x.pop + '٪' : '' }));
    });
    var strip = h('div', { class: 'wx-strip' }, h('div', { class: 'wx-strip-in', style: 'width:' + (n * CW) + 'px' },
      h('div', { class: 'wx-hrow' }, cols), curve));
    return h('div', { class: 'wx-card wx-in', style: '--i:1' }, strip);
  }

  function weekCard(d) {
    var days = d.days.slice(0, 6);
    var lo = Math.min.apply(null, days.map(function (x) { return x.tmin; })), hi = Math.max.apply(null, days.map(function (x) { return x.tmax; })), sp = Math.max(1, hi - lo);
    var rows = days.map(function (x, i) {
      var l = (x.tmin - lo) / sp * 100, w = Math.max(8, (x.tmax - x.tmin) / sp * 100);
      return h('div', { class: 'wx-day' + (i === 0 ? ' today' : '') },
        h('div', { class: 'wx-dn' }, h('b', { text: dayName(x.date, i) }), h('small', { text: dayFa(x.date) })),
        h('div', { class: 'wx-di' }, wicon(x.kind, 1, 30), h('span', { class: 'wx-dp' + (x.pop >= 30 ? ' wet' : ''), text: x.pop >= 10 ? '💧' + x.pop + '٪' : '' })),
        h('div', { class: 'wx-range', dir: 'ltr' },
          h('span', { class: 'lo', text: x.tmin + '°' }),
          h('i', { class: 'bar' }, h('u', { style: 'left:' + l + '%;width:' + w + '%;background:linear-gradient(90deg,' + tcol(x.tmin) + ',' + tcol(x.tmax) + ')' })),
          h('span', { class: 'hi', text: x.tmax + '°' })));
    });
    return h('div', { class: 'wx-card wx-in wx-week', style: '--i:3' }, rows);
  }

  function ringTile(d, i) {
    var today = d.days[0] || {}, mp = Math.max.apply(null, d.hours.slice(0, 12).map(function (x) { return x.pop; }).concat([0])), C = 2 * Math.PI * 34;
    var arc = svg('circle', { cx: 44, cy: 44, r: 34, class: 'arc', fill: 'none', 'stroke-width': 8, 'stroke-linecap': 'round', 'stroke-dasharray': C, 'stroke-dashoffset': C, transform: 'rotate(-90 44 44)' });
    var ring = svg('svg', { viewBox: '0 0 88 88', class: 'wx-ring' }, [svg('circle', { cx: 44, cy: 44, r: 34, fill: 'none', 'stroke-width': 8, class: 'trk' }), arc]);
    var num = h('b', { class: 'wx-rn', text: '0٪' });
    setTimeout(function () { arc.style.strokeDashoffset = C * (1 - mp / 100); countTo({ set textContent(v) { num.textContent = v + '٪'; } }, mp, 900); }, 250 + i * 70);
    return tile('', i, [tt('🌧️', 'احتمال بارش'), h('div', { class: 'wx-ringbox' }, ring, num),
      h('div', { class: 'wx-note', text: today.rain > 0 ? 'امروز ' + today.rain + ' میلی‌متر' : 'تا ۱۲ ساعتِ آینده' })]);
  }

  function airTile(d, i) {
    var a = d.air;
    if (!a) return tile('', i, [tt('🍃', 'کیفیت هوا'), h('div', { class: 'wx-note', text: 'اطلاعاتی در دسترس نیست' })]);
    var ang = clamp(a.aqi / 300, 0, 1) * 180 - 90, col = AQ_COL[a.level];
    var needle = svg('g', { class: 'needle', style: 'transform:rotate(-90deg)' }, [svg('path', { d: 'M50 56 L50 22', stroke: col, 'stroke-width': 3, 'stroke-linecap': 'round' }), svg('circle', { cx: 50, cy: 56, r: 5, fill: col })]);
    var g = svg('svg', { viewBox: '0 0 100 62', class: 'wx-gauge' }, [
      svg('defs', {}, [svg('linearGradient', { id: 'wxa', x1: 0, x2: 1, y1: 0, y2: 0 }, [['0', '#2fb344'], ['.35', '#d9b800'], ['.6', '#f08c00'], ['.8', '#e03131'], ['1', '#9c36b5']].map(function (s) { return svg('stop', { offset: s[0], 'stop-color': s[1] }); }))]),
      svg('path', { d: 'M10 56 A40 40 0 0 1 90 56', fill: 'none', stroke: 'url(#wxa)', 'stroke-width': 9, 'stroke-linecap': 'round', opacity: 0.9 }), needle]);
    setTimeout(function () { needle.style.transform = 'rotate(' + ang + 'deg)'; }, 350 + i * 70);
    var extra = a.dust != null && a.dust >= 50 ? 'گردوغبار ' + a.dust + ' µg' : (a.pm25 != null ? 'PM2.5: ' + a.pm25 : '');
    return tile('', i, [tt('🍃', 'کیفیت هوا'), g, h('div', { class: 'wx-big', style: 'color:' + col }, h('b', { text: String(a.aqi) }), h('span', { text: a.label })), h('div', { class: 'wx-note', text: extra })]);
  }

  function feelsTile(d, i) {
    var n = d.now, df = (n.feels != null && n.temp != null) ? n.feels - n.temp : 0;
    var txt = Math.abs(df) < 2 ? 'نزدیک به دمای واقعی' : df < 0 ? Math.abs(df) + '° خنک‌تر از دمای واقعی' : df + '° گرم‌تر از دمای واقعی';
    return tile('', i, [tt('🌡️', 'حس واقعی'), h('div', { class: 'wx-big', style: 'color:' + tcol(n.feels == null ? 20 : n.feels) }, h('b', { dir: 'ltr', text: (n.feels == null ? '—' : n.feels) + '°' })), h('div', { class: 'wx-note', text: txt })]);
  }

  function humTile(d, i) {
    var v = d.now.hum == null ? 0 : d.now.hum;
    var water = h('div', { class: 'wx-water', style: 'height:0%' });
    setTimeout(function () { water.style.height = v + '%'; }, 300 + i * 70);
    var lbl = v < 30 ? 'هوا خشکه' : v < 60 ? 'مطبوع' : v < 80 ? 'نسبتاً مرطوب' : 'خیلی مرطوب';
    return tile('wx-hum', i, [water, tt('💧', 'رطوبت'), h('div', { class: 'wx-big' }, h('b', { text: v + '٪' })), h('div', { class: 'wx-note', text: lbl })]);
  }

  function windTile(d, i) {
    var n = d.now, w = n.wind == null ? 0 : n.wind, dir = n.wind_dir == null ? 0 : n.wind_dir;
    var arrow = svg('g', { class: 'warrow', style: 'transform-origin:50px 50px;transform:rotate(0deg)' }, [svg('path', { d: 'M50 22 L58 46 L50 41 L42 46 Z', fill: 'currentColor' }), svg('path', { d: 'M50 41 L50 72', stroke: 'currentColor', 'stroke-width': 3, 'stroke-linecap': 'round' })]);
    var comp = svg('svg', { viewBox: '0 0 100 100', class: 'wx-comp' }, [svg('circle', { cx: 50, cy: 50, r: 44, fill: 'none', class: 'ring', 'stroke-width': 2 }),
      (function () { var t = svg('text', { x: 50, y: 14, 'text-anchor': 'middle', class: 'cl' }); t.textContent = 'ش'; return t; })(),
      (function () { var t = svg('text', { x: 50, y: 94, 'text-anchor': 'middle', class: 'cl' }); t.textContent = 'ج'; return t; })(),
      (function () { var t = svg('text', { x: 90, y: 54, 'text-anchor': 'middle', class: 'cl' }); t.textContent = 'ق'; return t; })(),
      (function () { var t = svg('text', { x: 10, y: 54, 'text-anchor': 'middle', class: 'cl' }); t.textContent = 'غ'; return t; })(), arrow]);
    setTimeout(function () { arrow.style.transform = 'rotate(' + ((dir + 180) % 360) + 'deg)'; }, 350 + i * 70);
    var lab = w < 6 ? 'آرام' : w < 20 ? 'نسیم' : w < 40 ? 'باد' : w < 60 ? 'باد شدید' : 'طوفانی';
    return tile('', i, [tt('🌬️', 'باد'), comp, h('div', { class: 'wx-big sm' }, h('b', { text: w }), h('span', { dir: 'ltr', text: 'km/h' }), h('span', { text: lab })),
      h('div', { class: 'wx-note', text: 'از ' + DIRS[Math.round(dir / 45) % 8] + (n.gust ? ' · وزش تا ' + n.gust : '') })]);
  }

  function uvTile(d, i) {
    var uv = (d.days[0] || {}).uv || 0, lab = uv < 3 ? 'کم' : uv < 6 ? 'متوسط' : uv < 8 ? 'زیاد' : uv < 11 ? 'خیلی زیاد' : 'شدید';
    var mk = h('i', { class: 'mk', style: 'left:0%' });
    setTimeout(function () { mk.style.left = clamp(uv / 11 * 100, 2, 98) + '%'; }, 400 + i * 70);
    return tile('', i, [tt('☀️', 'پرتو فرابنفش'), h('div', { class: 'wx-big' }, h('b', { text: uv.toFixed ? (+uv).toFixed(0) : uv }), h('span', { text: lab })), h('div', { class: 'wx-uv', dir: 'ltr' }, mk),
      h('div', { class: 'wx-note', text: uv >= 6 ? 'ضدآفتاب و کلاه' : 'بدونِ نگرانی' })]);
  }

  function presTile(d, i) {
    var p = d.now.pressure, lab = p == null ? '' : p < 1000 ? 'کم‌فشار' : p > 1020 ? 'پرفشار' : 'عادی';
    var cld = d.now.cloud == null ? '' : 'پوششِ ابر ' + d.now.cloud + '٪';
    return tile('', i, [tt('🧭', 'فشارِ هوا'), h('div', { class: 'wx-big' }, h('b', { text: p == null ? '—' : p }), h('span', { text: ' hPa' })), h('div', { class: 'wx-note', text: lab + (cld ? ' · ' + cld : '') })]);
  }

  var MOON_N = { '🌑': 'ماهِ نو', '🌒': 'هلالِ نوپا', '🌓': 'تربیعِ اول', '🌔': 'رو به بدر', '🌕': 'ماهِ کامل', '🌖': 'پس از بدر', '🌗': 'تربیعِ آخر', '🌘': 'هلالِ پایانی' };
  function moonTile(d, i) {
    var m = d.moon || '🌙', cl = d.now.cloud == null ? 50 : d.now.cloud;
    var note = d.now.is_day ? 'امشب در آسمان است' : (cl < 30 ? 'آسمان برای رصد مناسبه' : 'ابرها دیدِ ماه رو می‌گیرن');
    return tile('wx-moont', i, [tt('🌙', 'فازِ ماه'), h('div', { class: 'wx-mbig', text: m }), h('div', { class: 'wx-big sm' }, h('span', { text: MOON_N[m] || 'ماه' })), h('div', { class: 'wx-note', text: note })]);
  }

  function sunTile(d, i) {
    var t = d.days[0] || {}, sr = mins(t.sunrise), ss = mins(t.sunset);
    if (sr == null || ss == null) return null;
    var nm = nowMin(), p = clamp((nm - sr) / (ss - sr), 0, 1), q = 1 - p;
    var x = q * q * 20 + 2 * q * p * 150 + p * p * 280, y = q * q * 100 + 2 * q * p * -20 + p * p * 100;
    var dot = svg('g', { class: 'sdot', style: 'opacity:0' }, [svg('circle', { cx: x, cy: y, r: 13, fill: 'rgba(255,212,59,.28)' }), svg('circle', { cx: x, cy: y, r: 7, fill: '#ffd43b' })]);
    var art = svg('svg', { viewBox: '0 0 300 112', class: 'wx-arc' }, [svg('path', { d: 'M20 100 Q150 -20 280 100', fill: 'none', class: 'tr', 'stroke-width': 2, 'stroke-dasharray': '4 6' }), svg('path', { d: 'M10 100 H290', class: 'hz', 'stroke-width': 1.5 }), dot]);
    setTimeout(function () { dot.style.opacity = (nm >= sr && nm <= ss) ? 1 : 0.35; }, 500);
    return tile('wide', i, [tt('🌅', 'طلوع و غروب'), art,
      h('div', { class: 'wx-sun-t', dir: 'ltr' }, h('span', {}, h('small', { text: 'طلوع' }), h('b', { text: t.sunrise })), h('span', { class: 'mid', dir: 'rtl', text: fmtLen(ss - sr) }), h('span', {}, h('small', { text: 'غروب' }), h('b', { text: t.sunset })))]);
  }

  function adviceList(d) {
    return d.advice.map(function (a, i) {
      return h('div', { class: 'wx-adv wx-in', style: '--i:' + (i + 14) }, h('span', { class: 'ai', text: a.icon }), h('div', {}, h('b', { text: a.title }), h('p', { text: a.text })));
    });
  }

  /* ─── تحلیلِ هوش مصنوعی ───────────────────────────────────── */
  function aiCard() {
    R.aiText = h('div', { class: 'wx-ai-text', 'aria-live': 'polite' });
    R.aiBtn = h('button', { type: 'button', class: 'wx-ai-btn', onclick: runAI }, h('span', { class: 'spk', text: '✨' }), h('span', { class: 'lbl', text: 'تحلیل هوش مصنوعی' }));
    return h('div', { class: 'wx-ai wx-in', style: '--i:20' },
      h('div', { class: 'wx-ai-in' }, h('div', { class: 'wx-ai-h' }, h('b', { text: 'تحلیلِ هوشمندِ امروز' }), h('small', { text: 'بر اساسِ داده‌ی زنده‌ی همین صفحه' })), R.aiText, R.aiBtn));
  }
  function typeOut(text) {
    clearInterval(typeT);
    R.aiText.textContent = ''; R.aiText.classList.add('on');
    if (reduce()) { R.aiText.textContent = text; return; }
    var i = 0;
    typeT = setInterval(function () {
      i += 2; R.aiText.textContent = text.slice(0, i);
      if (i >= text.length) clearInterval(typeT);
    }, 22);
  }
  function runAI() {
    if (aiBusy) return;
    aiBusy = true; hx.tap();
    R.aiBtn.classList.add('busy'); R.aiBtn.querySelector('.lbl').textContent = 'در حال تحلیل…';
    R.aiText.classList.add('on'); R.aiText.replaceChildren(h('span', { class: 'wx-dots' }, h('i'), h('i'), h('i')));
    api('/hub/api/weather/ai', {}).then(function (r) {
      hx.ok(); typeOut(r.text);
    }).catch(function (e) {
      hx.err();
      R.aiText.classList.remove('on'); R.aiText.textContent = '';
      toast((e && e.data && e.data.message) || 'تحلیل الان ممکن نشد');
    }).then(function () {
      aiBusy = false; R.aiBtn.classList.remove('busy'); R.aiBtn.querySelector('.lbl').textContent = 'تحلیلِ دوباره';
    });
  }

  /* ─── رندرِ کل ─────────────────────────────────────────────── */
  function skeleton() {
    return h('div', { class: 'wx-skel' }, h('i', { style: 'height:128px' }), h('i', { style: 'height:360px' }), h('div', { class: 'g' }, h('i'), h('i'), h('i'), h('i')));
  }

  function paint(d) {
    var n = d.now, day = n.is_day, kind = n.kind, dusty = !!(d.air && d.air.dust >= 100), pal = SKY[kind][day ? 0 : 1];
    if (dusty && (kind === 'clear' || kind === 'partly' || kind === 'cloud')) pal = day ? ['#a8733c', '#d29c5a', '#efcf98'] : ['#2b1e12', '#4b3421', '#6c4f35'];
    R.root.dataset.kind = kind; R.root.dataset.day = day;
    R.root.style.setProperty('--s1', pal[0]); R.root.style.setProperty('--s2', pal[1]); R.root.style.setProperty('--s3', pal[2]);
    R.root.classList.toggle('dusty', dusty);
    countTo(R.temp, n.temp, 1000);
    R.label.textContent = n.label;
    var t0 = d.days[0] || {};
    R.sub.textContent = 'حسِ واقعی ' + (n.feels == null ? '—' : n.feels) + '° · بیشینه ' + (t0.tmax == null ? '—' : t0.tmax) + '° · کمینه ' + (t0.tmin == null ? '—' : t0.tmin) + '°';
    R.head.textContent = d.headline;
    R.upd.textContent = 'به‌روزرسانی ' + d.updated.slice(11) + (d.stale ? ' · قدیمی' : '');
    placeCelestial(d);
    fxSetup(kind, day, n.wind, !!(d.air && d.air.dust >= 100));
    try { if (shown && tg) tg.setHeaderColor(pal[0]); } catch (e) {}

    var body = h('div', { class: 'wx-body' },
      sec('ساعت‌به‌ساعت', h('small', { text: '۲۴ ساعتِ آینده' }), 0), hourlyCard(d),
      sec('پیش‌بینیِ ۵ روزِ آینده', null, 2), weekCard(d),
      sec('جزئیاتِ امروز', null, 4),
      h('div', { class: 'wx-grid' }, ringTile(d, 5), airTile(d, 6), feelsTile(d, 7), humTile(d, 8), windTile(d, 9), uvTile(d, 10), presTile(d, 11), moonTile(d, 12), sunTile(d, 13)),
      sec('توصیه‌ی امروز', null, 14), h('div', { class: 'wx-advs' }, adviceList(d)),
      aiCard(),
      h('div', { class: 'wx-foot', text: 'داده: Open-Meteo · سرپل‌ذهاب' }));
    R.bodyWrap.replaceChildren(body);
    fxStart();
  }

  function showError() {
    R.bodyWrap.replaceChildren(h('div', { class: 'wx-err' }, h('div', { class: 'em', text: '🛰️' }), h('b', { text: 'اتصال به سرویس آب‌وهوا برقرار نشد' }), h('p', { text: 'اینترنت را بررسی کن و دوباره امتحان کن.' }),
      h('button', { type: 'button', class: 'btn', text: 'تلاش دوباره', onclick: function () { load(true); } })));
  }

  function load(force) {
    if (loading) return;
    loading = true; R.refresh.classList.add('spin');
    api('/hub/api/weather').then(function (d) {
      D = d; lastAt = Date.now(); LS.set(K, { d: d, at: lastAt });
      paint(d); if (force) hx.ok();
    }).catch(function () {
      if (!D) showError(); else if (force) toast('به‌روزرسانی نشد؛ داده‌ی قبلی نمایش داده می‌شود');
      hx.err();
    }).then(function () { loading = false; R.refresh.classList.remove('spin'); });
  }

  function mount(root, c) {
    h = c.h; ic = c.ic; hx = c.hx; tg = c.tg; toast = c.toast; LS = c.LS; api = c.api; goHome = c.goHome;
    R.root = h('div', { class: 'wx', 'data-kind': 'clear', 'data-day': '1' });
    R.bodyWrap = h('div', { class: 'wx-bw' }, skeleton());
    R.root.append(buildHero(), R.bodyWrap);
    root.replaceChildren(R.root);
    var cache = LS.get(K);
    if (cache && cache.d && cache.d.now) { D = cache.d; lastAt = cache.at || 0; paint(D); }
    load(false);
    window.addEventListener('resize', function () { if (D && shown) { fxSetup(D.now.kind, D.now.is_day, D.now.wind, !!(D.air && D.air.dust >= 100)); } });
    document.addEventListener('visibilitychange', function () { if (document.hidden) fxStop(); else fxStart(); });
  }

  window.HubWeather = {
    mount: mount,
    onShow: function () {
      shown = true;
      if (D) { try { if (tg) tg.setHeaderColor(SKY[D.now.kind][D.now.is_day ? 0 : 1][0]); } catch (e) {} fxSetup(D.now.kind, D.now.is_day, D.now.wind, !!(D.air && D.air.dust >= 100)); }
      fxStart();
      if (Date.now() - lastAt > 600000) load(false);
    },
    onHide: function () { shown = false; fxStop(); }
  };
})();
