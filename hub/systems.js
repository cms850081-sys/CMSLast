/* نقشه‌ی سامانه‌های جوّی: فقط ایران با حاشیه‌ی همسایه‌ها.
   • زوم فقط بین ۴ (کل ایران) و ۷ (جزئیات). حرکت نقشه داخل کادری محدود است، پس کاشی‌های دورتر لود نمی‌شوند.
   • لایه‌ی رنگی (دما/بارش/ابر) یک تصویر ثابت است که با L.imageOverlay قرار می‌گیرد؛ هنگامِ پن و زوم کار اضافه‌ای نیست.
   • H/L و شهرها نشانگرِ Leaflet هستند. هیچ انیمیشن یا حلقه‌ی فریمی وجود ندارد. */
(function () {
  'use strict';

  var h, ic, hx, toast, LS, api;
  var CDN = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/';
  var Lf = null, map = null, mapEl = null, info = null, statusEl = null;
  var legBar = null, legLo = null, legHi = null, legTitle = null, legMax = null, legTicks = null;
  var mounted = false, shown = false, field = 'temp', showSys = true;
  var grid = null, loadedAt = 0, fetching = false;
  var overlay = null, hlLayer = null, townLayer = null, chipBtns = {}, townMarkers = [];

  var VIEW = { center: [32.5, 50.5], zoom: 4 };
  // کادرِ مجاز برای جابه‌جاییِ نقشه — دقیقاً با محدوده‌ی خودِ شبکه‌ی داده یکیه (lat 22..43, lon 34..67)
  // تا جایی که پن می‌کنی همیشه رنگ هست.
  var BOUNDS = [[21.0, 33.0], [44.0, 68.0]];
  var TOWNS = [
    { n: 'سرپل‌ذهاب', lat: 34.4597, lng: 45.8646, main: true },
    { n: 'کرمانشاه', lat: 34.3142, lng: 47.065 },
    { n: 'اسلام‌آبادغرب', lat: 34.125, lng: 46.433 },
    { n: 'جوانرود', lat: 34.783, lng: 46.52 },
    { n: 'سنندج', lat: 35.3144, lng: 46.9963 },
    { n: 'همدان', lat: 34.7983, lng: 48.5148 },
    { n: 'تهران', lat: 35.6892, lng: 51.389 },
    { n: 'اصفهان', lat: 32.6539, lng: 51.666 },
    { n: 'شیراز', lat: 29.5918, lng: 52.5836 },
    { n: 'مشهد', lat: 36.2605, lng: 59.6168 },
    { n: 'تبریز', lat: 38.0962, lng: 46.2738 },
    { n: 'اهواز', lat: 31.3183, lng: 48.6706 },
    { n: 'بغداد', lat: 33.3152, lng: 44.3661 },
    { n: 'بصره', lat: 30.5085, lng: 47.7804 },
    { n: 'کابل', lat: 34.5553, lng: 69.2075 },
    { n: 'ارومیه', lat: 37.5527, lng: 45.0761 },
    { n: 'بندرعباس', lat: 27.1832, lng: 56.2666 }
  ];
  var DIRS = ['شمال', 'شمال‌شرق', 'شرق', 'جنوب‌شرق', 'جنوب', 'جنوب‌غرب', 'غرب', 'شمال‌غرب'];

  var FIELDS = {
    // دما: بنفش (سرد) → آبی → سفید → زرد → نارنجی → قرمز تیره (گرم)
    temp: { key: 't', lo: -15, hi: 45, unit: '°', title: 'دما', sqrt: false, alpha: 115,
      ticks: [-15, -10, -5, 0, 5, 10, 15, 20, 25, 30, 35, 40, 45],
      stops: [[0, '#3c2a8c'], [0.2, '#2f6fd6'], [0.3, '#8fd3ff'], [0.4, '#eef8ff'], [0.5, '#ffe98a'], [0.65, '#ffb347'], [0.8, '#ff4b1f'], [1, '#7a0010']] },
    // بارش تجمعی امروز: خاکستری کم‌بارش → سبز → آبی → زرد → قرمز → ارغوانی (مقیاس جذر)
    precip: { key: 'pr', lo: 0, hi: 20, unit: ' mm', title: 'بارش تجمعی امروز', sqrt: true, alpha: 175,
      ticks: [0.2, 1, 2.5, 5, 10, 20],
      stops: [[0, '#c8d3dd'], [0.1, '#a8e6a1'], [0.3, '#3ccf4a'], [0.5, '#2b9dff'], [0.7, '#ffe066'], [0.85, '#ff7a1a'], [1, '#c2255c']] },
    // ابر: آبی تیره (صاف) → سفید (ابر کامل)
    cloud: { key: 'cl', lo: 0, hi: 100, unit: '٪', title: 'پوشش ابر', sqrt: false, alpha: 115,
      ticks: [0, 25, 50, 75, 100],
      stops: [[0, '#2a5f9e'], [0.5, '#8db4d9'], [1, '#f2f6fb']] }
  };
  var LUT = {};

  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function hex2rgb(x) { return [parseInt(x.substr(1, 2), 16), parseInt(x.substr(3, 2), 16), parseInt(x.substr(5, 2), 16)]; }

  function lutFor(name) {
    if (LUT[name]) return LUT[name];
    var stops = FIELDS[name].stops.map(function (s) { return [s[0], hex2rgb(s[1])]; });
    var out = new Uint8ClampedArray(256 * 4);
    for (var i = 0; i < 256; i++) {
      var t = i / 255, a = stops[0], b = stops[stops.length - 1];
      for (var k = 0; k < stops.length - 1; k++) {
        if (t >= stops[k][0] && t <= stops[k + 1][0]) { a = stops[k]; b = stops[k + 1]; break; }
      }
      var u = b[0] === a[0] ? 0 : (t - a[0]) / (b[0] - a[0]);
      for (var c = 0; c < 3; c++) out[i * 4 + c] = a[1][c] + (b[1][c] - a[1][c]) * u;
      out[i * 4 + 3] = FIELDS[name].alpha;
    }
    LUT[name] = out;
    return out;
  }
  function norm(name, v) {
    var f = FIELDS[name], x = clamp((v - f.lo) / (f.hi - f.lo), 0, 1);
    return f.sqrt ? Math.sqrt(x) : x;
  }

  /* ─── داده ─────────────────────────────────────────────── */
  function detectHL(g) {
    // آستانه‌ی بالاتر: فقط بیشینه/کمینه‌های واقعاً محسوس، نه نوسانِ ریزِ شبکه
    var cands = [], thr = 2.5, r, c, p, k, j, q, sum, cnt, isMax, isMin, diff;
    for (r = 1; r < g.rows - 1; r++) for (c = 1; c < g.cols - 1; c++) {
      p = g.p[r * g.cols + c];
      if (p == null) continue;
      sum = 0; cnt = 0; isMax = true; isMin = true;
      for (k = -1; k <= 1; k++) for (j = -1; j <= 1; j++) {
        if (!k && !j) continue;
        q = g.p[(r + k) * g.cols + (c + j)];
        if (q == null) { isMax = isMin = false; continue; }
        sum += q; cnt++;
        if (q >= p) isMax = false;
        if (q <= p) isMin = false;
      }
      if (!cnt) continue;
      diff = p - sum / cnt;
      if (isMax && diff >= thr) cands.push({ lat: g.lat0 - r * g.step, lng: g.lon0 + c * g.step, type: 'H', val: p, mag: diff });
      else if (isMin && -diff >= thr) cands.push({ lat: g.lat0 - r * g.step, lng: g.lon0 + c * g.step, type: 'L', val: p, mag: -diff });
    }
    // سرکوبِ نقاطِ نزدیک‌به‌هم: قوی‌ترین نقطه نگه داشته می‌شه و بقیه‌ی نقاطِ هم‌نوع توی شعاعِ نزدیک حذف می‌شن،
    // تا به‌جایِ یه خوشه از H/Lِ چسبیده‌به‌هم، فقط سامانه‌های واقعاً مجزا روی نقشه بمونن.
    cands.sort(function (a, b) { return b.mag - a.mag; });
    var RADIUS = 4 * g.step, out = [];
    for (var i = 0; i < cands.length; i++) {
      var c1 = cands[i], dup = false;
      for (var j2 = 0; j2 < out.length; j2++) {
        var c2 = out[j2];
        if (c2.type === c1.type && Math.abs(c2.lat - c1.lat) < RADIUS && Math.abs(c2.lng - c1.lng) < RADIUS) { dup = true; break; }
      }
      if (!dup) out.push(c1);
    }
    return out;
  }
  function inBox(g, lat, lng) {
    var gy = (g.lat0 - lat) / g.step, gx = (lng - g.lon0) / g.step;
    return gy >= 0 && gy <= g.rows - 1 && gx >= 0 && gx <= g.cols - 1;
  }
  function bil(arr, g, lat, lng) {
    var gy = (g.lat0 - lat) / g.step, gx = (lng - g.lon0) / g.step;
    var i0 = clamp(Math.floor(gy), 0, g.rows - 2), j0 = clamp(Math.floor(gx), 0, g.cols - 2);
    var fy = gy - i0, fx = gx - j0, C = g.cols, k = i0 * C + j0;
    var a = arr[k], b = arr[k + 1], c = arr[k + C], d = arr[k + C + 1];
    if (a == null || b == null || c == null || d == null) return null;
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy;
  }
  function valuesAt(lat, lng) {
    if (!grid || !inBox(grid, lat, lng)) return null;
    var g = grid;
    var gy = Math.round((g.lat0 - lat) / g.step), gx = Math.round((lng - g.lon0) / g.step);
    return {
      t: bil(g.t, g, lat, lng), pr: bil(g.pr, g, lat, lng), cl: bil(g.cl, g, lat, lng), p: bil(g.p, g, lat, lng),
      ws: bil(g.ws, g, lat, lng), h: bil(g.h, g, lat, lng), wd: g.wd[gy * g.cols + gx],
      updated: g.updated, stale: g.stale
    };
  }
  function gridBounds(g) {
    return [
      [g.lat0 - (g.rows - 1) * g.step - g.step / 2, g.lon0 - g.step / 2],
      [g.lat0 + g.step / 2, g.lon0 + (g.cols - 1) * g.step + g.step / 2]
    ];
  }

  /* ─── لایه‌ی رنگی: یک تصویرِ ثابت ───────────────────────── */
  function fieldImage(g) {
    // چندنمونه‌گیریِ دوخطیِ مستقیم — دقیقاً همون فرمولِ bil() که برای کارتِ اطلاعات استفاده می‌شه،
    // پس نقشه و عددی که با کلیک روی یک نقطه می‌بینی همیشه هم‌خوان‌اند. این برخلافِ رسمِ قبلی
    // (رنگِ تخت برای هر خونه + بزرگ‌نماییِ خودکارِ canvas) یه گرادیانِ صافِ واقعی می‌سازه، شبیه نقشه‌ی هواشناسی.
    var F = FIELDS[field], lut = lutFor(field), arr = g[F.key], cols = g.cols, rows = g.rows;
    var RES = 16; // پیکسل به‌ازایِ هر خانه‌ی شبکه؛ کوچیک‌تر از قبل تا بافتِ رویِ GPU سبک‌تر پن بشه
    var W = (cols - 1) * RES + 1, H = (rows - 1) * RES + 1;
    var cv = document.createElement('canvas');
    cv.width = W; cv.height = H;
    var ctx = cv.getContext('2d');
    var img = ctx.createImageData(W, H), d = img.data;
    var painted = 0;
    for (var m = 0; m < arr.length; m++) if (arr[m] != null) painted++;
    for (var py = 0; py < H; py++) {
      var gy = py / RES, i0 = gy >= rows - 1 ? rows - 2 : Math.floor(gy), fy = gy - i0;
      for (var px = 0; px < W; px++) {
        var gx = px / RES, j0 = gx >= cols - 1 ? cols - 2 : Math.floor(gx), fx = gx - j0;
        var k = i0 * cols + j0, o = (py * W + px) * 4;
        var a = arr[k], b = arr[k + 1], c = arr[k + cols], e = arr[k + cols + 1];
        if (a == null || b == null || c == null || e == null) { d[o + 3] = 0; continue; }
        var v = (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + e * fx) * fy;
        if (field === 'precip' && v < 0.05) { d[o + 3] = 0; continue; }
        var idx = clamp(Math.round(norm(field, v) * 255), 0, 255);
        d[o] = lut[idx * 4]; d[o + 1] = lut[idx * 4 + 1]; d[o + 2] = lut[idx * 4 + 2];
        d[o + 3] = field === 'cloud' ? Math.round(30 + 85 * clamp(v / 100, 0, 1)) : lut[idx * 4 + 3];
      }
    }
    ctx.putImageData(img, 0, 0);
    return { url: cv.toDataURL('image/png'), painted: painted };
  }

  function renderField() {
    if (!map) return;
    if (!grid || field === 'none') {
      if (overlay) { map.removeLayer(overlay); overlay = null; }
      updateLegend(0);
      return;
    }
    var img = fieldImage(grid);
    if (!overlay) {
      overlay = Lf.imageOverlay(img.url, gridBounds(grid), { interactive: false, opacity: 1 }).addTo(map);
    } else {
      overlay.setUrl(img.url);
      overlay.setBounds(gridBounds(grid));
    }
    updateLegend(img.painted);
  }

  function renderAll() {
    if (!map) return;
    renderField();
    rebuildHL();
    labelTowns();
    setStatus('داده: ' + (grid ? 'رسید' : 'نرسید') + ' · لایه: ' + field +
      (grid ? ' · به‌روزرسانی ' + (grid.updated || '').slice(11) : ''));
  }

  function fmt(v) { return field === 'precip' ? v.toFixed(1) : String(Math.round(v)); }
  function maxMin() {
    if (!grid) return null;
    var arr = grid[FIELDS[field].key], mx = null, mn = null;
    for (var i = 0; i < arr.length; i++) {
      var v = arr[i];
      if (v == null) continue;
      if (mx === null || v > mx) mx = v;
      if (mn === null || v < mn) mn = v;
    }
    return mx === null ? null : { max: mx, min: mn };
  }
  function labelTowns() {
    townMarkers.forEach(function (x) {
      var label = x.t.n, val = null;
      if (grid && field !== 'none') {
        var v = valuesAt(x.t.lat, x.t.lng);
        if (v && v[FIELDS[field].key] != null) val = fmt(v[FIELDS[field].key]) + FIELDS[field].unit;
      }
      if (val) label += ' ' + val;
      x.m.setIcon(Lf.divIcon({ className: 'sy-town' + (x.t.main ? ' main' : ''), html: '<i></i><span>' + label + '</span>', iconSize: null }));
    });
  }

  /* ─── H/L و شهرها ───────────────────────────────────────── */
  function rebuildHL() {
    if (!hlLayer) return;
    hlLayer.clearLayers();
    if (!showSys || !grid) return;
    grid.hl.forEach(function (x) {
      Lf.marker([x.lat, x.lng], {
        icon: Lf.divIcon({ className: 'sy-hl ' + x.type, html: '<b>' + x.type + '</b><small>' + Math.round(x.val) + '</small>', iconSize: null }),
        interactive: false, keyboard: false
      }).addTo(hlLayer);
    });
  }

  /* ─── کارتِ اطلاعات ───────────────────────────────────── */
  function showInfoAt(lat, lng, name) {
    if (!info) return;
    var v = valuesAt(lat, lng);
    if (!v) {
      info.replaceChildren(h('div', { class: 'sy-i-t' }, h('b', { text: name || 'این نقطه خارج از داده است' })));
      return;
    }
    var wd = v.wd == null ? '' : DIRS[Math.round(v.wd / 45) % 8];
    var title = name || ('نقطه‌ی انتخابی · ' + lat.toFixed(2) + '°، ' + lng.toFixed(2) + '°');
    var items = [
      ['دما', v.t == null ? '—' : Math.round(v.t) + '°'],
      ['بارش', v.pr == null ? '—' : v.pr.toFixed(1) + ' mm'],
      ['ابر', v.cl == null ? '—' : Math.round(v.cl) + '٪'],
      ['باد', v.ws == null ? '—' : Math.round(v.ws) + ' km/h' + (wd ? ' · از ' + wd : '')],
      ['فشار', v.p == null ? '—' : Math.round(v.p) + ' hPa'],
      ['رطوبت', v.h == null ? '—' : Math.round(v.h) + '٪']
    ];
    var kids = [h('div', { class: 'sy-i-t' },
      h('b', { text: title }),
      h('small', { text: 'شبکه‌ی ایران · به‌روزرسانی ' + (v.updated || '').slice(11) + (v.stale ? ' · قدیمی' : '') }))];
    kids.push(h('div', { class: 'sy-i-g' }, items.map(function (x) {
      return h('div', { class: 'sy-i' }, h('small', { text: x[0] }), h('b', { dir: 'ltr', text: x[1] }));
    })));
    info.replaceChildren.apply(info, kids);
  }
  function updateSarpolInfo() {
    if (!info || info.dataset.sticky) return;
    showInfoAt(34.4597, 45.8646, 'سرپل‌ذهاب');
  }

  /* ─── وضعیت و لجند ───────────────────────────────────── */
  function setStatus(t) { if (statusEl) statusEl.textContent = t; }
  function updateLegend(painted) {
    if (!legTitle) return;
    legTicks.replaceChildren();
    if (field === 'none') {
      legTitle.textContent = 'بدون لایه‌ی رنگی';
      legBar.style.background = 'transparent';
      legMax.textContent = '';
      return;
    }
    var f = FIELDS[field];
    legTitle.textContent = f.title + ((field === 'precip' && grid && painted === 0) ? ' · بارشی ثبت نشده' : '');
    legBar.style.background = 'linear-gradient(90deg,' + f.stops.map(function (s) { return s[1] + ' ' + Math.round(s[0] * 100) + '%'; }).join(',') + ')';
    f.ticks.forEach(function (v) {
      var t = h('span', { class: 'sy-tk', dir: 'ltr', text: String(v) });
      t.style.left = (norm(field, v) * 100) + '%';
      legTicks.appendChild(t);
    });
    var mm = maxMin();
    legMax.textContent = mm ? 'بیشینه ' + fmt(mm.max) + f.unit + ' · کمینه ' + fmt(mm.min) + f.unit : '';
  }

  /* ─── نقشه ───────────────────────────────────────────── */
  function ensureLeaflet(cb) {
    if (window.L) { Lf = window.L; return cb(); }
    var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = CDN + 'leaflet.css'; document.head.appendChild(l);
    var s = document.createElement('script'); s.src = CDN + 'leaflet.js';
    s.onload = function () { Lf = window.L; cb(); };
    s.onerror = function () { toast('بارگذاری نقشه ناموفق بود'); };
    document.head.appendChild(s);
  }
  function applyZoomClass() {
    if (!mapEl) return;
    if (map.getZoom() >= 6) mapEl.classList.add('z7'); else mapEl.classList.remove('z7');
  }
  function initMap() {
    map = Lf.map(mapEl, {
      zoomControl: false, attributionControl: true,
      minZoom: VIEW.zoom, maxZoom: 5,
      maxBounds: BOUNDS, maxBoundsViscosity: 0.8,
      fadeAnimation: false, zoomAnimation: false, markerZoomAnimation: false, worldCopyJump: false
    }).setView(VIEW.center, VIEW.zoom);
    Lf.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      subdomains: 'abc', maxZoom: 5, minZoom: VIEW.zoom, updateWhenIdle: true, keepBuffer: 2,
      attribution: '© OpenStreetMap'
    }).addTo(map);
    hlLayer = Lf.layerGroup().addTo(map);
    townLayer = Lf.layerGroup().addTo(map);
    TOWNS.forEach(function (t) {
      var m = Lf.marker([t.lat, t.lng], {
        icon: Lf.divIcon({ className: 'sy-town' + (t.main ? ' main' : ''), html: '<i></i><span>' + t.n + '</span>', iconSize: null }),
        keyboard: false
      });
      m.on('click', function () { showInfoAt(t.lat, t.lng, t.n); });
      m.addTo(townLayer);
      townMarkers.push({ t: t, m: m });
    });
    map.on('click', function (e) { info.dataset.sticky = '1'; showInfoAt(e.latlng.lat, e.latlng.lng, null); });
    map.on('zoomend', applyZoomClass);
    applyZoomClass();
  }

  /* ─── داده‌ی شبکه ─────────────────────────────────────── */
  function loadGrid(cb) {
    fetching = true;
    api('/hub/api/weather/map?region=iran').then(function (d) {
      grid = d;
      grid.hl = detectHL(d);
      try { LS.set('hub:map:iran', { d: d, at: Date.now() }); } catch (e) {}
      cb && cb();
    }).catch(function (err) {
      var reason = (err && err.data && err.data.reason) ? err.data.reason : (err && err.message) ? err.message : '';
      if (!grid && info) {
        info.replaceChildren(h('div', { class: 'sy-i-t' }, h('b', { text: 'داده‌ی نقشه الان دریافت نشد' }), h('small', { text: 'چند دقیقه‌ی دیگر دوباره باز کن' + (reason ? ' · ' + reason : '') })));
      }
      setStatus('خطا در دریافت داده‌ی ایران' + (reason ? ' · ' + reason : ''));
    }).then(function () { fetching = false; });
  }
  function refresh(force) {
    if (fetching) return;
    if (!force && grid && Date.now() - loadedAt < 3600000) return;
    loadedAt = Date.now();
    loadGrid(function () { renderAll(); updateSarpolInfo(); });
  }
  function fromCache() {
    var c = LS.get('hub:map:iran');
    if (c && c.d && c.d.rows && !grid) { grid = c.d; grid.hl = detectHL(c.d); }
  }

  /* ─── کنترل‌ها ─────────────────────────────────────────── */
  function setField(name) {
    field = name;
    Object.keys(chipBtns).forEach(function (k) { if (k !== 'sys') chipBtns[k].classList.toggle('on', k === name); });
    renderAll();
  }
  function setOverlay(on) {
    showSys = on;
    chipBtns.sys.classList.toggle('on', on);
    rebuildHL();
  }

  function build() {
    mapEl = h('div', { class: 'sy-map', 'aria-label': 'نقشه‌ی سامانه‌های جوّی ایران' });
    info = h('div', { class: 'sy-info' });
    statusEl = h('div', { class: 'sy-status' });
    legBar = h('i', { class: 'sy-legbar' });
    legLo = h('small', { class: 'sy-lo', dir: 'ltr' });
    legHi = h('small', { class: 'sy-hi', dir: 'ltr' });
    legTitle = h('b', { text: 'دما' });
    legMax = h('small', { class: 'sy-mm', dir: 'ltr' });
    legTicks = h('div', { class: 'sy-ticks' });

    var fields = [['temp', 'دما'], ['precip', 'بارش'], ['cloud', 'ابر'], ['none', 'بدون رنگ']];
    var chipEls = fields.map(function (c) {
      var b = h('button', { type: 'button', class: 'sy-chip' + (c[0] === field ? ' on' : ''),
        onclick: function () { hx.sel && hx.sel(); setField(c[0]); } }, h('span', { text: c[1] }));
      chipBtns[c[0]] = b;
      return b;
    });
    var sysChip = h('button', { type: 'button', class: 'sy-chip on',
      onclick: function () { hx.sel && hx.sel(); setOverlay(!showSys); } }, h('span', { text: 'سامانه‌ها (H/L)' }));
    chipBtns.sys = sysChip;

    return h('div', { class: 'sy' },
      h('div', { class: 'sy-chips' }, chipEls, sysChip),
      mapEl,
      statusEl,
      h('div', { class: 'sy-leg' },
        h('div', { class: 'sy-legtop' }, legTitle, legMax),
        h('div', { class: 'sy-legbarwrap' }, legBar, legTicks)),
      info,
      h('div', { class: 'sy-foot', text: 'داده: open-meteo · نقشه: OpenStreetMap' }));
  }

  /* ─── ورودی‌ها ───────────────────────────────────────── */
  function mount(el, c) {
    if (mounted) return;
    mounted = true;
    h = c.h; ic = c.ic; hx = c.hx; toast = c.toast; LS = c.LS; api = c.api;
    el.replaceChildren(build());
    fromCache();
  }
  function onShow() {
    shown = true;
    ensureLeaflet(function () {
      if (!shown) return;
      if (!map) initMap();
      map.invalidateSize();
      renderAll();
      setTimeout(function () { if (map) { map.invalidateSize(); renderAll(); } }, 150);
      refresh(false);
    });
  }
  function onHide() { shown = false; }

  window.HubSystems = { mount: mount, onShow: onShow, onHide: onHide };
})();
