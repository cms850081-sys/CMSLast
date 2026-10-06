/* بخشِ «سامانه‌ها»: نقشه‌ی جوّی روی آسیا، با جزئیاتِ کرمانشاه و سرپل‌ذهاب.
   هدف: روانی روی گوشی‌های ضعیف (FPS اولویت است)
   • داده از /hub/api/weather/map: دو شبکه‌ی فشرده (آسیا و کرمانشاه)، هر ۱۵ دقیقه کش می‌شود
   • لایه‌ی رنگی (دما/بارش/ابر) فقط روی یک بافرِ کوچک (هر خانه ۶px) محاسبه و با drawImage بزرگ می‌شود؛ هنگامِ drag بازسازی نمی‌شود
   • موج/باد: ذره‌های کم‌تعداد که در یک path کشیده می‌شوند؛ هنگامِ drag و وقتی بخش دیده نمی‌شود متوقف‌اند
   • تعدادِ ذره‌ها با سنجشِ زمانِ فریم خودکار کم می‌شود؛ روی دستگاه‌های ضعیف از ابتدا کمتر است
   • هیچ فیلتر/blur/backdrop-filter نیست؛ انیمیشنِ fade نقشه خاموش است */
(function () {
  'use strict';

  var h, ic, hx, tg, toast, LS, api, goHome;
  var CDN = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/';
  var statusEl = null; var Lf = null, map = null, root = null, mapEl = null, info = null, legBar = null, legLo = null, legHi = null, legTitle = null;
  var cvField, ctxField, cvWave, ctxWave, buf, bctx;
  var mounted = false, shown = false, field = 'temp', showSys = true, showWave = true;
  var grids = {}, loadedAt = 0, fetching = false, hlLayer = null, townLayer = null;
  var chipBtns = {}, segBtns = {}, townMarkers = [];
  var lastW = 0, lastH = 0, redrawT = 0, rafW = 0, lastT = 0, dragging = false;
  var CELL = 6, cw = 0, ch = 0, cu = null, cv = null, cvOK = null;
  var parts = [], nParts = 0, maxParts = 0, perfAcc = 0, perfN = 0;
  var KRM = { lat1: 33.6, lat2: 35.1, lng1: 45.2, lng2: 47.7 };
  var TOWNS = [
    { n: 'سرپل‌ذهاب', lat: 34.4597, lng: 45.8646, main: true },
    { n: 'کرمانشاه', lat: 34.3142, lng: 47.065 },
    { n: 'اسلام‌آبادغرب', lat: 34.125, lng: 46.433 },
    { n: 'جوانرود', lat: 34.783, lng: 46.52 },
    { n: 'روانسر', lat: 34.7116, lng: 46.66 },
    { n: 'کنگاور', lat: 34.5, lng: 47.9667 },
    { n: 'هرسین', lat: 34.26, lng: 47.59 },
    { n: 'گیلان‌غرب', lat: 34.153, lng: 46.08 },
    { n: 'قصرشیرین', lat: 34.516, lng: 45.579 },
    { n: 'پاوه', lat: 35.02, lng: 46.45 }
  ];
  var DIRS = ['شمال', 'شمال‌شرق', 'شرق', 'جنوب‌شرق', 'جنوب', 'جنوب‌غرب', 'غرب', 'شمال‌غرب'];

  /* ─── تعریفِ لایه‌های رنگی ───────────────────────────────── */
  var FIELDS = {
    temp: { lo: -15, hi: 45, unit: '°', title: 'دما', map: 'lin', stops: [[0, '#3b5bdb'], [0.25, '#4dabf7'], [0.4, '#38d9a9'], [0.55, '#a9e34b'], [0.65, '#ffd43b'], [0.78, '#ff922b'], [1, '#e03131']] },
    precip: { lo: 0, hi: 20, unit: 'mm', title: 'بارش', map: 'sqrt', stops: [[0, '#74c0fc'], [0.25, '#339af0'], [0.5, '#7048e8'], [0.75, '#e64980'], [1, '#c2255c']] },
    cloud: { lo: 0, hi: 100, unit: '٪', title: 'ابر', map: 'lin', stops: [[0, '#e9eef5'], [1, '#ffffff']] }
  };
  var LUT = {};

  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function hex2rgb(x) { return [parseInt(x.substr(1, 2), 16), parseInt(x.substr(3, 2), 16), parseInt(x.substr(5, 2), 16)]; }
  function buildLUT(f) {
    var out = new Uint8ClampedArray(256 * 4), st = f.stops.map(function (s) { return [s[0], hex2rgb(s[1])]; });
    for (var i = 0; i < 256; i++) {
      var t = i / 255, a = st[0], b = st[st.length - 1];
      for (var k = 0; k < st.length - 1; k++) { if (t >= st[k][0] && t <= st[k + 1][0]) { a = st[k]; b = st[k + 1]; break; } }
      var u = b[0] === a[0] ? 0 : (t - a[0]) / (b[0] - a[0]);
      for (var c = 0; c < 3; c++) out[i * 4 + c] = a[1][c] + (b[1][c] - a[1][c]) * u;
      out[i * 4 + 3] = 200;
    }
    return out;
  }
  function lutFor(name) { if (!LUT[name]) LUT[name] = buildLUT(FIELDS[name]); return LUT[name]; }
  function fieldNorm(name, v) {
    var f = FIELDS[name], x = clamp((v - f.lo) / (f.hi - f.lo), 0, 1);
    return f.map === 'sqrt' ? Math.sqrt(x) : x;
  }

  /* ─── داده و نمونه‌برداری ───────────────────────────────── */
  function prepGrid(g) {
    var n = g.rows * g.cols, i, u = new Float32Array(n), v = new Float32Array(n);
    for (i = 0; i < n; i++) {
      var s = g.ws[i], d = g.wd[i];
      if (s == null || d == null) { u[i] = NaN; v[i] = NaN; continue; }
      var r = d * Math.PI / 180;
      u[i] = -s * Math.sin(r);   // مؤلفه‌ی شرق‌به‌غرب (x روی صفحه)
      v[i] = s * Math.cos(r);    // مؤلفه‌ی شمال‌به‌جنوب (y روی صفحه، رو به پایین)
    }
    g.u = u; g.v = v;
    g.hl = detectHL(g);
    return g;
  }
  // سامانه‌ها: قله/دره‌ی محلیِ فشار (در همسایگیِ ۳×۳)
  function detectHL(g) {
    var out = [], thr = g.step > 0.5 ? 1.5 : 0.4, r, c, i, p, k, sum, cnt, isMax, isMin;
    for (r = 1; r < g.rows - 1; r++) for (c = 1; c < g.cols - 1; c++) {
      i = r * g.cols + c; p = g.p[i];
      if (p == null) continue;
      sum = 0; cnt = 0; isMax = true; isMin = true;
      for (k = -1; k <= 1; k++) for (var j = -1; j <= 1; j++) {
        if (!k && !j) continue;
        var q = g.p[(r + k) * g.cols + (c + j)];
        if (q == null) { isMax = isMin = false; continue; }
        sum += q; cnt++;
        if (q >= p) isMax = false;
        if (q <= p) isMin = false;
      }
      if (!cnt) continue;
      var diff = p - sum / cnt;
      if (isMax && diff >= thr) out.push({ lat: g.lat0 - r * g.step, lng: g.lon0 + c * g.step, type: 'H', val: p });
      else if (isMin && -diff >= thr) out.push({ lat: g.lat0 - r * g.step, lng: g.lon0 + c * g.step, type: 'L', val: p });
    }
    return out;
  }
  function inBox(g, lat, lng) {
    var gy = (g.lat0 - lat) / g.step, gx = (lng - g.lon0) / g.step;
    return gy >= 0 && gy <= g.rows - 1 && gx >= 0 && gx <= g.cols - 1;
  }
  function gridFor(lat, lng) {
    if (grids.kermanshah && inBox(grids.kermanshah, lat, lng)) return grids.kermanshah;
    if (grids.asia && inBox(grids.asia, lat, lng)) return grids.asia;
    return null;
  }
  function bil(arr, g, lat, lng) {
    var gy = (g.lat0 - lat) / g.step, gx = (lng - g.lon0) / g.step;
    var i0 = Math.min(Math.floor(gy), g.rows - 2), j0 = Math.min(Math.floor(gx), g.cols - 2);
    if (i0 < 0) i0 = 0; if (j0 < 0) j0 = 0;
    var fy = gy - i0, fx = gx - j0, C = g.cols, k = i0 * C + j0;
    var a = arr[k], b = arr[k + 1], c = arr[k + C], d = arr[k + C + 1];
    if (a == null || b == null || c == null || d == null || a !== a || b !== b || c !== c || d !== d) return null;
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy;
  }
  function valuesAt(lat, lng) {
    var g = gridFor(lat, lng); if (!g) return null;
    var gy = Math.round((g.lat0 - lat) / g.step), gx = Math.round((lng - g.lon0) / g.step), k = gy * g.cols + gx;
    var wdDeg = g.wd[k];
    return {
      t: bil(g.t, g, lat, lng), pr: bil(g.pr, g, lat, lng), cl: bil(g.cl, g, lat, lng), p: bil(g.p, g, lat, lng),
      ws: bil(g.ws, g, lat, lng), h: bil(g.h, g, lat, lng), wd: wdDeg,
      detail: g.region === 'kermanshah', updated: g.updated, stale: g.stale
    };
  }

  /* ─── رسمِ لایه‌ی رنگی ─────────────────────────────────── */
  function drawField() {
    if (!map || !ctxField) return;
    var s = map.getSize(), W = s.x, H = s.y;
    lastW = W; lastH = H;
    cvField.width = W; cvField.height = H;
    cvWave.width = W; cvWave.height = H;
    var nw = Math.ceil(W / CELL), nh = Math.ceil(H / CELL), n = nw * nh;
    if (buf.width !== nw) buf.width = nw;
    if (buf.height !== nh) buf.height = nh;
    if (!cu || cu.length !== n) { cu = new Float32Array(n); cv = new Float32Array(n); cvOK = new Uint8Array(n); }
    cw = nw; ch = nh;
    var img = bctx.createImageData(nw, nh), d = img.data;
    var lut = field !== 'none' ? lutFor(field) : null, F = FIELDS[field];
    var grid, lat, lng, p, k, gkey, val, idx, a, painted = 0;
    for (var j = 0; j < nh; j++) for (var i = 0; i < nw; i++) {
      k = j * nw + i;
      p = map.containerPointToLatLng(Lf.point(i * CELL + CELL / 2, j * CELL + CELL / 2));
      lat = p.lat; lng = p.lng;
      grid = gridFor(lat, lng);
      cvOK[k] = 0;
      if (!grid) { d[k * 4 + 3] = 0; continue; }
      var uu = bil(grid.u, grid, lat, lng), vv = bil(grid.v, grid, lat, lng);
      if (uu != null) { cu[k] = uu; cv[k] = vv; cvOK[k] = 1; }
      if (!lut) { d[k * 4 + 3] = 0; continue; }
      val = bil(grid[field === 'temp' ? 't' : field === 'precip' ? 'pr' : 'cl'], grid, lat, lng);
      if (val == null || (field === 'precip' && val < 0.05)) { d[k * 4 + 3] = 0; continue; }
      idx = Math.round(fieldNorm(field, val) * 255);
      a = lut[idx * 4 + 3];
      if (field === 'cloud') a = Math.round(40 + 150 * clamp(val / 100, 0, 1));
      d[k * 4] = lut[idx * 4]; d[k * 4 + 1] = lut[idx * 4 + 1]; d[k * 4 + 2] = lut[idx * 4 + 2]; d[k * 4 + 3] = a;
      if (a > 0) painted++;
    }
    bctx.putImageData(img, 0, 0);
    ctxField.clearRect(0, 0, W, H);
    ctxField.imageSmoothingEnabled = true;
    ctxField.drawImage(buf, 0, 0, nw * CELL, nh * CELL);
    if (F) updateLegend();
    setStatus('نقشه ' + W + '×' + H + ' · آسیا: ' + (grids.asia ? 'رسیده' : 'نرسیده') +
      ' · جزئیات: ' + (grids.kermanshah ? 'رسیده' : 'نرسیده') + ' · پیکسل رنگی: ' + painted +
      ' · لایه: ' + field);
    if (showWave) ensureParticles();
  }
  function setStatus(t) { if (statusEl) statusEl.textContent = t; }
  function drawFieldSafe() {
    try { drawField(); }
    catch (e) { setStatus('خطا در رسم لایه: ' + (e && e.message ? e.message : e)); }
  }
  function scheduleDraw(immediate) {
    if (immediate) { clearTimeout(redrawT); redrawT = 0; drawFieldSafe(); return; }
    if (!redrawT) redrawT = setTimeout(function () { redrawT = 0; drawFieldSafe(); }, 160);
  }

  /* ─── موج/باد: ذره‌های سبک ───────────────────────────────── */
  function lowEnd() {
    return (navigator.hardwareConcurrency || 4) <= 4 || (navigator.deviceMemory || 4) <= 3;
  }
  function newPart(p, rnd) {
    p.x = Math.random() * lastW; p.y = Math.random() * lastH;
    p.life = 50 + Math.random() * 90 | 0; return p;
  }
  function ensureParticles() {
    maxParts = lowEnd() ? 110 : 240;
    if (!nParts) nParts = Math.floor(maxParts * 0.6);
    if (nParts > maxParts) nParts = maxParts;
    while (parts.length < nParts) parts.push(newPart({}));
    startWave();
  }
  function waveFrame(t) {
    rafW = 0;
    if (!shown || !showWave || dragging || !cu) return;
    rafW = requestAnimationFrame(waveFrame);
    var dt = lastT ? Math.min(0.05, (t - lastT) / 1000) : 0.016; lastT = t;
    var c = ctxWave, i, p, ix, iy, idx, ux, vy, nx, ny;
    c.clearRect(0, 0, lastW, lastH);
    c.strokeStyle = 'rgba(255,255,255,.8)';
    c.lineWidth = 1.3; c.lineCap = 'round';
    c.beginPath();
    for (i = 0; i < nParts; i++) {
      p = parts[i];
      ix = (p.x / CELL) | 0; iy = (p.y / CELL) | 0;
      idx = iy * cw + ix;
      ux = 0; vy = 0;
      if (ix >= 0 && iy >= 0 && ix < cw && iy < ch && cvOK[idx]) { ux = cu[idx]; vy = cv[idx]; }
      var sp = Math.sqrt(ux * ux + vy * vy);
      var k = 1.4 * dt;               // px در ثانیه برای هر km/h
      nx = p.x + ux * k; ny = p.y + vy * k;
      if (sp > 0.5) { c.moveTo(p.x, p.y); c.lineTo(nx, ny); }
      p.x = nx; p.y = ny; p.life--;
      if (p.life <= 0 || p.x < 0 || p.y < 0 || p.x > lastW || p.y > lastH) newPart(p);
    }
    c.stroke();
    // تنظیمِ خودکارِ کیفیت: اگر فریم‌ها کند بود، ذره‌ها کم می‌شوند
    perfAcc += dt * 1000; perfN++;
    if (perfN >= 45) {
      var avg = perfAcc / perfN; perfAcc = 0; perfN = 0;
      if (avg > 30 && nParts > 30) nParts = Math.floor(nParts * 0.7);
      else if (avg < 18 && nParts < maxParts) nParts = Math.min(maxParts, nParts + 15);
    }
  }
  function startWave() { if (!rafW && shown && showWave) { lastT = 0; rafW = requestAnimationFrame(waveFrame); } }
  function stopWave() { if (rafW) cancelAnimationFrame(rafW); rafW = 0; lastT = 0; if (ctxWave) ctxWave.clearRect(0, 0, lastW, lastH); }

  /* ─── نقشه و بارگذاری ───────────────────────────────────── */
  function ensureLeaflet(cb) {
    if (window.L) { Lf = window.L; return cb(); }
    var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = CDN + 'leaflet.css'; document.head.appendChild(l);
    var s = document.createElement('script'); s.src = CDN + 'leaflet.js';
    s.onload = function () { Lf = window.L; cb(); };
    s.onerror = function () { toast('بارگذاری نقشه ناموفق بود'); };
    document.head.appendChild(s);
  }
  function initMap() {
    map = Lf.map(mapEl, {
      zoomControl: false, attributionControl: true, minZoom: 2, maxZoom: 9,
      fadeAnimation: false, zoomAnimation: false, markerZoomAnimation: false, worldCopyJump: false, preferCanvas: false
    }).setView([36, 75], 3);
    Lf.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      subdomains: 'abc', maxZoom: 9, updateWhenIdle: true, keepBuffer: 1, detectRetina: false,
      attribution: '© OpenStreetMap'
    }).addTo(map);
    // لایه‌ها روی تایل‌ها و زیرِ نشانگرها
    cvField = document.createElement('canvas'); cvField.className = 'sy-cv'; cvField.style.zIndex = 350;
    cvWave = document.createElement('canvas'); cvWave.className = 'sy-cv'; cvWave.style.zIndex = 360;
    cvWave.style.pointerEvents = 'none';
    var cont = map.getContainer();
    cont.appendChild(cvField); cont.appendChild(cvWave);
    ctxField = cvField.getContext('2d'); ctxWave = cvWave.getContext('2d');
    buf = document.createElement('canvas'); bctx = buf.getContext('2d');
    hlLayer = Lf.layerGroup().addTo(map);
    townLayer = Lf.layerGroup().addTo(map);
    TOWNS.forEach(function (t) {
      var m = Lf.marker([t.lat, t.lng], {
        icon: Lf.divIcon({ className: 'sy-town' + (t.main ? ' main' : ''), html: '<i></i><span>' + t.n + '</span>', iconSize: null }),
        keyboard: false, interactive: true
      });
      m.on('click', function () { showInfoAt(t.lat, t.lng, t.n); });
      townMarkers.push(m); m.addTo(townLayer);
    });
    map.on('movestart', function () { dragging = true; stopWave(); });
    map.on('moveend', function () { dragging = false; scheduleDraw(true); if (showWave) startWave(); });
    map.on('move', function () { scheduleDraw(false); });
    map.on('zoomend', applyZoomClass);
    map.on('resize', function () { scheduleDraw(true); });
    map.on('click', function (e) { info.dataset.sticky = '1'; showInfoAt(e.latlng.lat, e.latlng.lng, null); });
    applyZoomClass();
  }
  function applyZoomClass() {
    var z = map.getZoom(), cls = mapEl.classList;
    if (z >= 7) cls.add('z7'); else cls.remove('z7');
  }
  function loadGrid(region, cb) {
    fetching = true;
    api('/hub/api/weather/map?region=' + region).then(function (d) {
      grids[region] = prepGrid(d);
      try { LS.set('hub:map:' + region, { d: d, at: Date.now() }); } catch (e) {}
      cb && cb();
    }).catch(function () {
      if (region === 'asia' && !grids.asia && info) {
        info.replaceChildren(h('div', { class: 'sy-i-t' }, h('b', { text: 'داده‌ی نقشه الان دریافت نشد' }), h('small', { text: 'چند دقیقه‌ی دیگر دوباره باز کن' })));
      }
      cb && cb(true);
    }).then(function () { fetching = false; });
  }
  function refreshAll(force) {
    if (fetching) return;
    if (!force && Date.now() - loadedAt < 900000 && grids.asia) return;
    loadedAt = Date.now();
    var wantDetail = !!grids.kermanshah || map.getZoom() >= 6;
    loadGrid('asia', function () {
      rebuildHL(); scheduleDraw(true); updateSarpolInfo();
    });
    if (wantDetail) loadGrid('kermanshah', function () { rebuildHL(); scheduleDraw(true); updateSarpolInfo(); });
  }
  function fromCache() {
    ['asia', 'kermanshah'].forEach(function (r) {
      var c = LS.get('hub:map:' + r);
      if (c && c.d && c.d.rows && !grids[r]) grids[r] = prepGrid(c.d);
    });
  }

  /* ─── سامانه‌ها (H / L) ─────────────────────────────────── */
  function rebuildHL() {
    if (!hlLayer) return;
    hlLayer.clearLayers();
    if (!showSys) return;
    var list = [];
    if (grids.asia) grids.asia.hl.forEach(function (x) {
      if (grids.kermanshah && inBox(grids.kermanshah, x.lat, x.lng)) return;  // جزئیات جای آن را گرفته
      list.push(x);
    });
    if (grids.kermanshah) list = list.concat(grids.kermanshah.hl);
    list.forEach(function (x) {
      var m = Lf.marker([x.lat, x.lng], {
        icon: Lf.divIcon({ className: 'sy-hl ' + x.type, html: '<b>' + x.type + '</b><small>' + Math.round(x.val) + '</small>', iconSize: null }),
        interactive: false, keyboard: false
      });
      m.addTo(hlLayer);
    });
  }

  /* ─── کارتِ اطلاعات و راهنما ───────────────────────────── */
  function showInfoAt(lat, lng, name) {
    var v = valuesAt(lat, lng);
    if (!info) return;
    if (!v) { info.replaceChildren(h('div', { class: 'sy-i-t', text: name || 'این نقطه خارج از داده است' })); return; }
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
    var kids = [h('div', { class: 'sy-i-t' }, h('b', { text: title }), h('small', { text: (v.detail ? 'جزئیات کرمانشاه' : 'شبکه‌ی آسیا') + ' · به‌روزرسانی ' + (v.updated || '').slice(11) + (v.stale ? ' · قدیمی' : '') }))];
    kids.push(h('div', { class: 'sy-i-g' }, items.map(function (x) {
      return h('div', { class: 'sy-i' }, h('small', { text: x[0] }), h('b', { dir: 'ltr', text: x[1] }));
    })));
    if (name === 'سرپل‌ذهاب') {
      kids.push(h('button', { type: 'button', class: 'sy-go', onclick: function () { hx.tap(); goWeather(); } }, h('span', { text: 'پیش‌بینی کامل سرپل‌ذهاب' }), ic('chev')));
    }
    info.replaceChildren.apply(info, kids);
  }
  var goWeatherFn = null;
  function goWeather() { if (goWeatherFn) goWeatherFn(); }
  function updateSarpolInfo() {
    if (!info || info.dataset.sticky) return;
    showInfoAt(34.4597, 45.8646, 'سرپل‌ذهاب');
  }
  function updateLegend() {
    var f = FIELDS[field];
    legTitle.textContent = f.title;
    legBar.style.background = 'linear-gradient(90deg,' + f.stops.map(function (s) { return s[1] + ' ' + Math.round(s[0] * 100) + '%'; }).join(',') + ')';
    legLo.textContent = f.lo + f.unit; legHi.textContent = f.hi + f.unit;
  }

  /* ─── کنترل‌ها ─────────────────────────────────────────── */
  function setField(name) {
    field = name;
    Object.keys(chipBtns).forEach(function (k) { chipBtns[k].classList.toggle('on', k === name); });
    if (name === 'none') { ctxField.clearRect(0, 0, lastW, lastH); legTitle.textContent = 'بدون لایه‌ی رنگی'; legBar.style.background = 'transparent'; legLo.textContent = ''; legHi.textContent = ''; return; }
    scheduleDraw(true);
  }
  function setOverlay(name, on) {
    if (name === 'sys') { showSys = on; rebuildHL(); }
    else { showWave = on; if (on) ensureParticles(); else stopWave(); }
    chipBtns[name].classList.toggle('on', on);
  }
  function setRegion(r) {
    Object.keys(segBtns).forEach(function (k) { segBtns[k].classList.toggle('on', k === r); });
    if (r === 'kermanshah') { map.setView([34.6, 46.4], 8, { animate: false }); }
    else { map.setView([36, 75], 3, { animate: false }); }
    refreshAll(false);
  }

  function build() {
    mapEl = h('div', { class: 'sy-map', 'aria-label': 'نقشه‌ی سامانه‌های جوّی' });
    info = h('div', { class: 'sy-info' });
    legBar = h('i', { class: 'sy-legbar' });
    legLo = h('small', { class: 'sy-lo', dir: 'ltr' }); legHi = h('small', { class: 'sy-hi', dir: 'ltr' }); legTitle = h('b', { text: 'دما' });
    var back = (tg && tg.BackButton) ? h('span', { class: 'sy-sp' })
      : h('button', { type: 'button', class: 'sy-btn', 'aria-label': 'بازگشت', onclick: function () { hx.tap(); goHome(); } }, ic('chev'));
    var refresh = h('button', { type: 'button', class: 'sy-btn', 'aria-label': 'به‌روزرسانی', onclick: function () { hx.tap(); refreshAll(true); } }, ic('reset'));
    var chips = [['temp', 'دما'], ['precip', 'بارش'], ['cloud', 'ابر'], ['none', 'بدون رنگ']];
    var chipEls = chips.map(function (c) {
      var b = h('button', { type: 'button', class: 'sy-chip' + (c[0] === field ? ' on' : ''), onclick: function () { hx.sel && hx.sel(); setField(c[0]); } }, h('span', { text: c[1] }));
      chipBtns[c[0]] = b; return b;
    });
    var ov = [['sys', 'سامانه‌ها (H/L)'], ['wave', 'موج و باد']].map(function (c) {
      var b = h('button', { type: 'button', class: 'sy-chip ov on', onclick: function () { hx.sel && hx.sel(); setOverlay(c[0], !chipBtns[c[0]].classList.contains('on')); } }, h('span', { text: c[1] }));
      chipBtns[c[0]] = b; return b;
    });
    var sBtn = function (r, t) {
      var b = h('button', { type: 'button', class: 'sy-seg-b' + (r === 'asia' ? ' on' : ''), onclick: function () { hx.tap(); setRegion(r); } }, h('span', { text: t }));
      segBtns[r] = b; return b;
    };
    statusEl = h('div', { class: 'sy-status' });
    root = h('div', { class: 'sy' },
      h('div', { class: 'sy-seg' }, sBtn('asia', 'آسیا'), sBtn('kermanshah', 'کرمانشاه و سرپل‌ذهاب')),
      h('div', { class: 'sy-chips' }, chipEls),
      h('div', { class: 'sy-chips ovs' }, ov),
      mapEl,
      statusEl,
      h('div', { class: 'sy-leg' }, legTitle, h('div', { class: 'sy-legrow' }, legLo, legBar, legHi)),
      info,
      h('div', { class: 'sy-foot', text: 'داده: open-meteo · نقشه: OpenStreetMap' }));
    return root;
  }

  function mount(el, c) {
    if (mounted) return;
    mounted = true;
    h = c.h; ic = c.ic; hx = c.hx; tg = c.tg; toast = c.toast; LS = c.LS; api = c.api; goHome = c.goHome;
    goWeatherFn = c.goWeather || null;
    el.replaceChildren(build());
    fromCache();
    maxParts = lowEnd() ? 110 : 240;
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) stopWave(); else if (shown) startWave();
    });
  }

  function onShow() {
    shown = true;
    ensureLeaflet(function () {
      if (!map) { initMap(); }
      map.invalidateSize();
      rebuildHL(); scheduleDraw(true);
      setTimeout(function () { if (map) { map.invalidateSize(); scheduleDraw(true); } }, 150);
      if (!info.childNodes.length) updateSarpolInfo();
      refreshAll(false);
      if (showWave) startWave();
    });
  }
  function onHide() {
    shown = false;
    stopWave();
  }

  window.HubSystems = { mount: mount, onShow: onShow, onHide: onHide, setGoWeather: function (f) { goWeatherFn = f; } };
})();
