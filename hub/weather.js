/* بخشِ آب‌وهوا (سرپل‌ذهاب) — تنبل بار می‌شود، مثلِ clock.js.
   روانی (FPS):
   • کانواس فقط وقتی تب باز، هیرو دیدنی و صفحه فعال است می‌چرخد؛ حرکت‌ها بر پایه‌ی زمان (dt) هستند، نه تعدادِ فریم
   • ذره‌ها در یک path دسته‌ای کشیده می‌شوند؛ صاعقه بدونِ shadowBlur؛ dpr حداکثر ۱.۵
   • اگر دستگاه کند بود، خودکار کیفیت (dpr و تعدادِ ذره) پایین می‌آید
   • کاشی‌ها و انیمیشن‌هایشان با IntersectionObserver فقط وقتی به صفحه می‌رسند شروع می‌شوند
   • داده از /hub/api/weather؛ نسخه‌ی قبلی از localStorage فوری نشان داده می‌شود */
(function () {
  'use strict';

  var h, ic, hx, tg, toast, LS, api, goHome;
  var K = 'hub:wx2';
  var D = null, loading = false, lastAt = 0, shown = false, heroVis = true, aiBusy = false, typeT = 0;
  var lastSig = '';
  var tickT = 0, tickI = 0, tickItems = [], paintTok = 0, painted = false, io = null, heroIO = null, R = {};
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
  var MOON_IDX = { '🌑': 0, '🌒': 1, '🌓': 2, '🌔': 3, '🌕': 4, '🌖': 5, '🌗': 6, '🌘': 7 };
  var MOON_K = [0, 0.25, 0.5, 0.75, 1, 0.75, 0.5, 0.25];
  var MOON_N = ['ماهِ نو', 'هلالِ نوپا', 'تربیعِ اول', 'رو به بدر', 'ماهِ کامل', 'پس از بدر', 'تربیعِ آخر', 'هلالِ پایانی'];

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
  function tempHue(t) { return Math.round(clamp((42 - t) / 47, 0, 1) * 240); }
  function tcol(t) { return 'hsl(' + tempHue(t) + ',85%,58%)'; }
  function dayName(iso, i) {
    if (i === 0) return 'امروز';
    if (i === 1) return 'فردا';
    return WD[new Date(iso + 'T12:00:00').getDay()];
  }
  function dayFa(iso) {
    try { return new Date(iso + 'T12:00:00').toLocaleDateString('fa-IR-u-ca-persian', { day: 'numeric', month: 'long' }); } catch (e) { return ''; }
  }
  function countTo(el, to, ms, suffix, from) {
    suffix = suffix || '';
    if (reduce() || to == null) { el.textContent = to == null ? '—' : to + suffix; return; }
    var f0 = from == null ? 0 : from, t0 = performance.now();
    (function step(t) {
      var p = clamp((t - t0) / ms, 0, 1), e = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(f0 + (to - f0) * e) + suffix;
      if (p < 1) requestAnimationFrame(step);
    })(t0);
  }

  /* ─── ماهِ ترسیم‌شده با فازِ واقعی (به‌جای اموجی) ──────────── */
  function moonSVG(emoji) {
    var idx = MOON_IDX[emoji]; if (idx == null) idx = 4;
    var k = MOON_K[idx], waning = idx > 4;
    var lit = '';
    if (k >= 1) lit = '<circle cx="50" cy="50" r="44" fill="#f3f0df"/>';
    else if (k > 0) {
      var rx = (44 * Math.abs(1 - 2 * k)).toFixed(1), sw = k > 0.5 ? 1 : 0;
      lit = '<path d="M50 6A44 44 0 0 1 50 94A' + rx + ' 44 0 0 ' + sw + ' 50 6Z" fill="#f3f0df"/>';
    }
    if (waning) lit = '<g transform="translate(100 0) scale(-1 1)">' + lit + '</g>';
    var cr = '<g fill="#8f93a8" opacity=".28"><circle cx="38" cy="38" r="7"/><circle cx="60" cy="58" r="9"/><circle cx="44" cy="68" r="4.5"/><circle cx="66" cy="34" r="4"/></g>';
    return '<svg viewBox="0 0 100 100" aria-hidden="true"><circle cx="50" cy="50" r="44" fill="#252d52" opacity=".85"/>' + lit + cr + '</svg>';
  }

  /* ─── آیکون‌ها ──────────────────────────────────────────────
     • آیکون‌های وضعیتِ هوا: sprite گرادیانی در index.html (<use>) — سبک و یکدست
     • آیکون‌های خطی (۲۴px): برای عنوانِ کاشی‌ها، توصیه‌ها و دکمه‌ها؛ هیچ اموجی‌ای در رابط نیست */
  var WI = { clear: ['clear-day', 'clear-night'], partly: ['partly-day', 'partly-night'], cloud: ['cloud', 'cloud'], drizzle: ['drizzle', 'drizzle'],
    rain: ['rain', 'rain'], storm: ['storm', 'storm'], snow: ['snow', 'snow'], fog: ['fog', 'fog'] };
  function wicon(kind, day, px) {
    var id = (WI[kind] || WI.cloud)[day ? 0 : 1];
    return raw('<svg class="wx-wi" viewBox="0 0 48 48" width="' + (px || 36) + '" height="' + (px || 36) + '" aria-hidden="true"><use href="#wi-' + id + '"/></svg>');
  }
  var LI = {
    drop: '<path d="M12 3.5S6 9.8 6 14a6 6 0 0 0 12 0c0-4.2-6-10.5-6-10.5z"/>',
    humidity: '<path d="M12 3.5S6 9.8 6 14a6 6 0 0 0 12 0c0-4.2-6-10.5-6-10.5z"/><path d="M9.2 14.6a2.9 2.9 0 0 0 2.5 2.4"/>',
    thermo: '<path d="M10 14.2V5.5a2 2 0 0 1 4 0v8.7a4 4 0 1 1-4 0z"/><path d="M12 9.5v6.5"/>',
    wind: '<path d="M3 9h10.5a2.5 2.5 0 1 0-2.5-2.5"/><path d="M3 13h15a2.5 2.5 0 1 1-2.5 2.5"/><path d="M3 17h6"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/>',
    gauge: '<path d="M4.5 17a8.5 8.5 0 1 1 15 0"/><path d="M12 14l3.5-4.2"/><circle cx="12" cy="14" r="1"/>',
    moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/>',
    sunrise: '<path d="M3 19h18"/><path d="M7 19a5 5 0 0 1 10 0"/><path d="M12 6v4M5.2 11.2l1.5 1.5M18.8 11.2l-1.5 1.5"/>',
    leaf: '<path d="M5 19C5 10.5 10.5 5 19 5c0 8.5-5.5 14-14 14z"/><path d="M5 19l7-7"/>',
    umbrella: '<path d="M12 3.5a9 9 0 0 1 9 9H3a9 9 0 0 1 9-9z"/><path d="M12 12.5V18a2 2 0 0 0 4 0"/>',
    mask: '<path d="M4 8.5c5-2 11-2 16 0V13c0 3.5-3.5 6-8 6s-8-2.5-8-6z"/><path d="M4 11H2M20 11h2M8.5 14h7"/>',
    tree: '<path d="M12 3.5a5 5 0 0 0-4.5 7.2A3.8 3.8 0 0 0 9 18h6a3.8 3.8 0 0 0 1.5-7.3A5 5 0 0 0 12 3.5z"/><path d="M12 18v3"/>',
    home: '<path d="M4 11l8-7 8 7"/><path d="M6 10v10h12V10"/><path d="M10 20v-5h4v5"/>',
    pin: '<path d="M12 21s7-6 7-11.5a7 7 0 0 0-14 0C5 15 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    spark: '<path d="M11 3l1.9 5.3L18 10l-5.1 1.7L11 17l-1.9-5.3L4 10l5.1-1.7z"/><path d="M18.5 15l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>',
    tee: '<path d="M8.5 4L3 7l2 4 3-1.5V20h8V9.5l3 1.5 2-4-5.5-3c-.8 1.7-2 2.5-3.5 2.5S9.3 5.7 8.5 4z"/>',
    jacket: '<path d="M8.5 5c0-2 1.6-3.2 3.5-3.2S15.5 3 15.5 5"/><path d="M8.5 5 3.5 8.5 3 18l3.2.8.5-7V20h10.6v-8.2l.5 7 3.2-.8-.5-9.5-5-3.5c-.8 2-2 3-3.5 3S9.3 7 8.5 5z"/><path d="M12 8v12"/>',
    coat: '<path d="M8.5 5c0-2 1.6-3.2 3.5-3.2S15.5 3 15.5 5"/><path d="M8.5 5 3.5 8.5 3 20l3.3.5.7-8.5V21.5h10V12l.7 8.5 3.3-.5-.5-11.5-5-3.5c-.8 2-2 3-3.5 3S9.3 7 8.5 5z"/><path d="M12 8v13.5"/>',
    heavy: '<path d="M8.5 5c0-2 1.6-3.2 3.5-3.2S15.5 3 15.5 5"/><path d="M8.5 5 3.5 8.5 3 20l3.3.5.7-8.5V21.5h10V12l.7 8.5 3.3-.5-.5-11.5-5-3.5c-.8 2-2 3-3.5 3S9.3 7 8.5 5z"/><path d="M7.7 8.5c2.6 1.8 5.9 1.8 8.6 0"/><path d="M10.2 10.5l-.2 4.5"/>'
  };
  function li(name, px, cls) {
    return raw('<svg class="wx-li' + (cls ? ' ' + cls : '') + '" viewBox="0 0 24 24" width="' + (px || 18) + '" height="' + (px || 18) + '" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (LI[name] || '') + '</svg>');
  }
  var TT_MAP = { '🌧️': 'drop', '🍃': 'leaf', '🌡️': 'thermo', '💧': 'humidity', '🌬️': 'wind', '☀️': 'sun', '🧭': 'gauge', '🌙': 'moon', '🌅': 'sunrise' };

  var LV_COL = { ok: '#2fb344', warn: '#f0a500', bad: '#e03131' };

  /* ─── موتورِ جلوه‌ها (کانواس) ──────────────────────────────── */
  /* فقط برای بارش/برف/ستاره/گردوغبار/صاعقه؛ در هوای صاف و ابری روزانه هیچ فریمی کشیده نمی‌شود.
     رزولوشنِ کانواس ۱× است (خطوطِ نازکِ باران نیازی به بیشتر ندارند) و ذره‌ها در چند pathِ دسته‌ای کشیده می‌شوند. */
  var fx = { cv: null, c: null, w: 0, h: 0, kind: '', day: 1, wind: 0, dustOn: false, rain: [], snow: [], stars: [], tw: [], dust: [],
    raf: 0, last: 0, flash: 0, bolt: null, nextBolt: 0, shoot: null, nextShoot: 0, acc: 0, cnt: 0, half: false, lowq: false, skip: 0 };

  function fxOn() { return shown && heroVis && !document.hidden; }
  function fxSize() {
    if (!fx.cv || !fx.cv.parentNode) return;
    var r = fx.cv.parentNode.getBoundingClientRect();
    fx.w = Math.max(1, r.width); fx.h = Math.max(1, r.height);
    fx.cv.width = Math.round(fx.w); fx.cv.height = Math.round(fx.h);
  }
  function fxSetup(kind, day, wind, dust) {
    fx.kind = kind; fx.day = day; fx.wind = wind || 0; fx.dustOn = !!dust;
    fxSize();
    var f = (reduce() ? 0.35 : 1) * (fx.lowq ? 0.6 : 1), W = fx.w, H = fx.h, i, n;
    fx.rain = []; fx.snow = []; fx.stars = []; fx.tw = []; fx.dust = [];
    if (kind === 'rain' || kind === 'storm' || kind === 'drizzle') {
      n = Math.round((kind === 'storm' ? 80 : kind === 'rain' ? 62 : 34) * f);
      for (i = 0; i < n; i++) {
        var near = i % 5 < 2;                      // ۴۰٪ نزدیک (بلندتر/سریع‌تر/روشن‌تر)، ۶۰٪ دور
        fx.rain.push({ x: rnd(-60, W + 60), y: rnd(0, H), near: near, l: near ? rnd(16, 26) : rnd(8, 14), v: near ? rnd(13, 18) : rnd(7, 10) });
      }
    } else if (kind === 'snow') {
      n = Math.round(52 * f);
      for (i = 0; i < n; i++) {
        var z = i % 3;                             // ۳ عمق: کوچک/آهسته … بزرگ/سریع
        fx.snow.push({ x: rnd(0, W), y: rnd(0, H), z: z, r: [1.2, 2, 3.2][z], v: [0.45, 0.85, 1.4][z], ph: rnd(0, 6.28) });
      }
    }
    if (!day && (kind === 'clear' || kind === 'partly')) {
      n = Math.round(56 * f);
      for (i = 0; i < n; i++) {
        var s = { x: rnd(0, W), y: rnd(0, H * 0.72), r: rnd(0.5, 1.4), ph: rnd(0, 6.28), sp: rnd(0.0009, 0.0028) };
        (i < 9 ? fx.tw : fx.stars).push(s);
      }
    }
    if (dust) {
      n = Math.round(26 * f);
      for (i = 0; i < n; i++) fx.dust.push({ x: rnd(0, W), y: rnd(0, H), l: rnd(26, 64), v: rnd(2, 4.2) });
    }
    fx.last = 0; fx.acc = 0; fx.cnt = 0; fx.skip = 0;
    fx.nextBolt = performance.now() + rnd(2500, 6000);
    fx.nextShoot = performance.now() + rnd(3000, 8000);
    if (fx.c) fx.c.clearRect(0, 0, W, H);
  }
  function fxAnimated() { return fx.rain.length || fx.snow.length || fx.stars.length || fx.tw.length || fx.dust.length; }
  function makeBolt() {
    var x = rnd(fx.w * 0.15, fx.w * 0.85), y = 0, pts = [[x, y]], endY = fx.h * rnd(0.45, 0.7);
    while (y < endY) { x += rnd(-20, 20); y += rnd(14, 28); pts.push([x, y]); }
    return pts;
  }
  function boltPath(c) { c.beginPath(); fx.bolt.forEach(function (q, j) { if (j) c.lineTo(q[0], q[1]); else c.moveTo(q[0], q[1]); }); }

  function fxFrame(t) {
    fx.raf = 0;
    if (!fxOn() || !fxAnimated()) return;       // صحنه‌ی ایستا: هیچ فریمی هدر نمی‌رود
    fx.raf = requestAnimationFrame(fxFrame);
    var dt = fx.last ? Math.min(60, t - fx.last) : 16.7; fx.last = t;

    // کیفیتِ تطبیقی: اگر کند بود اول ۳۰fps، بعد کمترکردنِ ذره‌ها
    if (!fx.lowq) {
      fx.acc += dt; fx.cnt++;
      if (fx.cnt >= 40) {
        var avg = fx.acc / fx.cnt; fx.acc = 0; fx.cnt = 0;
        if (avg > 27) { if (!fx.half) fx.half = true; else { fx.lowq = true; fxSetup(fx.kind, fx.day, fx.wind, fx.dustOn); fxStart(); return; } }
      }
    }
    if (fx.half && (fx.skip ^= 1)) return;      // یک فریم در میان
    var k = dt / 16.67, c = fx.c, W = fx.w, H = fx.h, i, p;
    c.clearRect(0, 0, W, H);
    var slant = clamp(fx.wind / 30, 0, 1.6) * 0.3 + 0.1;

    if (fx.stars.length || fx.tw.length) {
      c.fillStyle = 'rgba(255,255,255,.7)'; c.beginPath();
      for (i = 0; i < fx.stars.length; i++) { p = fx.stars[i]; c.moveTo(p.x + p.r, p.y); c.arc(p.x, p.y, p.r, 0, 6.283); }
      c.fill();
      c.fillStyle = '#fff';
      for (i = 0; i < fx.tw.length; i++) {
        p = fx.tw[i]; c.globalAlpha = 0.2 + 0.8 * Math.abs(Math.sin(t * p.sp + p.ph));
        c.beginPath(); c.arc(p.x, p.y, p.r + 0.5, 0, 6.283); c.fill();
      }
      c.globalAlpha = 1;
      if (!reduce() && fx.kind === 'clear') {
        if (!fx.shoot && t > fx.nextShoot) fx.shoot = { x: rnd(W * 0.35, W), y: rnd(0, H * 0.22), life: 0 };
        if (fx.shoot) {
          var s = fx.shoot; s.life += k; s.x -= 10 * k; s.y += 5.5 * k;
          var a = 1 - s.life / 30;
          if (a <= 0) { fx.shoot = null; fx.nextShoot = t + rnd(7000, 15000); }
          else {
            var g = c.createLinearGradient(s.x, s.y, s.x + 64, s.y - 35);
            g.addColorStop(0, 'rgba(255,255,255,' + a.toFixed(2) + ')'); g.addColorStop(1, 'rgba(255,255,255,0)');
            c.strokeStyle = g; c.lineWidth = 1.4; c.beginPath(); c.moveTo(s.x, s.y); c.lineTo(s.x + 64, s.y - 35); c.stroke();
          }
        }
      }
    }

    if (fx.rain.length) {
      var far = [], near = [];
      for (i = 0; i < fx.rain.length; i++) {
        p = fx.rain[i]; (p.near ? near : far).push(p);
      }
      function streak(arr, style, lw) {
        c.strokeStyle = style; c.lineWidth = lw; c.beginPath();
        for (var j = 0; j < arr.length; j++) {
          var q = arr[j];
          c.moveTo(q.x, q.y); c.lineTo(q.x - q.l * slant, q.y + q.l);
          q.y += q.v * k; q.x -= q.v * slant * k;
          if (q.y > H) { q.y = -q.l; q.x = rnd(-20, W + 80); }
        }
        c.stroke();
      }
      streak(far, 'rgba(200,220,255,.2)', 0.8);
      streak(near, 'rgba(225,238,255,.5)', 1.2);
      if (fx.kind === 'storm' && !reduce()) {
        if (t > fx.nextBolt) { fx.flash = 1; fx.bolt = makeBolt(); fx.nextBolt = t + rnd(4500, 10000); }
        if (fx.flash > 0.03) {
          c.fillStyle = 'rgba(225,235,255,' + (fx.flash * 0.34).toFixed(3) + ')'; c.fillRect(0, 0, W, H);
          if (fx.bolt && fx.flash > 0.3) {
            c.lineJoin = 'round';
            c.strokeStyle = 'rgba(180,205,255,' + (fx.flash * 0.3).toFixed(2) + ')'; c.lineWidth = 6; boltPath(c); c.stroke();
            c.strokeStyle = 'rgba(255,255,255,' + fx.flash.toFixed(2) + ')'; c.lineWidth = 1.8; boltPath(c); c.stroke();
          }
          fx.flash *= Math.pow(0.87, k);
        }
      }
    } else if (fx.snow.length) {
      for (var z = 0; z < 3; z++) {
        c.fillStyle = 'rgba(255,255,255,' + [0.5, 0.75, 0.95][z] + ')'; c.beginPath();
        for (i = 0; i < fx.snow.length; i++) {
          p = fx.snow[i]; if (p.z !== z) continue;
          p.y += p.v * k; p.x += (Math.sin(t * 0.0009 + p.ph) * 0.4 - fx.wind * 0.01) * k;
          if (p.y > H + 6) { p.y = -6; p.x = rnd(0, W); }
          c.moveTo(p.x + p.r, p.y); c.arc(p.x, p.y, p.r, 0, 6.283);
        }
        c.fill();
      }
    }
    if (fx.dust.length) {
      c.strokeStyle = 'rgba(224,182,118,.17)'; c.lineWidth = 1.5; c.lineCap = 'round'; c.beginPath();
      for (i = 0; i < fx.dust.length; i++) {
        p = fx.dust[i];
        c.moveTo(p.x, p.y); c.lineTo(p.x + p.l, p.y + p.l * 0.05);
        p.x += (p.v + fx.wind * 0.05) * k;
        if (p.x > W + 70) { p.x = -p.l - 10; p.y = rnd(0, H); }
      }
      c.stroke();
    }
  }
  function fxStart() { if (!fx.raf && fxOn() && fxAnimated()) fx.raf = requestAnimationFrame(fxFrame); }
  function fxStop() { if (fx.raf) cancelAnimationFrame(fx.raf); fx.raf = 0; fx.last = 0; }

  /* ─── کادرِ اصلی: توصیه‌ی چرخان ────────────────────────────── */
  function tickShow(i, instant) {
    var it = tickItems[i]; if (!it) return;
    function set() {
      R.tkIc.replaceChildren(li(it.ic, 17));
      R.tkTx.textContent = it.tx;
      var ds = R.tkDots.children;
      for (var j = 0; j < ds.length; j++) ds[j].classList.toggle('on', j === i);
      R.tick.classList.remove('out');
    }
    if (instant || reduce()) { set(); return; }
    R.tick.classList.add('out'); setTimeout(set, 200);
  }
  function tickGo(step, manual) {
    if (tickItems.length < 2) return;
    tickI = (tickI + step + tickItems.length) % tickItems.length;
    tickShow(tickI);
    if (manual) tickRestart();
  }
  function tickRestart() {
    clearInterval(tickT); tickT = 0;
    if (reduce() || !shown || tickItems.length < 2) return;
    tickT = setInterval(function () { if (!document.hidden && heroVis) tickGo(1); }, 6000);
  }
  function tickSet(d) {
    tickItems = [{ ic: 'pin', tx: d.headline }].concat((d.advice || []).map(function (a) { return { ic: a.ic || 'spark', tx: a.text }; }));
    tickI = Math.min(tickI, tickItems.length - 1);
    R.tkDots.replaceChildren.apply(R.tkDots, tickItems.map(function () { return h('i'); }));
    tickShow(tickI, true);
    tickRestart();
  }

  /* ─── ساخت صحنه‌ی هیرو ─────────────────────────────────────── */
  /* لایه‌های متحرک عمداً کم‌اند (خورشید: ۱ لایه‌ی opacity، ابرها: transform، کانواس فقط برای بارش/ستاره):
     همه‌چیز دیگر گرادیانِ ثابت است و فقط یک‌بار رسم می‌شود. */
  function buildHero() {
    var cv = h('canvas', { class: 'wx-cv', 'aria-hidden': 'true' });
    fx.cv = cv; fx.c = cv.getContext('2d');
    R.sun = h('div', { class: 'wx-sun' }, h('i', { class: 'wx-glow' }), h('i', { class: 'wx-core' }));
    R.moon = h('div', { class: 'wx-moon' });
    R.temp = h('span', { class: 'n', dir: 'ltr', text: '–' });
    R.label = h('div', { class: 'wx-label' });
    R.sub = h('div', { class: 'wx-sub' });
    R.refresh = h('button', { type: 'button', class: 'wx-btn', 'aria-label': 'به‌روزرسانی', onclick: function () { hx.tap(); load(true); } }, ic('reset'));
    // هدرِ تلگرام خودش «Back» دارد؛ دکمه‌ی تکراری فقط وقتی لازم است که BackButton نباشد
    var back = (tg && tg.BackButton) ? h('span', { class: 'wx-sp', 'aria-hidden': 'true' })
      : h('button', { type: 'button', class: 'wx-btn', 'aria-label': 'بازگشت', onclick: function () { hx.tap(); goHome(); } }, ic('chev'));
    R.city = h('div', { class: 'wx-city' }, h('b', { text: 'سرپل‌ذهاب' }), R.upd = h('small', { text: '' }));
    R.tkIc = h('span', { class: 'ti' }); R.tkTx = h('span', { class: 'tx' }); R.tkDots = h('span', { class: 'dots', 'aria-hidden': 'true' });
    R.tick = h('button', { type: 'button', class: 'wx-tick', 'aria-live': 'polite', onclick: function () { hx.tap(); tickGo(1, true); } }, R.tkIc, R.tkTx, R.tkDots);
    R.hero = h('header', { class: 'wx-hero' },
      h('div', { class: 'wx-fxl', 'aria-hidden': 'true' },
        R.sun, R.moon,
        h('i', { class: 'wx-cloud c1' }), h('i', { class: 'wx-cloud c2' }), h('i', { class: 'wx-cloud c3' }),
        h('i', { class: 'wx-fog f1' }), h('i', { class: 'wx-fog f2' })),
      cv,
      h('div', { class: 'wx-bar' }, back, R.city, R.refresh),
      h('div', { class: 'wx-main' },
        h('div', { class: 'wx-temp' }, R.temp, h('span', { class: 'deg', text: '°' })),
        R.label, R.sub),
      R.tick);
    return R.hero;
  }

  function placeCelestial(d) {
    var today = d.days[0] || {}, sr = mins(today.sunrise), ss = mins(today.sunset), nm = nowMin(), day = d.now.is_day;
    if (day && sr != null && ss != null) {
      var p = clamp((nm - sr) / Math.max(1, ss - sr), 0, 1);
      R.sun.style.left = (24 + 52 * p) + '%';
      R.sun.style.top = (50 - 34 * Math.sin(Math.PI * p)) + '%';
    } else { R.sun.style.left = '50%'; R.sun.style.top = '26%'; }
    if (R.moon.dataset.m !== d.moon) { R.moon.dataset.m = d.moon || ''; R.moon.innerHTML = moonSVG(d.moon); }
  }

  /* ─── اجزای بدنه ──────────────────────────────────────────── */
  function si(i) { return Math.min(i, 7); }
  function sec(title, extra, i) {
    return h('div', { class: 'sec-title wx-in', style: '--i:' + si(i) }, h('span', { text: title }), extra || null);
  }
  function tile(cls, i, kids) {
    return h('div', { class: 'wx-tile wx-in ' + cls, style: '--i:' + si(i) }, kids);
  }
  function tt(icon, text) { var n = TT_MAP[icon]; return h('div', { class: 'wx-tt' }, n ? li(n, 16) : h('span', { text: icon }), h('span', { text: text })); }

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
        h('div', { class: 'wx-di' }, wicon(x.kind, 1, 30), h('span', { class: 'wx-dp' + (x.pop >= 30 ? ' wet' : ''), text: x.pop >= 10 ? x.pop + '٪' : '' })),
        h('div', { class: 'wx-range', dir: 'ltr' },
          h('span', { class: 'lo', text: x.tmin + '°' }),
          h('i', { class: 'bar' }, h('u', { style: 'left:' + l + '%;width:' + w + '%;background:linear-gradient(90deg,' + tcol(x.tmin) + ',' + tcol(x.tmax) + ')' })),
          h('span', { class: 'hi', text: x.tmax + '°' })));
    });
    return h('div', { class: 'wx-card wx-in wx-week', style: '--i:3' }, rows);
  }

  function maxPop(d) { return Math.max.apply(null, d.hours.slice(0, 12).map(function (x) { return x.pop; }).concat([0])); }

  function ringTile(d, i) {
    var today = d.days[0] || {}, mp = maxPop(d), C = 2 * Math.PI * 34;
    var arc = svg('circle', { cx: 44, cy: 44, r: 34, class: 'arc', fill: 'none', 'stroke-width': 8, 'stroke-linecap': 'round', 'stroke-dasharray': C, 'stroke-dashoffset': C, transform: 'rotate(-90 44 44)' });
    var ring = svg('svg', { viewBox: '0 0 88 88', class: 'wx-ring' }, [svg('circle', { cx: 44, cy: 44, r: 34, fill: 'none', 'stroke-width': 8, class: 'trk' }), arc]);
    var num = h('b', { class: 'wx-rn', text: '0٪' });
    var el = tile('', i, [tt('🌧️', 'احتمال بارش'), h('div', { class: 'wx-ringbox' }, ring, num),
      h('div', { class: 'wx-note', text: today.rain > 0 ? 'امروز ' + today.rain + ' میلی‌متر' : 'تا ۱۲ ساعتِ آینده' })]);
    el._go = function () { arc.style.strokeDashoffset = C * (1 - mp / 100); countTo(num, mp, 900, '٪'); };
    return el;
  }

  function airTile(d, i) {
    var a = d.air;
    if (!a) return tile('', i, [tt('🍃', 'کیفیت هوا'), h('div', { class: 'wx-note', text: 'اطلاعاتی در دسترس نیست' })]);
    var ang = clamp(a.aqi / 300, 0, 1) * 180 - 90, col = AQ_COL[a.level];
    var needle = svg('g', { class: 'needle', style: 'transform:rotate(-90deg)' }, [svg('path', { d: 'M50 56 L50 22', stroke: col, 'stroke-width': 3, 'stroke-linecap': 'round' }), svg('circle', { cx: 50, cy: 56, r: 5, fill: col })]);
    var g = svg('svg', { viewBox: '0 0 100 62', class: 'wx-gauge' }, [
      svg('defs', {}, [svg('linearGradient', { id: 'wxa', x1: 0, x2: 1, y1: 0, y2: 0 }, [['0', '#2fb344'], ['.35', '#d9b800'], ['.6', '#f08c00'], ['.8', '#e03131'], ['1', '#9c36b5']].map(function (s) { return svg('stop', { offset: s[0], 'stop-color': s[1] }); }))]),
      svg('path', { d: 'M10 56 A40 40 0 0 1 90 56', fill: 'none', stroke: 'url(#wxa)', 'stroke-width': 9, 'stroke-linecap': 'round', opacity: 0.9 }), needle]);
    var extra = a.dust != null && a.dust >= 50 ? 'گردوغبار ' + a.dust + ' µg' : (a.pm25 != null ? 'PM2.5: ' + a.pm25 : '');
    var el = tile('', i, [tt('🍃', 'کیفیت هوا'), g, h('div', { class: 'wx-big', style: 'color:' + col }, h('b', { text: String(a.aqi) }), h('span', { text: a.label })), h('div', { class: 'wx-note', text: extra })]);
    el._go = function () { needle.style.transform = 'rotate(' + ang + 'deg)'; };
    return el;
  }

  function feelsTile(d, i) {
    var n = d.now, df = (n.feels != null && n.temp != null) ? n.feels - n.temp : 0;
    var txt = Math.abs(df) < 2 ? 'نزدیک به دمای واقعی' : df < 0 ? Math.abs(df) + '° خنک‌تر از دمای واقعی' : df + '° گرم‌تر از دمای واقعی';
    return tile('', i, [tt('🌡️', 'حس واقعی'), h('div', { class: 'wx-big', style: 'color:' + tcol(n.feels == null ? 20 : n.feels) }, h('b', { dir: 'ltr', text: (n.feels == null ? '—' : n.feels) + '°' })), h('div', { class: 'wx-note', text: txt })]);
  }

  function humTile(d, i) {
    var v = d.now.hum == null ? 0 : d.now.hum;
    var water = h('div', { class: 'wx-water' });
    var lbl = v < 30 ? 'هوا خشکه' : v < 60 ? 'مطبوع' : v < 80 ? 'نسبتاً مرطوب' : 'خیلی مرطوب';
    var el = tile('wx-hum', i, [water, tt('💧', 'رطوبت'), h('div', { class: 'wx-big' }, h('b', { text: v + '٪' })), h('div', { class: 'wx-note', text: lbl })]);
    el._go = function () { water.style.transform = 'translateY(' + (100 - v) + '%)'; };
    return el;
  }

  function windTile(d, i) {
    var n = d.now, w = n.wind == null ? 0 : n.wind, dir = n.wind_dir == null ? 0 : n.wind_dir;
    var arrow = svg('g', { class: 'warrow', style: 'transform-origin:50px 50px;transform:rotate(0deg)' }, [svg('path', { d: 'M50 22 L58 46 L50 41 L42 46 Z', fill: 'currentColor' }), svg('path', { d: 'M50 41 L50 72', stroke: 'currentColor', 'stroke-width': 3, 'stroke-linecap': 'round' })]);
    function lt(x, y, s) { var t = svg('text', { x: x, y: y, 'text-anchor': 'middle', class: 'cl' }); t.textContent = s; return t; }
    var comp = svg('svg', { viewBox: '0 0 100 100', class: 'wx-comp' }, [svg('circle', { cx: 50, cy: 50, r: 44, fill: 'none', class: 'ring', 'stroke-width': 2 }),
      lt(50, 14, 'ش'), lt(50, 94, 'ج'), lt(90, 54, 'ق'), lt(10, 54, 'غ'), arrow]);
    var lab = w < 6 ? 'آرام' : w < 20 ? 'نسیم' : w < 40 ? 'باد' : w < 60 ? 'باد شدید' : 'طوفانی';
    var el = tile('', i, [tt('🌬️', 'باد'), comp, h('div', { class: 'wx-big sm' }, h('b', { text: w }), h('span', { dir: 'ltr', text: 'km/h' }), h('span', { text: lab })),
      h('div', { class: 'wx-note', text: 'از ' + DIRS[Math.round(dir / 45) % 8] + (n.gust ? ' · وزش تا ' + n.gust : '') })]);
    el._go = function () { arrow.style.transform = 'rotate(' + ((dir + 180) % 360) + 'deg)'; };
    return el;
  }

  function uvTile(d, i) {
    var uv = (d.days[0] || {}).uv || 0, lab = uv < 3 ? 'کم' : uv < 6 ? 'متوسط' : uv < 8 ? 'زیاد' : uv < 11 ? 'خیلی زیاد' : 'شدید';
    var mk = h('i', { class: 'mk', style: 'left:0%' });
    var el = tile('', i, [tt('☀️', 'پرتو فرابنفش'), h('div', { class: 'wx-big' }, h('b', { text: String(Math.round(uv)) }), h('span', { text: lab })), h('div', { class: 'wx-uv', dir: 'ltr' }, mk),
      h('div', { class: 'wx-note', text: uv >= 6 ? 'ضدآفتاب و کلاه' : 'بدونِ نگرانی' })]);
    el._go = function () { mk.style.left = clamp(uv / 11 * 100, 2, 98) + '%'; };
    return el;
  }

  function presTile(d, i) {
    var p = d.now.pressure, lab = p == null ? '' : p < 1000 ? 'کم‌فشار' : p > 1020 ? 'پرفشار' : 'عادی';
    var cld = d.now.cloud == null ? '' : 'پوششِ ابر ' + d.now.cloud + '٪';
    return tile('', i, [tt('🧭', 'فشارِ هوا'), h('div', { class: 'wx-big' }, h('b', { text: p == null ? '—' : p }), h('span', { text: ' hPa' })), h('div', { class: 'wx-note', text: lab + (cld ? ' · ' + cld : '') })]);
  }

  function moonTile(d, i) {
    var idx = MOON_IDX[d.moon]; if (idx == null) idx = 4;
    var cl = d.now.cloud == null ? 50 : d.now.cloud;
    var note = d.now.is_day ? 'امشب در آسمان است' : (cl < 30 ? 'آسمان برای رصد مناسبه' : 'ابرها دیدِ ماه رو می‌گیرن');
    var m = h('div', { class: 'wx-mbig' }); m.innerHTML = moonSVG(d.moon);
    return tile('', i, [tt('🌙', 'فازِ ماه'), m, h('div', { class: 'wx-big sm' }, h('span', { text: MOON_N[idx] })), h('div', { class: 'wx-note', text: note })]);
  }

  function sunTile(d, i) {
    var t = d.days[0] || {}, sr = mins(t.sunrise), ss = mins(t.sunset);
    if (sr == null || ss == null) return null;
    var nm = nowMin(), p = clamp((nm - sr) / (ss - sr), 0, 1), q = 1 - p;
    var x = q * q * 20 + 2 * q * p * 150 + p * p * 280, y = q * q * 100 + 2 * q * p * -20 + p * p * 100;
    var dot = svg('g', { class: 'sdot', style: 'opacity:0' }, [svg('circle', { cx: x, cy: y, r: 13, fill: 'rgba(255,212,59,.28)' }), svg('circle', { cx: x, cy: y, r: 7, fill: '#ffd43b' })]);
    var art = svg('svg', { viewBox: '0 0 300 112', class: 'wx-arc' }, [svg('path', { d: 'M20 100 Q150 -20 280 100', fill: 'none', class: 'tr', 'stroke-width': 2, 'stroke-dasharray': '4 6' }), svg('path', { d: 'M10 100 H290', class: 'hz', 'stroke-width': 1.5 }), dot]);
    var el = tile('wide', i, [tt('🌅', 'طلوع و غروب'), art,
      h('div', { class: 'wx-sun-t', dir: 'ltr' }, h('span', {}, h('small', { text: 'طلوع' }), h('b', { text: t.sunrise })), h('span', { class: 'mid', dir: 'rtl', text: fmtLen(ss - sr) }), h('span', {}, h('small', { text: 'غروب' }), h('b', { text: t.sunset })))]);
    el._go = function () { dot.style.opacity = (nm >= sr && nm <= ss) ? 1 : 0.35; };
    return el;
  }

  /* ─── توصیه‌ی امروز: کارتِ ساده و خلوت ─────────────────────── */
  function adviceCard(d) {
    var items = d.advice || [], wear = items.filter(function (a) { return a.k === 'wear'; })[0] || { outfit: 'jacket', title: 'پوشش', text: '' };
    var chips = items.filter(function (a) { return a.k !== 'wear'; }).slice(0, 3);
    var k = wear.outfit || wear.ic || 'jacket';
    return h('div', { class: 'wx-adv wx-in', style: '--i:1' },
      h('div', { class: 'wx-adv-top' },
        h('span', { class: 'wx-advic g-' + k }, li(k, 26)),
        h('div', {}, h('h3', { text: wear.title }), h('p', { text: wear.text }))),
      chips.length ? h('div', { class: 'wx-adv-row' }, chips.map(function (x) {
        return h('span', { class: 'wx-it', style: '--c:' + (LV_COL[x.level] || LV_COL.ok) }, li(x.ic, 16), h('em', { text: x.short }));
      })) : null);
  }

  /* ─── تحلیلِ هوش مصنوعی (جمع‌وجور) ─────────────────────────── */
  function aiCard() {
    R.aiText = h('div', { class: 'wx-ai-text', 'aria-live': 'polite' });
    R.aiBtn = h('button', { type: 'button', class: 'wx-ai-btn', onclick: runAI }, li('spark', 16), h('span', { class: 'lbl', text: 'تحلیل هوش مصنوعی' }));
    return h('div', { class: 'wx-ai wx-in', style: '--i:2' }, R.aiBtn, R.aiText);
  }
  function typeOut(text) {
    clearInterval(typeT);
    R.aiText.textContent = ''; R.aiText.classList.add('on');
    if (reduce()) { R.aiText.textContent = text; return; }
    var i = 0;
    typeT = setInterval(function () {
      i += 3; R.aiText.textContent = text.slice(0, i);
      if (i >= text.length) clearInterval(typeT);
    }, 30);
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

  /* ─── نمایشِ تدریجیِ بخش‌ها (فقط وقتی به صفحه می‌رسند) ──────── */
  function reveal(root, instant) {
    instant = true; // بدون انیمیشنِ ورود/اسکرول: کارت‌ها همان لحظه دیده می‌شن
    var els = root.querySelectorAll('.wx-in');
    function show(el) {
      el.style.transitionDelay = '0ms';
      el.classList.add('vis');
      if (el._go) { var f = el._go; el._go = null; try { f(); } catch (e) {} }
    }
    if (io) { io.disconnect(); io = null; }
    if (instant || !('IntersectionObserver' in window)) { for (var i = 0; i < els.length; i++) show(els[i]); return; }
    var fired = false;
    io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { fired = true; io.unobserve(e.target); show(e.target); } });
    }, { rootMargin: '0px 0px -4% 0px' });
    for (var j = 0; j < els.length; j++) io.observe(els[j]);
    setTimeout(function () { if (!fired) for (var q = 0; q < els.length; q++) show(els[q]); }, 1800); // اگر IO کار نکرد، چیزی پنهان نماند
  }

  /* ─── رندرِ کل ─────────────────────────────────────────────── */
  function skeleton() {
    return h('div', { class: 'wx-skel' }, h('i', { style: 'height:128px' }), h('i', { style: 'height:300px' }), h('div', { class: 'g' }, h('i'), h('i'), h('i'), h('i')));
  }

  function paint(d) {
    var n = d.now, day = n.is_day, kind = n.kind, dusty = false, pal = SKY[kind][day ? 0 : 1];
    if (dusty && (kind === 'clear' || kind === 'partly' || kind === 'cloud')) pal = day ? ['#a8733c', '#d29c5a', '#efcf98'] : ['#2b1e12', '#4b3421', '#6c4f35'];
    R.root.dataset.kind = kind; R.root.dataset.day = day;
    R.root.style.setProperty('--s1', pal[0]); R.root.style.setProperty('--s2', pal[1]); R.root.style.setProperty('--s3', pal[2]);
    R.root.classList.toggle('dusty', dusty);
    lastSig = sigOf(d);
    if (painted && R.tv != null && n.temp != null && R.tv !== n.temp) countTo(R.temp, n.temp, 700, '', R.tv);
    else R.temp.textContent = n.temp == null ? '—' : n.temp;
    R.tv = n.temp;
    R.label.textContent = n.label;
    var t0 = d.days[0] || {};
    R.sub.textContent = 'حسِ واقعی ' + (n.feels == null ? '—' : n.feels) + '° · بیشینه ' + (t0.tmax == null ? '—' : t0.tmax) + '° · کمینه ' + (t0.tmin == null ? '—' : t0.tmin) + '°';
    R.upd.textContent = 'به‌روزرسانی ' + d.updated.slice(11) + (d.stale ? ' · قدیمی' : '');
    placeCelestial(d);
    tickSet(d);
    fxSetup(kind, day, n.wind, dusty);
    try { if (shown && tg) tg.setHeaderColor(pal[0]); } catch (e) {}

    // بدنه بعد از هیرو ساخته می‌شود تا بازشدنِ تب یک «تک‌تسکِ سنگین» نسازد
    var tok = ++paintTok;
    function build() {
      if (tok !== paintTok) return;
      var body = h('div', { class: 'wx-body' },
        sec('توصیه‌ی امروز', null, 0), adviceCard(d), aiCard(),
        sec('ساعت‌به‌ساعت', h('small', { text: '۲۴ ساعتِ آینده' }), 3), hourlyCard(d),
        sec('پیش‌بینیِ ۵ روزِ آینده', null, 4), weekCard(d),
        sec('جزئیاتِ امروز', null, 5),
        h('div', { class: 'wx-grid' }, ringTile(d, 6), airTile(d, 7), feelsTile(d, 8), humTile(d, 9), windTile(d, 10), uvTile(d, 11), presTile(d, 12), moonTile(d, 13), sunTile(d, 14)),
        h('div', { class: 'wx-foot', text: 'منبع: PST-WEATHER' }));
      var wasPainted = painted;
      painted = true;
      function swapIn() {
        if (tok !== paintTok) return;
        body.append(sysCardEl());
        R.bodyWrap.replaceChildren(body);
        R.bodyWrap.classList.remove('swap');
        reveal(body, false);
        fxStart();
      }
      if (wasPainted) { R.bodyWrap.classList.add('swap'); setTimeout(swapIn, 200); }
      else swapIn();
    }
    if (painted) build(); else { fxStart(); requestAnimationFrame(function () { setTimeout(build, 0); }); }
  }

  function showError() {
    R.bodyWrap.replaceChildren(h('div', { class: 'wx-err' }, h('div', { class: 'em' }, li('wind', 44)), h('b', { text: 'اتصال به سرویس آب‌وهوا برقرار نشد' }), h('p', { text: 'اینترنت را بررسی کن و دوباره امتحان کن.' }),
      h('button', { type: 'button', class: 'btn', text: 'تلاش دوباره', onclick: function () { load(true); } })));
  }

  function sigOf(d) {
    var c = {}; for (var k in d) if (k !== 'updated' && k !== 'stale') c[k] = d[k];
    return JSON.stringify(c);
  }
  function load(force) {
    if (loading) return;
    loading = true; R.refresh.classList.remove('fin'); R.refresh.classList.add('spin');
    api('/hub/api/weather').then(function (d) {
      D = d; lastAt = Date.now(); LS.set(K, { d: d, at: lastAt });
      if (painted && sigOf(d) === lastSig) {
        R.upd.textContent = 'به‌روزرسانی ' + d.updated.slice(11) + (d.stale ? ' · قدیمی' : '');
      } else paint(d);
      if (force) hx.ok();
    }).catch(function () {
      if (!D) showError(); else if (force) toast('به‌روزرسانی نشد؛ داده‌ی قبلی نمایش داده می‌شود');
      hx.err();
    }).then(function () { loading = false; R.refresh.classList.add('fin'); });
  }

  /* ─── کارتِ «نقشه‌ی سامانه‌ها» داخلِ صفحه‌ی آب‌وهوا ─────────── */
  var sysEl = null, sysBox = null, sysOpen = false, sysScript = 0;
  function sysCardEl() {
    if (sysEl) return sysEl;
    sysBox = h('div', { class: 'sys-box' });
    sysBox.hidden = true;
    var head = h('button', { type: 'button', class: 'sys-head', 'aria-expanded': 'false' },
      h('span', { class: 'sys-t' }, h('b', { text: 'نقشه‌ی سامانه‌های جوّی' }), h('small', { text: 'آسیا · ایران · کرمانشاه و سرپل‌ذهاب' })),
      h('span', { class: 'sys-chev' }, ic('chev')));
    head.addEventListener('click', function () {
      sysOpen = !sysOpen;
      hx.sel && hx.sel();
      sysBox.hidden = !sysOpen;
      head.classList.toggle('open', sysOpen);
      head.setAttribute('aria-expanded', String(sysOpen));
      if (sysOpen) openSystems(); else if (window.HubSystems) window.HubSystems.onHide();
    });
    sysEl = h('div', { class: 'wx-card wx-sysc' }, head, sysBox);
    return sysEl;
  }
  function openSystems() {
    if (window.HubSystems) { window.HubSystems.onShow(); return; }
    if (sysScript === 1) return;
    sysScript = 1;
    if (!document.getElementById('sy-css')) {
      var l = document.createElement('link'); l.id = 'sy-css'; l.rel = 'stylesheet'; l.href = 'systems.css?v=' + (window.HUB_V || '1');
      document.head.appendChild(l);
    }
    var s = document.createElement('script');
    s.src = 'systems.js?v=' + (window.HUB_V || '1');
    s.onload = function () {
      sysScript = 2;
      window.HubSystems.mount(sysBox, { h: h, ic: ic, hx: hx, tg: tg, toast: toast, LS: LS, api: api });
      if (sysOpen) window.HubSystems.onShow();
    };
    s.onerror = function () { sysScript = 0; toast('بارگذاری نقشه ناموفق بود'); };
    document.head.appendChild(s);
  }

  function mount(root, c) {
    h = c.h; ic = c.ic; hx = c.hx; tg = c.tg; toast = c.toast; LS = c.LS; api = c.api; goHome = c.goHome;
    R.root = h('div', { class: 'wx', 'data-kind': 'clear', 'data-day': '1' });
    R.bodyWrap = h('div', { class: 'wx-bw' }, skeleton());
    R.root.append(buildHero(), R.bodyWrap);
    root.replaceChildren(R.root);
    // وقتی هیرو از صفحه بیرون رفت، کانواس و انیمیشن‌های CSS می‌ایستند
    if ('IntersectionObserver' in window) {
      heroIO = new IntersectionObserver(function (es) {
        heroVis = es[es.length - 1].isIntersecting;
        R.root.classList.toggle('paused', !heroVis);
        if (heroVis) fxStart(); else fxStop();
      }, { threshold: 0 });
      heroIO.observe(R.hero);
    }
    var cache = LS.get(K);
    if (cache && cache.d && cache.d.now) { D = cache.d; lastAt = cache.at || 0; paint(D); }
    R.refresh.addEventListener('animationiteration', function () {
      if (R.refresh.classList.contains('fin') && !loading) R.refresh.classList.remove('spin', 'fin');
    });
    load(false);
    var rs = 0;
    window.addEventListener('resize', function () {
      clearTimeout(rs);
      rs = setTimeout(function () { if (D && shown) { fxSetup(D.now.kind, D.now.is_day, D.now.wind, !!(D.air && D.air.dust >= 100)); fxStart(); } }, 200);
    });
    document.addEventListener('visibilitychange', function () { if (document.hidden) { fxStop(); clearInterval(tickT); } else if (shown) { fxStart(); tickRestart(); } });
  }

  window.HubWeather = {
    mount: mount,
    onShow: function () {
      shown = true;
      if (D) {
        try { if (tg) tg.setHeaderColor(SKY[D.now.kind][D.now.is_day ? 0 : 1][0]); } catch (e) {}
        fxSetup(D.now.kind, D.now.is_day, D.now.wind, !!(D.air && D.air.dust >= 100));
      }
      fxStart(); tickRestart();
      if (Date.now() - lastAt > 600000) load(false);
    },
    onHide: function () { shown = false; fxStop(); clearInterval(tickT); tickT = 0; }
  };
})();
