/* بخشِ «نقشه‌ی سامانه‌های جوّی» (داخلِ کارتِ صفحه‌ی آب‌وهوا).
   • لایه‌های رنگی (دما/بارش/ابر) یک بار به‌صورت تصویر رسم می‌شوند و با L.imageOverlay روی نقشه قرار می‌گیرند.
     پس موقع پن و زوم هیچ کاری روی گوشی انجام نمی‌شود؛ Leaflet خودش جابه‌جا می‌کند.
   • H/L سامانه‌های فشار و شهرها نشانگرِ Leaflet هستند.
   • هیچ انیمیشن و canvas هم‌گامِ فریمی وجود ندارد. */
(function () {
  'use strict';

  var h, ic, hx, toast, LS, api;
  var CDN = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/';
  var Lf = null, map = null, mapEl = null, info = null, statusEl = null;
  var legBar = null, legLo = null, legHi = null, legTitle = null;
  var mounted = false, shown = false, field = 'temp', showSys = true;
  var grids = {}, loadedAt = 0, fetching = false;
  var overlays = { asia: null, kermanshah: null };
  var hlLayer = null, townLayer = null, chipBtns = {}, segBtns = {};

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

  var FIELDS = {
    temp: { key: 't', lo: -15, hi: 45, unit: '°', title: 'دما', sqrt: false,
      stops: [[0, '#3b5bdb'], [0.25, '#4dabf7'], [0.4, '#38d9a9'], [0.55, '#a9e34b'], [0.65, '#ffd43b'], [0.78, '#ff922b'], [1, '#e03131']] },
    precip: { key: 'pr', lo: 0, hi: 20, unit: ' mm', title: 'بارش', sqrt: true,
      stops: [[0, '#74c0fc'], [0.25, '#339af0'], [0.5, '#7048e8'], [0.75, '#e64980'], [1, '#c2255c']] },
    cloud: { key: 'cl', lo: 0, hi: 100, unit: '٪', title: 'ابر', sqrt: false,
      stops: [[0, '#e9eef5'], [1, '#ffffff']] }
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
      out[i * 4 + 3] = 170;
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
    var out = [], thr = g.step > 0.5 ? 1.5 : 0.4, r, c, p, k, j, q, sum, cnt, isMax, isMin, diff;
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
      if (isMax && diff >= thr) out.push({ lat: g.lat0 - r * g.step, lng: g.lon0 + c * g.step, type: 'H', val: p });
      else if (isMin && -diff >= thr) out.push({ lat: g.lat0 - r * g.step, lng: g.lon0 + c * g.step, type: 'L', val: p });
    }
    return out;
  }
  function prepGrid(g) { g.hl = detectHL(g); return g; }

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
    var i0 = clamp(Math.floor(gy), 0, g.rows - 2), j0 = clamp(Math.floor(gx), 0, g.cols - 2);
    var fy = gy - i0, fx = gx - j0, C = g.cols, k = i0 * C + j0;
    var a = arr[k], b = arr[k + 1], c = arr[k + C], d = arr[k + C + 1];
    if (a == null || b == null || c == null || d == null) return null;
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy;
  }
  function valuesAt(lat, lng) {
    var g = gridFor(lat, lng);
    if (!g) return null;
    var gy = Math.round((g.lat0 - lat) / g.step), gx = Math.round((lng - g.lon0) / g.step);
    var wd = g.wd[gy * g.cols + gx];
    return {
      t: bil(g.t, g, lat, lng), pr: bil(g.pr, g, lat, lng), cl: bil(g.cl, g, lat, lng), p: bil(g.p, g, lat, lng),
      ws: bil(g.ws, g, lat, lng), h: bil(g.h, g, lat, lng), wd: wd,
      detail: g.region === 'kermanshah', updated: g.updated, stale: g.stale
    };
  }
  // مرزِ تصویرِ هر شبکه: هر نقطه‌ی داده در مرکزِ یک خانه است، پس نیم‌خانه بیرون‌تر
  function gridBounds(g) {
    return [
      [g.lat0 - (g.rows - 1) * g.step - g.step / 2, g.lon0 - g.step / 2],
      [g.lat0 + g.step / 2, g.lon0 + (g.cols - 1) * g.step + g.step / 2]
    ];
  }

  /* ─── لایه‌ی رنگی به‌صورت تصویر ───────────────────────────── */
  function fieldImage(g) {
    var F = FIELDS[field], lut = lutFor(field), arr = g[F.key];
    var small = document.createElement('canvas');
    small.width = g.cols; small.height = g.rows;
    var sc = small.getContext('2d');
    var img = sc.createImageData(g.cols, g.rows), d = img.data, n = 0;
    for (var k = 0; k < g.cols * g.rows; k++) {
      var v = arr[k], o = k * 4;
      if (v == null || (field === 'precip' && v < 0.05)) { d[o + 3] = 0; continue; }
      var idx = clamp(Math.round(norm(field, v) * 255), 0, 255);
      d[o] = lut[idx * 4]; d[o + 1] = lut[idx * 4 + 1]; d[o + 2] = lut[idx * 4 + 2];
      d[o + 3] = field === 'cloud' ? Math.round(40 + 150 * clamp(v / 100, 0, 1)) : lut[idx * 4 + 3];
      n++;
    }
    sc.putImageData(img, 0, 0);
    var SCALE = 40;
    var big = document.createElement('canvas');
    big.width = g.cols * SCALE; big.height = g.rows * SCALE;
    var bc = big.getContext('2d');
    bc.imageSmoothingEnabled = true;
    bc.drawImage(small, 0, 0, big.width, big.height);
    return { url: big.toDataURL('image/png'), painted: n };
  }

  function renderRegion(region) {
    var g = grids[region];
    var ov = overlays[region];
    if (!map) return 0;
    if (!g || field === 'none') {
      if (ov) { map.removeLayer(ov); overlays[region] = null; }
      return 0;
    }
    var img = fieldImage(g);
    if (!ov) {
      overlays[region] = Lf.imageOverlay(img.url, gridBounds(g), { interactive: false, opacity: 1 }).addTo(map);
    } else {
      ov.setUrl(img.url);
      ov.setBounds(gridBounds(g));
    }
    return img.painted;
  }

  function renderAll() {
    if (!map) return;
    var painted = { asia: renderRegion('asia'), kermanshah: renderRegion('kermanshah') };
    // جزئیات کرمانشاه همیشه روی نقشه‌ی آسیا باشد
    if (overlays.kermanshah) { var kk = overlays.kermanshah; map.removeLayer(kk); kk.addTo(map); }
    rebuildHL();
    updateLegend();
    setStatus('آسیا: ' + (grids.asia ? 'رسید' : 'نرسید') + ' · کرمانشاه: ' + (grids.kermanshah ? 'رسید' : 'نرسید') +
      ' · لایه: ' + field + ' · خانه‌های رنگی: ' + (painted.asia + painted.kermanshah));
  }

  /* ─── سامانه‌ها (H/L) و شهرها ───────────────────────────── */
  function rebuildHL() {
    if (!hlLayer) return;
    hlLayer.clearLayers();
    if (!showSys) return;
    var list = [];
    if (grids.asia) grids.asia.hl.forEach(function (x) {
      if (grids.kermanshah && inBox(grids.kermanshah, x.lat, x.lng)) return;
      list.push(x);
    });
    if (grids.kermanshah) list = list.concat(grids.kermanshah.hl);
    list.forEach(function (x) {
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
      h('small', { text: (v.detail ? 'جزئیات کرمانشاه' : 'شبکه‌ی آسیا') + ' · به‌روزرسانی ' + (v.updated || '').slice(11) + (v.stale ? ' · قدیمی' : '') }))];
    kids.push(h('div', { class: 'sy-i-g' }, items.map(function (x) {
      return h('div', { class: 'sy-i' }, h('small', { text: x[0] }), h('b', { dir: 'ltr', text: x[1] }));
    })));
    info.replaceChildren.apply(info, kids);
  }
  function updateSarpolInfo() {
    if (!info || info.dataset.sticky) return;
    showInfoAt(34.4597, 45.8646, 'سرپل‌ذهاب');
  }

  /* ─── وضعیت و راهنما ─────────────────────────────────── */
  function setStatus(t) { if (statusEl) statusEl.textContent = t; }
  function updateLegend() {
    var f = FIELDS[field];
    if (!legTitle) return;
    if (field === 'none') {
      legTitle.textContent = 'بدون لایه‌ی رنگی';
      legBar.style.background = 'transparent';
      legLo.textContent = ''; legHi.textContent = '';
      return;
    }
    legTitle.textContent = f.title;
    legBar.style.background = 'linear-gradient(90deg,' + f.stops.map(function (s) { return s[1] + ' ' + Math.round(s[0] * 100) + '%'; }).join(',') + ')';
    legLo.textContent = f.lo + f.unit; legHi.textContent = f.hi + f.unit;
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
    if (map.getZoom() >= 7) mapEl.classList.add('z7'); else mapEl.classList.remove('z7');
  }
  function initMap() {
    map = Lf.map(mapEl, {
      zoomControl: false, attributionControl: true, minZoom: 2, maxZoom: 9,
      fadeAnimation: false, zoomAnimation: false, markerZoomAnimation: false, worldCopyJump: false
    }).setView([36, 75], 3);
    Lf.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      subdomains: 'abc', maxZoom: 9, updateWhenIdle: true, keepBuffer: 1, attribution: '© OpenStreetMap'
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
    });
    map.on('click', function (e) { info.dataset.sticky = '1'; showInfoAt(e.latlng.lat, e.latlng.lng, null); });
    map.on('zoomend', applyZoomClass);
    applyZoomClass();
  }

  /* ─── داده‌ی شبکه‌ها ───────────────────────────────────── */
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
      setStatus('خطا در دریافت شبکه‌ی ' + region);
    }).then(function () { fetching = false; });
  }
  function refreshAll(force) {
    if (fetching) return;
    if (!force && Date.now() - loadedAt < 900000 && grids.asia) return;
    loadedAt = Date.now();
    var wantDetail = !!grids.kermanshah || (map && map.getZoom() >= 6);
    loadGrid('asia', function () { renderAll(); updateSarpolInfo(); });
    if (wantDetail) loadGrid('kermanshah', function () { renderAll(); updateSarpolInfo(); });
  }
  function fromCache() {
    ['asia', 'kermanshah'].forEach(function (r) {
      var c = LS.get('hub:map:' + r);
      if (c && c.d && c.d.rows && !grids[r]) grids[r] = prepGrid(c.d);
    });
  }

  /* ─── کنترل‌ها ─────────────────────────────────────────── */
  function setField(name) {
    field = name;
    Object.keys(chipBtns).forEach(function (k) { if (k !== 'sys') chipBtns[k].classList.toggle('on', k === name); });
    if (map) renderAll();
  }
  function setOverlay(on) {
    showSys = on;
    chipBtns.sys.classList.toggle('on', on);
    rebuildHL();
  }
  function setRegion(r) {
    Object.keys(segBtns).forEach(function (k) { segBtns[k].classList.toggle('on', k === r); });
    if (!map) return;
    if (r === 'kermanshah') map.setView([34.6, 46.4], 8, { animate: false });
    else map.setView([36, 75], 3, { animate: false });
    refreshAll(false);
  }

  function build() {
    mapEl = h('div', { class: 'sy-map', 'aria-label': 'نقشه‌ی سامانه‌های جوّی' });
    info = h('div', { class: 'sy-info' });
    statusEl = h('div', { class: 'sy-status' });
    legBar = h('i', { class: 'sy-legbar' });
    legLo = h('small', { class: 'sy-lo', dir: 'ltr' });
    legHi = h('small', { class: 'sy-hi', dir: 'ltr' });
    legTitle = h('b', { text: 'دما' });

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

    var sBtn = function (r, t) {
      var b = h('button', { type: 'button', class: 'sy-seg-b' + (r === 'asia' ? ' on' : ''),
        onclick: function () { hx.sel && hx.sel(); setRegion(r); } }, h('span', { text: t }));
      segBtns[r] = b;
      return b;
    };
    return h('div', { class: 'sy' },
      h('div', { class: 'sy-seg' }, sBtn('asia', 'آسیا'), sBtn('kermanshah', 'کرمانشاه و سرپل‌ذهاب')),
      h('div', { class: 'sy-chips' }, chipEls, sysChip),
      mapEl,
      statusEl,
      h('div', { class: 'sy-leg' }, legTitle, h('div', { class: 'sy-legrow' }, legLo, legBar, legHi)),
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
      refreshAll(false);
    });
  }
  function onHide() { shown = false; }

  window.HubSystems = { mount: mount, onShow: onShow, onHide: onHide };
})();
