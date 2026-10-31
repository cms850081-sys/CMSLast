/* رهگشا — چتِ واقعیِ دستیارِ هوشمندِ CMS داخلِ «پنل من» (ماژولِ تنبل: فقط با زدنِ دکمه‌ی رهگشا بار می‌شود)
   • همه‌چیز واقعی است و به بک‌اند (hub_chat.py → /hub/api/rg/*) وصل است: همان مغزِ رهگشای ربات تلگرام،
     همان ابزارها و قفل‌ها، و همان تاریخچه/تنظیمات/حافظه‌ی مشترک با تلگرام (فقط محیط فرق می‌کند).
   • نقشِ کاربر را سرور از روی initData امضاشده تشخیص می‌دهد؛ مرورگر هیچ‌چیز را «اعلام» نمی‌کند.
   • نگه‌داشتنِ جزیره (~۰٫۴۲ ثانیه) → تنظیمات مثل اپ از دلِ جزیره باز می‌شود.
   • همه‌ی رشته‌های کاربر/مدل با textContent نمایش داده می‌شوند (innerHTML فقط برای آیکون‌های ثابت). */
(function () {
  'use strict';
  var doc = document;
  var ctx = null, root = null, app = null, feed = null, inp = null, goBtn = null, isl = null, sp = null, dr = null, listEl = null;
  var built = false, isOpen = false, spOpen = false, busy = false, ir = null, tT = 0, loadSeq = 0;

  /* وضعیتِ زنده‌ی چت و تنظیمات (همه از سرور پر می‌شود) */
  var ST = { me: { role: '', role_label: '', is_pishva: false }, access: { usable: true, message: '' },
             sid: null, lastId: 0, P: {}, EFF: {}, MEM: [], ADM: null, admLoading: false, sessions: [] };

  function el(tag, cls, txt) { var n = doc.createElement(tag); if (cls) n.className = cls; if (txt != null) n.textContent = txt; return n; }
  function $(s, r) { return (r || root).querySelector(s); }
  function logo(w, charge) {
    var b = el('span', 'rg-bw' + (charge ? ' rg-charge' : '')); b.style.width = w + 'px';
    b.append(el('span', 'rg-bolt')); return b;
  }
  var fast = !!(window.matchMedia && matchMedia('(prefers-reduced-motion:reduce)').matches);

  /* ── شبکه ── */
  function call(path, body) {
    if (!ctx || !ctx.api) return Promise.reject(new Error('no-api'));
    return ctx.api(path, body);
  }
  function errMsg(e, dflt) {
    if (e && e.data && e.data.message) return e.data.message;
    if (e && e.code === 401) return 'نشست منقضی شده؛ پنل را ببند و دوباره باز کن.';
    return dflt || 'ارتباط با سرور برقرار نشد؛ دوباره امتحان کن.';
  }

  /* ── تعریفِ گزینه‌ها: عیناً از ai_persona.py ── */
  var MODES=[['normal','🙂','نرمال','رفتار پیش‌فرض و متعادل؛ همون سبک همیشگی.'],['serious','🧐','جدی','جدی، رسمی‌تر و دقیق؛ بدون شوخی و حاشیه.'],['warm','🤗','گرم و صمیمی','خودمونی مثل یه دوست نزدیک؛ با انرژی و محبت.'],['analyst','📈','تحلیلگر','عدد، دلیل، مقایسه و نتیجه‌گیری؛ ساختارمند.'],['harsh','🔥','تند و خشن','رک و بی‌تعارف و کوتاه؛ فقط در لحن، نه در درستیِ اطلاعات.'],['kind','💗','مهربان','مهربان، صبور و دلگرم‌کننده؛ بدون تعارفِ دروغ.'],['brief','✂️','خلاصه‌گو','فوق‌العاده خلاصه: اول نتیجه، بدون مقدمه.']];
  var ML=MODES.map(function(m){return[m[0],m[1]+' '+m[2]]}),OFFML=[['off','غیرفعال']].concat(ML),OO=[['1','روشن'],['0','خاموش']];
  var OPT={
   mode:['🎭','حالت رفتار',ML],
   emoji:['😊','اموجی',[['none','بدون'],['low','کم'],['normal','معمولی'],['high','زیاد']]],
   humor:['😄','شوخ‌طبعی',[['off','خاموش'],['low','کم'],['high','زیاد']]],
   profanity:['🗯️','فحش رفیقانه',[['off','خاموش'],['auto','مثل خودت (پیش‌فرض)']]],
   formality:['🗣️','لحن',[['casual','محاوره‌ای'],['semi','نیمه‌رسمی'],['formal','رسمی']]],
   length:['📏','طول جواب',[['short','کوتاه'],['medium','متوسط'],['long','مفصل']]],
   structure:['🧱','ساختار',[['text','متن ساده'],['list','لیست'],['table','عددمحور/جدولی']]],
   result_first:['🎯','اول نتیجه',[['1','اول نتیجه'],['0','اول توضیح']]],
   ask_or_guess:['❓','اطلاعات ناقص',[['ask','بپرس'],['guess','با فرضِ معقول جلو برو']]],
   confirm:['🛡️','تأیید قبل از اقدام',[['never','هیچ‌وقت'],['important','فقط کارهای مهم'],['always','همیشه']]],
   proactive:['💡','پیشنهاد فعال',OO],auto_remind:['🔔','یادآوری کارهای عقب‌افتاده',OO],memory:['🧠','حافظه‌ی شخصی',OO],
   voice:['🔊','خروجی صوتی',[['0','خاموش'],['1','روشن']]],trainer:['🎓','حالت مربی',[['0','خاموش'],['1','روشن']]],
   night_mode:['🌙','حالت شب (۲۲ تا ۶)',OFFML],work_mode:['🏢','حالت ساعت کاری',OFFML],
   deliver:['📥','ساعات دریافت پیام‌های رهگشا',[['all','همیشه'],['8-22','۸ تا ۲۲'],['9-21','۹ تا ۲۱'],['10-23','۱۰ تا ۲۳']]],
   pro_on:['📣','پیام‌دادنِ خودکارِ رهگشا',[['1','روشن'],['0','خاموش']]],
   pro_quiet:['🌙','ساعتِ سکوتِ پیام‌های خودکار',[['23:45-09:40','۲۳:۴۵ تا ۰۹:۴۰ (پیش‌فرض)'],['22:00-08:00','۲۲ تا ۸'],['23:00-07:00','۲۳ تا ۷'],['00:00-10:00','۰ تا ۱۰'],['off','بدون ساعت سکوت']]]
  };
  var SEC={style:['🎨','سبک گفتار',['emoji','humor','profanity','formality']],format:['📏','قالب جواب',['length','structure','result_first']],behavior:['⚙️','رفتار',['ask_or_guess','confirm','proactive','pro_on','auto_remind','memory','trainer','voice']],time:['🕒','زمان و دریافت',['night_mode','work_mode','deliver','pro_quiet']]};
  var TXT={ai_name:['✍️','اسم دلخواه برای رهگشا',30,'اسمی که می‌خوای رهگشا با اون خودش رو معرفی کنه (مثلاً «یار»). «-» = حذف.'],user_nick:['🏷️','لقبِ تو',30,'رهگشا تو رو چی صدا بزنه؟ (مثلاً «داداش»، «رئیس»). «-» = حذف.'],dialect:['🗺️','گویش / سبک خاص',40,'مثلاً «کرمانشاهی» یا «ادبی». «-» = حذف.'],report_template:['🧾','قالب گزارش',300,'گزارش‌های مسابقه رو چه ترتیب و فیلدی بخوای؟ مثال: «اول جدول، بعد بازی‌های در انتظار، آخر هشدارها». «-» = حذف.'],persona_text:['📝','پرامپت اختصاصی',500,'هر چیزی درباره‌ی لحن و سبکِ دلخواهت. فقط روی سبک اثر می‌ذاره و قوانین سیستم رو تغییر نمی‌ده. «-» = حذف.'],shortcut_add:['⚡','میانبر جدید',400,'با این قالب بنویس: «کلمه = دستور کامل». مثال: «صبحانه = خلاصه صبحگاهی بده و وضعیت مسابقات رو بگو»'],profile_save:['💾','ذخیره‌ی پروفایل',20,'یه اسم برای این ترکیب تنظیمات بنویس (مثلاً «مسابقه»).']};
  var HINTS={pro_on:'اگه روشن باشه رهگشا فقط سرِ موضوع‌های واقعاً مهم خودش بهت پیام می‌ده (حداکثر چند پیام در روز).',pro_quiet:'در این بازه رهگشا خودش پیام نمی‌ده. ساعتِ دلخواه (مثل ۲۲:۳۰ تا ۸) رو توی چت به خودِ رهگشا بگو.',confirm:'«فقط کارهای مهم» یعنی اخطار/اخراج/حذف/اطلاعیه/تغییر تنظیمات و … قبل از اجرا از تو تأیید می‌خواد.',deliver:'یادآورها و نتیجه‌ی اقدام‌های زمان‌بندی‌شده خارج از این ساعت‌ها نگه داشته می‌شن و اول بازه‌ی بعدی میان.',voice:'(آزمایشی) نسخه‌ی صوتیِ جواب‌های کوتاه به‌صورت فایل صوتی فرستاده می‌شه.'};

  /* ── وضعیتِ تنظیمات (از سرور) ── */
  var stack = [], body = null, titleEl = null, backBtn = null, toastEl = null;

  function applyS(r) {
    if (!r) return;
    if (r.prefs) ST.P = r.prefs;
    if (r.effective) ST.EFF = r.effective;
    if (r.memory) ST.MEM = r.memory;
  }
  function mode() { return ST.P.mode || 'normal'; }
  function modeObj() { return MODES.filter(function (m) { return m[0] === mode(); })[0] || MODES[0]; }
  function lab(k, v) { var l = OPT[k][2].filter(function (x) { return x[0] === v; })[0]; return l ? l[1] : String(v); }
  function val(k) { return ST.P[k] != null ? lab(k, ST.P[k]) : 'پیش‌فرض'; }
  function eff(k) { return ST.EFF[k] != null ? ST.EFF[k] : (ST.P[k] != null ? ST.P[k] : ''); }
  function toast(t) {
    if (!toastEl) return;
    toastEl.textContent = t; toastEl.classList.add('rg-on');
    clearTimeout(tT); tT = setTimeout(function () { toastEl.classList.remove('rg-on'); }, 2200);
    try { ctx.hx.sel(); } catch (e) {}
  }
  /* تغییرِ تنظیم روی سرور → وضعیتِ تازه → رندرِ دوباره */
  function mut(path, bodyObj, okMsg) {
    return call(path, bodyObj).then(function (r) { applyS(r); if (okMsg) toast(okMsg); refresh(); return r; },
      function (e) { toast(errMsg(e)); throw e; });
  }
  /* صفحه‌ای که دیتایش را از سرور می‌گیرد */
  function lazy(loader, build) {
    var box = el('div'); box.append(el('div', 'rg-note rg-ctr', 'در حال بارگذاری…'));
    loader().then(function (r) { box.replaceChildren(build(r)); },
      function (e) { box.replaceChildren(el('div', 'rg-note rg-ctr', '⚠️ ' + errMsg(e))); });
    return box;
  }
  function dlg(title, text, btns) {
    var d = el('div', 'rg-dlg'), w = el('div'), p1 = el('p'), p2 = el('p');
    p1.append(el('b', null, title)); p2.textContent = text || ''; p2.style.cssText = 'color:var(--hint);font-size:13px';
    w.append(p1, p2);
    btns.forEach(function (b) {
      var x = el('button', b[2] || 'rg-b2', b[0]); x.type = 'button';
      x.onclick = function () { d.remove(); if (b[1]) b[1](); }; w.append(x);
    });
    d.append(w); sp.append(d);
  }

  /* ── اجزای تنظیمات ── */
  var CH = '<svg class="rg-ch" viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7"/></svg>';
  var BACK = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg>';
  var CLOSE = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';

  function row(o) {
    var b = el('button', 'rg-rw'); b.type = 'button';
    if (o.ic) b.append(el('span', 'rg-ri', o.ic));
    var l = el('span', 'rg-rl' + (o.dg ? ' rg-dg' : ''));
    l.append(doc.createTextNode(o.l));
    if (o.s) l.append(el('small', null, o.s));
    b.append(l);
    if (o.v != null) b.append(el('span', 'rg-rv', o.v));
    if (o.sw != null) b.append(el('span', 'rg-sw' + (o.sw ? ' rg-on' : '')));
    if (o.ck) b.append(el('span', 'rg-ck', '✓'));
    if (o.go) b.insertAdjacentHTML('beforeend', CH);
    if (o.fn) b.onclick = o.fn;
    return b;
  }
  function grp(f, t, rows) { if (t) f.append(el('div', 'rg-gt', t)); var g = el('div', 'rg-grp'); rows.forEach(function (r) { g.append(r); }); f.append(g); }
  function go(t, fn) { stack.push({ t: t, f: fn }); render(1); }
  function spBack() { if (stack.length > 1) { stack.pop(); render(-1); } else closeSp(); }
  function render(dir) {
    var cur = stack[stack.length - 1], pg = el('div', 'rg-pg'); pg.append(cur.f());
    titleEl.textContent = cur.t; backBtn.innerHTML = stack.length > 1 ? BACK : CLOSE;
    var old = body.lastElementChild; body.append(pg);
    if (dir && old && !fast && pg.animate) {
      var d = dir > 0 ? -1 : 1;
      pg.animate([{ transform: 'translateX(' + d * 35 + '%)', opacity: 0 }, { transform: 'none', opacity: 1 }], { duration: 320, easing: 'cubic-bezier(.2,.9,.3,1)' });
      var a = old.animate([{ opacity: 1 }, { opacity: 0, transform: 'translateX(' + (-d * 18) + '%)' }], { duration: 240, fill: 'forwards' });
      a.onfinish = function () { old.remove(); };
    } else if (old) old.remove();
  }
  function refresh() {
    var cur = stack[stack.length - 1], old = body.lastElementChild, st = old ? old.scrollTop : 0, pg = el('div', 'rg-pg');
    pg.append(cur.f()); body.replaceChild(pg, old); pg.scrollTop = st;
  }
  function info(f, items, fn) {
    var rows = items.map(function (it, i) { return row({ ic: it[0], l: it[1], s: it[2], v: fn ? 'لغو' : null, fn: fn ? function () { fn(i); } : null }); });
    grp(f, null, rows);
  }

  /* ── صفحه‌ها ── */
  function pgHome() {
    var f = doc.createDocumentFragment(), m = modeObj(), isP = ST.me.is_pishva;
    var hc = el('div', 'rg-hc'), tx = el('div');
    var cps = el('div', 'rg-cps');
    var chips = [lab('length', eff('length') || 'medium'), 'اموجی: ' + lab('emoji', eff('emoji') || 'normal'), 'تأیید: ' + lab('confirm', eff('confirm') || 'never')];
    if (ST.me.role_label) chips.unshift(ST.me.role_label);
    chips.forEach(function (t) { cps.append(el('span', 'rg-cp', t)); });
    tx.append(el('b', null, m[1] + ' ' + m[2]), cps);
    hc.append(logo(46), tx); f.append(hc);
    var sc = Object.keys(ST.P.shortcuts || {}).length, pr = Object.keys(ST.P.profiles || {}).length;
    grp(f, 'شخصی‌سازی', [
      row({ ic: '🎭', l: 'حالت رفتار', v: m[1] + ' ' + m[2], go: 1, fn: function () { go('حالت رفتار', pgMode); } }),
      row({ ic: SEC.style[0], l: SEC.style[1], go: 1, fn: function () { go(SEC.style[1], function () { return pgSec('style'); }); } }),
      row({ ic: SEC.format[0], l: SEC.format[1], go: 1, fn: function () { go(SEC.format[1], function () { return pgSec('format'); }); } }),
      row({ ic: SEC.behavior[0], l: SEC.behavior[1], go: 1, fn: function () { go(SEC.behavior[1], function () { return pgSec('behavior'); }); } }),
      row({ ic: SEC.time[0], l: SEC.time[1], go: 1, fn: function () { go(SEC.time[1], function () { return pgSec('time'); }); } }),
      row({ ic: '✍️', l: 'اسم‌ها و گویش', go: 1, fn: function () { go('اسم‌ها و گویش', pgNames); } })]);
    grp(f, 'حافظه و میانبر', [
      row({ ic: '🧠', l: 'حافظه‌ی من', v: ST.P.memory === '0' ? 'خاموش' : 'روشن', go: 1, fn: function () { go('حافظه‌ی من', pgMem); } }),
      row({ ic: '⚡', l: 'میانبرها', v: String(sc), go: 1, fn: function () { go('میانبرها', pgSc); } }),
      row({ ic: '🧾', l: 'قالب گزارش', v: ST.P.report_template ? 'تنظیم شده' : '—', go: 1, fn: function () { go(TXT.report_template[1], function () { return pgTxt('report_template'); }); } }),
      row({ ic: '📝', l: 'پرامپت اختصاصی', v: ST.P.persona_text ? 'تنظیم شده' : '—', go: 1, fn: function () { go(TXT.persona_text[1], function () { return pgTxt('persona_text'); }); } }),
      row({ ic: '💾', l: 'پروفایل‌ها', v: String(pr), go: 1, fn: function () { go('پروفایل‌ها', pgProf); } })]);
    grp(f, 'ابزارها', [
      row({ ic: '🤖', l: 'کارهای زمان‌بندی‌شده', go: 1, fn: function () { go('کارهای زمان‌بندی‌شده', pgSch); } }),
      row({ ic: '🔔', l: 'قانون‌های «اگر فلان شد بهم بگو»', go: 1, fn: function () { go('قانون‌های من', pgRules); } }),
      row({ ic: '🛠️', l: 'توانایی‌های من', go: 1, fn: function () { go('توانایی‌های من', pgAbil); } }),
      row({ ic: '❓', l: 'چرا این جواب؟', go: 1, fn: function () { go('چرا این جواب؟', pgWhy); } }),
      row({ ic: '👁', l: 'پیش‌نمایش سبک', go: 1, fn: function () { go('پیش‌نمایش سبک', pgPrev); } }),
      row({ ic: '📖', l: 'راهنمای رهگشا', go: 1, fn: function () { go('راهنمای رهگشا', pgHelp); } })]);
    if (isP) {
      if (!ST.ADM && !ST.admLoading) {
        ST.admLoading = true;
        call('/hub/api/rg/admin/overview').then(function (r) { ST.ADM = r; ST.admLoading = false; if (spOpen && stack.length === 1) refresh(); },
          function () { ST.admLoading = false; });
      }
      var A = ST.ADM || { online: true, lock: true };
      grp(f, '👑 مدیریت دستیار (فقط مدیر ارشد)', [
        row({ ic: '🔌', l: 'هوش مصنوعی', s: 'خاموش‌کردن یعنی رهگشا برای همه قطع می‌شه', sw: !!A.online, fn: function () {
          if (!ST.ADM) return;
          call('/hub/api/rg/admin/online', {}).then(function (r) { ST.ADM.online = r.online; if (r.access) ST.access = r.access; applyAccess(); refresh(); }, function (e) { toast(errMsg(e)); });
        } }),
        row({ ic: '🔐', l: 'قفل دستور مدیر ارشد', s: 'اجرای اقدام‌های حساس فقط با دستور شما', sw: !!A.lock, fn: function () {
          if (!ST.ADM) return;
          call('/hub/api/rg/admin/lock', {}).then(function (r) { ST.ADM.lock = r.lock; refresh(); }, function (e) { toast(errMsg(e)); });
        } }),
        row({ ic: '📣', l: 'پیام‌دادنِ خودکارِ رهگشا', go: 1, fn: function () { go('پیام‌دادنِ خودکار', pgPro); } }),
        row({ ic: '🛠️', l: 'اختیارات دستیار', go: 1, fn: function () { go('اختیارات دستیار', pgPerm); } }),
        row({ ic: '🗂️', l: 'سوابق چت مدیران', go: 1, fn: function () { go('سوابق چت مدیران', pgLog); } }),
        row({ ic: '🔕', l: 'خاموشی برای ادمین', go: 1, fn: function () { go('خاموشی برای ادمین', pgOff); } }),
        row({ ic: '🎭', l: 'تنظیمات رهگشای مدیران', go: 1, fn: function () { go('تنظیمات رهگشای مدیران', pgAdm); } }),
        row({ ic: '🛰️', l: 'نمای لحظه‌ای', go: 1, fn: function () { go('نمای لحظه‌ای', pgLive); } }),
        row({ ic: '📜', l: 'لاگ مدیران', go: 1, fn: function () { go('لاگ مدیران', pgTrail); } }),
        row({ ic: '📒', l: 'یادداشت‌های مدیریتی', go: 1, fn: function () { go('یادداشت‌های مدیریتی', pgNotes); } })]);
    }
    grp(f, null, [row({ ic: '♻️', l: 'بازنشانی تنظیمات', dg: 1, fn: askReset })]);
    f.append(el('div', 'rg-note', isP ? '👁️ شما به‌عنوان مدیر ارشد به چت مدیران با رهگشا دسترسی دارید.' : '👁️ یادآوری شفافیت: چت‌های شما با رهگشا برای مدیر ارشد قابل مشاهده است.'));
    return f;
  }
  function pgMode() {
    var f = doc.createDocumentFragment();
    grp(f, null, MODES.map(function (m) { return row({ ic: m[1], l: m[2], s: m[3], ck: mode() === m[0], fn: function () { mut('/hub/api/rg/pref', { key: 'mode', value: m[0] }, 'حالت «' + m[2] + '» فعال شد'); } }); }));
    grp(f, null, [row({ ic: '↩️', l: 'پیروی از پیش‌فرض', fn: function () { mut('/hub/api/rg/pref', { key: 'mode', value: 'def' }); } })]);
    return f;
  }
  function pgSec(s) {
    var f = doc.createDocumentFragment();
    grp(f, null, SEC[s][2].map(function (k) { return row({ ic: OPT[k][0], l: OPT[k][1], v: val(k), go: 1, fn: function () { go(OPT[k][1], function () { return pgOpt(k); }); } }); }));
    return f;
  }
  function pgOpt(k) {
    var f = doc.createDocumentFragment();
    grp(f, null, OPT[k][2].map(function (c) { return row({ l: c[1], ck: ST.P[k] === c[0], fn: function () { mut('/hub/api/rg/pref', { key: k, value: c[0] }, 'ذخیره شد'); } }); }));
    if (k !== 'memory') grp(f, null, [row({ ic: '↩️', l: 'پیروی از حالت / پیش‌فرض', fn: function () { mut('/hub/api/rg/pref', { key: k, value: 'def' }); } })]);
    if (HINTS[k]) f.append(el('div', 'rg-hint', HINTS[k]));
    return f;
  }
  function pgNames() {
    var f = doc.createDocumentFragment();
    grp(f, null, ['ai_name', 'user_nick', 'dialect'].map(function (k) { return row({ ic: TXT[k][0], l: TXT[k][1], v: ST.P[k] || '—', go: 1, fn: function () { go(TXT[k][1], function () { return pgTxt(k); }); } }); }));
    return f;
  }
  function pgTxt(k) {
    var f = doc.createDocumentFragment(), T = TXT[k], cur = (k === 'shortcut_add' || k === 'profile_save') ? '' : (ST.P[k] || '');
    var ta = el('textarea', 'rg-ta'); ta.maxLength = T[2]; ta.value = cur; ta.placeholder = T[1]; if (T[2] <= 40) ta.style.minHeight = '56px';
    var c = el('div', 'rg-cnt', cur.length + ' / ' + T[2]); ta.oninput = function () { c.textContent = ta.value.length + ' / ' + T[2]; };
    f.append(ta, c, el('div', 'rg-hint', T[3]));
    var s = el('button', 'rg-b1', 'ذخیره'); s.type = 'button';
    s.onclick = function () {
      var v = ta.value.trim().replace(/\s+/g, ' '); if (!v) return;
      var p;
      if (k === 'shortcut_add') {
        var i = v.indexOf('='); if (i < 1 || !v.slice(i + 1).trim()) { toast('قالب: کلمه = دستور'); return; }
        p = call('/hub/api/rg/shortcut', { act: 'add', trig: v.slice(0, i).trim(), cmd: v.slice(i + 1).trim() });
      } else if (k === 'profile_save') p = call('/hub/api/rg/profile', { act: 'save', name: v });
      else p = call('/hub/api/rg/text', { key: k, value: v });
      s.disabled = true;
      p.then(function (r) { applyS(r); toast('ذخیره شد'); stack.pop(); render(-1); },
        function (e) { s.disabled = false; toast(errMsg(e)); });
    };
    f.append(s);
    if (cur) {
      var d = el('button', 'rg-b2', 'حذف'); d.type = 'button';
      d.onclick = function () {
        call('/hub/api/rg/text', { key: k, value: '-' }).then(function (r) { applyS(r); toast('حذف شد'); stack.pop(); render(-1); }, function (e) { toast(errMsg(e)); });
      };
      f.append(d);
    }
    return f;
  }
  function pgMem() {
    var f = doc.createDocumentFragment();
    grp(f, null, [row({ ic: '🧠', l: 'حافظه‌ی شخصی', sw: ST.P.memory !== '0', fn: function () { mut('/hub/api/rg/mem', { act: 'toggle' }); } })]);
    if (ST.MEM.length) grp(f, 'یادداشت‌ها', ST.MEM.map(function (m) { return row({ l: '#' + m.id + ' ' + m.content, v: '🗑', fn: function () { mut('/hub/api/rg/mem', { act: 'del', id: m.id }, 'حذف شد'); } }); }));
    else f.append(el('div', 'rg-note', 'چیزی ذخیره نشده.'));
    if (ST.MEM.length) grp(f, null, [row({ ic: '🧹', l: 'پاک‌کردن همه', dg: 1, fn: function () { mut('/hub/api/rg/mem', { act: 'clear' }); } })]);
    if (ST.me.is_pishva) grp(f, null, [row({ ic: '📒', l: 'یادداشت‌های مدیریتیِ مدیر ارشد', go: 1, fn: function () { go('یادداشت‌های مدیریتی', pgNotes); } })]);
    return f;
  }
  function pgSc() {
    var f = doc.createDocumentFragment(), sc = ST.P.shortcuts || {}, ks = Object.keys(sc);
    f.append(el('div', 'rg-note', 'یه کلمه‌ی کوتاه که به‌جای یه دستور بلند به رهگشا می‌گی.'));
    if (ks.length) grp(f, null, ks.map(function (k) { return row({ l: '«' + k + '»', s: sc[k], v: '🗑', fn: function () { mut('/hub/api/rg/shortcut', { act: 'del', trig: k }, 'حذف شد'); } }); }));
    else f.append(el('div', 'rg-note', 'میانبری نداری.'));
    if (ks.length < 15) grp(f, null, [row({ ic: '➕', l: 'میانبر جدید', go: 1, fn: function () { go('میانبر جدید', function () { return pgTxt('shortcut_add'); }); } })]);
    return f;
  }
  function pgProf() {
    var f = doc.createDocumentFragment(), pr = ST.P.profiles || {}, ks = Object.keys(pr);
    f.append(el('div', 'rg-note', 'ترکیب‌های ذخیره‌شده؛ با یک دکمه عوض می‌شن.'));
    if (ks.length) grp(f, null, ks.map(function (k) {
      return row({ ic: '▶️', l: k, v: 'اعمال / حذف', fn: function () {
        dlg('پروفایل «' + k + '»', 'می‌خوای این ترکیب تنظیمات اعمال بشه یا پروفایل حذف بشه؟', [
          ['اعمال', function () { mut('/hub/api/rg/profile', { act: 'apply', name: k }, 'اعمال شد'); }, 'rg-b1'],
          ['حذف', function () { mut('/hub/api/rg/profile', { act: 'del', name: k }, 'حذف شد'); }],
          ['انصراف', null]]);
      } });
    }));
    else f.append(el('div', 'rg-note', 'پروفایلی نداری.'));
    if (ks.length < 5) grp(f, null, [row({ ic: '➕', l: 'ذخیره‌ی تنظیمات فعلی', go: 1, fn: function () { go('ذخیره‌ی پروفایل', function () { return pgTxt('profile_save'); }); } })]);
    return f;
  }
  function pgSch() {
    return lazy(function () { return call('/hub/api/rg/sched'); }, function (r) {
      var f = doc.createDocumentFragment(), jobs = r.jobs || [];
      if (!jobs.length) { f.append(el('div', 'rg-note', '📭 چیزی زمان‌بندی نشده')); return f; }
      grp(f, r.all_admins ? 'همه‌ی مدیران' : 'فقط مال خودت', jobs.map(function (j) {
        return row({ l: j.label, v: 'لغو', fn: function () {
          dlg('لغو این کار؟', j.label, [['لغو کار', function () {
            call('/hub/api/rg/sched/cancel', { id: j.id }).then(function (x) { toast(x.message || 'انجام شد'); refresh(); }, function (e) { toast(errMsg(e)); });
          }, 'rg-b1'], ['انصراف', null]]);
        } });
      }));
      return f;
    });
  }
  function pgAbil() {
    return lazy(function () { return call('/hub/api/rg/abilities'); }, function (r) {
      var f = doc.createDocumentFragment();
      grp(f, null, (r.items || []).map(function (c) {
        return row({ ic: c.on ? '✅' : '❌', l: c.label, s: c.count + ' ابزار' + (c.on ? '' : ' — توسط مدیر ارشد خاموش شده') });
      }));
      f.append(el('div', 'rg-note', 'سقف دسترسی هر نقش همیشه روی تنظیمات سبک اولویت داره.'));
      return f;
    });
  }
  function pgWhy() {
    return lazy(function () { return call('/hub/api/rg/why'); }, function (r) {
      var f = doc.createDocumentFragment(), w = r.why;
      if (!w) { f.append(el('div', 'rg-note', 'اطلاعاتی برای این جواب نیست.')); return f; }
      var items = [['🎭', 'حالت رفتار', w.mode], ['🧠', 'یادداشت‌های شخصیِ استفاده‌شده', String(w.mem)]];
      if (w.tools && w.tools.length) w.tools.forEach(function (t) { items.push(['🔧', t.name, t.result]); });
      else items.push(['🛠', 'ابزار', 'ابزاری صدا زده نشد؛ جواب مستقیم از خود مدل و پیام‌های همین گفتگو بود.']);
      info(f, items);
      return f;
    });
  }
  function pgHelp() {
    var f = doc.createDocumentFragment();
    info(f, [['💬', 'گفت‌وگو', 'عادی بنویس؛ هر کاری که نقشت اجازه می‌ده خودم انجام می‌دم.'],
      ['🔔', 'یادآوری و زمان‌بندی', '«یادم بنداز …» یا «فردا ساعت ۹ … رو انجام بده».'],
      ['🎭', 'تنظیم با چت', '«از این به بعد کوتاه بگو / اموجی نذار» لحنت رو همون لحظه تنظیم می‌کنه.'],
      ['⚡', 'میانبر', 'یه کلمه‌ی کوتاه که یه دستور بلند رو اجرا می‌کنه.'],
      ['❓', 'چرا این جواب؟', 'نشون می‌ده چه ابزارهایی صدا زده شد.'],
      ['🛡️', 'تأیید و قفل', 'اخطار، اخراج و حذف فقط با دستور مدیر ارشد انجام می‌شه.'],
      ['🔗', 'یک چت، دو محیط', 'گفتگوی هاب و گفتگوی ربات تلگرام یکی‌اند؛ هرجا ادامه بدی همون‌جا پیداست.'],
      ['👁️', 'شفافیت', 'چت‌های مدیران با رهگشا برای مدیر ارشد قابل مشاهده است.']]);
    return f;
  }
  function pgPrev() {
    return lazy(function () { return call('/hub/api/rg/preview', {}); }, function (r) {
      var f = doc.createDocumentFragment();
      f.append(el('div', 'rg-pvw', r.text || ''));
      f.append(el('div', 'rg-hint', 'یک جوابِ واقعی از رهگشا با تنظیمات فعلی.\nحالت: ' + modeObj()[2] + ' · ' + lab('length', eff('length') || 'medium') + ' · اموجی: ' + lab('emoji', eff('emoji') || 'normal')));
      return f;
    });
  }

  /* ── بخشِ مدیر ارشد ── */
  function admList(ovr, onTap, rowExtra) {
    var f = doc.createDocumentFragment(), a = ovr.admins || [];
    if (!a.length) { f.append(el('div', 'rg-note', 'هیچ مدیری ثبت نشده.')); return f; }
    grp(f, null, a.map(function (x) { return row(rowExtra(x, function () { onTap(x); })); }));
    return f;
  }
  function pgRules() {
    return lazy(function () { return call('/hub/api/rg/rules'); }, function (r) {
      var f = doc.createDocumentFragment(), rs = r.rules || [];
      f.append(el('div', 'rg-note', 'برای ساختنِ قانون توی چت به رهگشا بگو: «اگر … شد بهم بگو». این‌جا فقط می‌بینی یا پاک می‌کنی.'));
      if (!rs.length) { f.append(el('div', 'rg-note', 'قانونِ فعالی نداری.')); return f; }
      grp(f, null, rs.map(function (x) {
        return row({ ic: '🔔', l: x.title, s: x.what + (x.repeat ? ' — تکراری' : '') + (x.mine ? '' : ' — مالِ دیگری'), v: '🗑', fn: function () {
          call('/hub/api/rg/rules/del', { id: x.id }).then(function () { toast('حذف شد'); refresh(); }, function (e) { toast(errMsg(e)); });
        } });
      }));
      return f;
    });
  }
  function pgPro() {
    return lazy(function () { return call('/hub/api/rg/admin/pro'); }, function (s) {
      var f = doc.createDocumentFragment();
      function act(b) { call('/hub/api/rg/admin/pro', b).then(function () { refresh(); }, function (e) { toast(errMsg(e)); }); }
      f.append(el('div', 'rg-note', 'رهگشا فقط سرِ موضوع‌های مهم خودش پیام می‌ده؛ ساکت‌بودن پیش‌فرضه. هر مدیر برای خودش هم می‌تونه خاموش/روشن و ساعتِ سکوت رو عوض کنه.'));
      grp(f, null, [
        row({ ic: '📣', l: 'کلیدِ کلی', s: 'خاموش = رهگشا برای هیچ‌کس خودش پیام نمی‌ده', sw: s.on, fn: function () { act({ key: 'on' }); } }),
        row({ ic: '📅', l: 'سقفِ روزانه برای هر نفر', v: String(s.max_day), fn: function () { act({ key: 'max_day' }); } }),
        row({ ic: '⏱', l: 'فاصله‌ی حداقلیِ دو پیام', v: s.gap_h + ' ساعت', fn: function () { act({ key: 'gap_h' }); } }),
        row({ ic: '🎯', l: 'آستانه‌ی اهمیت (۱ تا ۵)', s: 'بیشتر = ساکت‌تر و گزیده‌تر', v: String(s.min_imp), fn: function () { act({ key: 'min_imp' }); } }),
        row({ ic: '🌙', l: 'ساعتِ سکوتِ پیش‌فرض', v: s.quiet === 'off' ? 'ندارد' : s.quiet, fn: function () { act({ key: 'quiet' }); } }),
        row({ ic: '🧠', l: 'قاضیِ هوش مصنوعی', s: 'مدل می‌تونه بگه «این ارزش مزاحمت نداره»', sw: s.llm, fn: function () { act({ key: 'llm' }); } })]);
      grp(f, null, [
        row({ ic: '🗂', l: 'کدام موضوع‌ها بررسی شوند', v: (s.topics.filter(function (t) { return t.on; }).length) + ' / ' + s.topics.length, go: 1, fn: function () { go('موضوع‌ها', pgProTopics); } }),
        row({ ic: '👥', l: 'کدام مدیرها پیام بگیرند', go: 1, fn: function () { go('افراد', pgProPeople); } })]);
      return f;
    });
  }
  function pgProTopics() {
    return lazy(function () { return call('/hub/api/rg/admin/pro'); }, function (s) {
      var f = doc.createDocumentFragment();
      grp(f, null, s.topics.map(function (t) {
        return row({ l: t.label, sw: t.on, fn: function () {
          call('/hub/api/rg/admin/pro', { topic: t.key }).then(function () { refresh(); }, function (e) { toast(errMsg(e)); });
        } });
      }));
      return f;
    });
  }
  function pgProPeople() {
    return lazy(function () { return call('/hub/api/rg/admin/pro'); }, function (s) {
      var f = doc.createDocumentFragment(), ps = s.people.filter(function (p) { return p.active; });
      f.append(el('div', 'rg-note', 'روشن = رهگشا می‌تونه برای این نفر پیام بده (خودش هم می‌تونه جدا خاموش کنه).'));
      grp(f, null, ps.map(function (p) {
        return row({ ic: '👤', l: p.name, sw: !p.blocked, fn: function () {
          call('/hub/api/rg/admin/pro', { uid: p.id }).then(function () { refresh(); }, function (e) { toast(errMsg(e)); });
        } });
      }));
      return f;
    });
  }
  function pgPerm() {
    return lazy(function () { return call('/hub/api/rg/admin/overview'); }, function (r) {
      var f = doc.createDocumentFragment();
      grp(f, null, (r.categories || []).map(function (c) {
        return row({ l: c.label, s: c.count + ' ابزار', sw: c.on, fn: function () {
          call('/hub/api/rg/admin/perm', { key: c.key }).then(function () { refresh(); }, function (e) { toast(errMsg(e)); });
        } });
      }));
      f.append(el('div', 'rg-note', 'خاموش‌کردن یک دسته، اجرای ابزارهای اون دسته رو واقعاً می‌بنده (برای همه، از جمله خودت).'));
      return f;
    });
  }
  function pgLog() {
    return lazy(function () { return call('/hub/api/rg/admin/overview'); }, function (r) {
      return admList(r, function (a) { go(a.name, function () { return pgLogOne(a); }); },
        function (a, fn) { return { ic: '👤', l: a.name, s: a.role_label, go: 1, fn: fn }; });
    });
  }
  function pgLogOne(a) {
    return lazy(function () { return call('/hub/api/rg/admin/logs?tid=' + encodeURIComponent(a.id)); }, function (r) {
      var f = doc.createDocumentFragment(), ss = r.sessions || [];
      if (!ss.length) { f.append(el('div', 'rg-note', 'این مدیر هنوز چتی با رهگشا نداشته.')); return f; }
      grp(f, null, ss.map(function (s) { return row({ ic: '💬', l: s.title, s: s.at, go: 1, fn: function () { go(s.title, function () { return pgSessView(s.id); }); } }); }));
      return f;
    });
  }
  function pgSessView(id) {
    return lazy(function () { return call('/hub/api/rg/admin/session/' + id); }, function (r) {
      var w = el('div', 'rg-sv'), ms = r.messages || [];
      if (!ms.length) return el('div', 'rg-note rg-ctr', 'پیامی نیست.');
      ms.forEach(function (m) { w.append(msgRow(m)); });
      return w;
    });
  }
  function pgOff() {
    return lazy(function () { return call('/hub/api/rg/admin/overview'); }, function (r) {
      return admList(r, null, function (a) {
        return { ic: '👤', l: a.name, s: a.ai_on ? 'رهگشا روشن' : 'رهگشا خاموش', sw: a.ai_on, fn: function () {
          call('/hub/api/rg/admin/admin-ai', { id: a.id, on: !a.ai_on }).then(function () { refresh(); }, function (e) { toast(errMsg(e)); });
        } };
      });
    });
  }
  function pgAdm() {
    return lazy(function () { return call('/hub/api/rg/admin/overview'); }, function (r) {
      return admList(r, function (a) { go(a.name, function () { return pgAdmOne(a); }); },
        function (a, fn) { return { ic: '👤', l: a.name, s: a.role_label, go: 1, fn: fn }; });
    });
  }
  function pgAdmOne(a) {
    return lazy(function () { return call('/hub/api/rg/admin/persona/' + encodeURIComponent(a.id)); }, function (r) {
      var f = doc.createDocumentFragment();
      f.append(el('div', 'rg-pvw', r.summary || 'پیش‌فرض'));
      grp(f, null, [row({ ic: '♻️', l: 'بازنشانی تنظیمات این مدیر', dg: 1, fn: function () {
        dlg('بازنشانی؟', 'تنظیمات رهگشای «' + a.name + '» به پیش‌فرض برمی‌گرده.', [['بازنشانی', function () {
          call('/hub/api/rg/admin/persona/' + encodeURIComponent(a.id) + '/reset', {}).then(function () { toast('بازنشانی شد'); refresh(); }, function (e) { toast(errMsg(e)); });
        }, 'rg-b1'], ['انصراف', null]]);
      } })]);
      return f;
    });
  }
  function pgText(path) {
    return lazy(function () { return call(path); }, function (r) { return el('div', 'rg-pvw', r.text || '—'); });
  }
  function pgLive() { return pgText('/hub/api/rg/admin/live'); }
  function pgTrail() { return pgText('/hub/api/rg/admin/trail'); }
  function pgNotes() {
    return lazy(function () { return call('/hub/api/rg/admin/notes'); }, function (r) {
      var f = doc.createDocumentFragment(), ns = r.notes || [];
      if (!ns.length) { f.append(el('div', 'rg-note', 'خالیه.')); return f; }
      grp(f, null, ns.map(function (n) {
        return row({ ic: '📒', l: '#' + n.id + ' ' + n.subject, s: n.content, v: '🗑', fn: function () {
          call('/hub/api/rg/admin/notes/del', { id: n.id }).then(function () { toast('حذف شد'); refresh(); }, function (e) { toast(errMsg(e)); });
        } });
      }));
      return f;
    });
  }
  function askReset() {
    dlg('بازنشانی همه‌ی تنظیمات؟', 'همه‌ی گزینه‌ها به پیش‌فرض برمی‌گردن (حافظه‌ی شخصی دست‌نخورده می‌مونه).', [
      ['بازنشانی', function () { mut('/hub/api/rg/reset', {}, 'بازنشانی شد'); }, 'rg-b1'], ['انصراف', null]]);
  }

  /* ── باز و بسته شدن تنظیمات: مثلِ اپ، از دلِ جزیره ── */
  function nosel() { try { var s = window.getSelection && window.getSelection(); if (s && s.removeAllRanges) s.removeAllRanges(); } catch (e) {} }
  function rect() {
    var a = app.getBoundingClientRect(), r = isl.getBoundingClientRect();
    return 'inset(' + (r.top - a.top) + 'px ' + (a.right - r.right) + 'px ' + (a.bottom - r.bottom) + 'px ' + (r.left - a.left) + 'px round 999px)';
  }
  function openSp() {
    if (spOpen) return; spOpen = true; nosel(); tipOff();
    try { ctx.LS.set('hub:rg:tip', 1); } catch (e) {}
    ir = rect(); stack = [{ t: 'تنظیمات', f: pgHome }];
    sp.replaceChildren();
    var inn = el('div', 'rg-sp-in'), st = el('div', 'rg-st'), sb = el('button', 'rg-ib'), tt = el('b'), sbody = el('div', 'rg-sbody');
    sb.type = 'button'; sb.setAttribute('aria-label', 'بازگشت'); st.append(sb, tt, el('i')); inn.append(st, sbody);
    toastEl = el('div', 'rg-tt'); sp.append(inn, toastEl);
    body = sbody; titleEl = tt; backBtn = sb; sb.onclick = spBack;
    sp.style.display = 'flex'; render(0); app.classList.add('rg-sopen');
    try { ctx.hx.ok(); } catch (e) {}
    if (sp.animate) {
      var bg = getComputedStyle(sp).backgroundColor, dur = fast ? 1 : 560;
      try {
        sp.animate([{ clipPath: ir, backgroundColor: '#050607' }, { clipPath: 'inset(0px 0px 0px 0px round 0px)', backgroundColor: bg }], { duration: dur, easing: 'cubic-bezier(.2,.85,.25,1)' });
        inn.animate([{ opacity: 0, transform: 'scale(.9)' }, { opacity: 1, transform: 'none' }], { duration: fast ? 1 : 380, delay: fast ? 0 : 170, easing: 'ease-out', fill: 'backwards' });
      } catch (e) {}
    }
  }
  function closeSp() {
    if (!spOpen) return; spOpen = false; app.classList.remove('rg-sopen');
    var done = function () { sp.style.display = 'none'; sp.replaceChildren(); };
    if (!sp.animate) { done(); return; }
    try {
      var bg = getComputedStyle(sp).backgroundColor, inn = $('.rg-sp-in', sp);
      if (inn) inn.animate([{ opacity: 1 }, { opacity: 0 }], { duration: fast ? 1 : 200, fill: 'forwards' });
      var a = sp.animate([{ clipPath: 'inset(0px 0px 0px 0px round 0px)', backgroundColor: bg }, { clipPath: ir, backgroundColor: '#050607' }], { duration: fast ? 1 : 460, easing: 'cubic-bezier(.5,0,.25,1)', fill: 'forwards' });
      a.onfinish = function () { done(); a.cancel(); };
    } catch (e) { done(); }
  }
  function tipOff() { var t = $('.rg-tip'); if (t) t.remove(); }

  /* ── چتِ واقعی ── */
  var QUICK = ['گزارش امروز رو بده', 'وضعیت مسابقه‌ها چطوره؟', 'کارهای زمان‌بندی‌شده‌ام چیه؟', 'فردا ساعت ۸ یادم بنداز'];
  function charge(on) { if (isl) isl.classList.toggle('rg-charge', on); }
  function down() { feed.scrollTop = feed.scrollHeight; }
  function lines(box, arr) { arr.forEach(function (t, i) { if (i) box.append(doc.createElement('br')); box.append(doc.createTextNode(t)); }); }
  function applyAccess() {
    if (!inp) return;
    var ok = !!ST.access.usable;
    inp.disabled = !ok;
    inp.placeholder = ok ? 'به رهگشا بگو…' : 'رهگشا فعلاً در دسترس نیست';
    goBtn.disabled = busy || !ok || !inp.value.trim();
  }
  function hero() {
    feed.replaceChildren();
    var h = el('div', 'rg-hero'), hb = el('div', 'rg-hb');
    hb.innerHTML = '<svg class="rg-ol" viewBox="0 0 1280 1240"><polygon pathLength="100" points="282,5 655,495 663,222 1220,1228 637,707 635,1013 158,273 500,580"/></svg>';
    hb.append(logo(112));
    h.append(hb);
    if (!ST.access.usable) { h.append(el('div', 'rg-ban', ST.access.message || 'رهگشا فعلاً در دسترس نیست')); feed.append(h); return; }
    var p = el('p', null, 'هرچی بخوای بگو؛ کار انجام می‌دم، گزارش می‌دم یا فقط حرف می‌زنیم.'), chips = el('div', 'rg-chips');
    QUICK.forEach(function (t) { var b = el('button', 'rg-chip', t); b.type = 'button'; b.onclick = function () { send(t); }; chips.append(b); });
    h.append(p, chips); feed.append(h);
  }
  function report(arr) {
    var r = el('div', 'rg-rep'); r.append(el('b', null, '📋 گزارش سیستم'));
    arr.forEach(function (t) { r.append(doc.createElement('br')); r.append(doc.createTextNode('— ' + t)); });
    return r;
  }
  function aiRow(text, extra, charging) {
    var r = el('div', 'rg-row rg-ai'), av = el('span', 'rg-av'), b = el('div', 'rg-bub', text);
    var lg = logo(22, charging); if (!charging) lg.classList.add('rg-still'); av.append(lg); if (extra) b.append(extra); r.append(av, b); return r;
  }
  function msgRow(m) {
    if (m.who === 'me') { var r = el('div', 'rg-row rg-me'); r.append(el('div', 'rg-bub', m.text)); return r; }
    var extra = null;
    if ((m.report && m.report.length) || m.memo) {
      extra = el('div');
      if (m.report && m.report.length) extra.append(report(m.report));
      if (m.memo) extra.append(el('div', 'rg-memo', 'Memo Updated📝'));
    }
    return aiRow(m.text, extra);
  }
  function user(t) { var r = el('div', 'rg-row rg-me'); r.append(el('div', 'rg-bub', t)); feed.append(r); down(); }
  function ai(text, extra) { var r = aiRow(text, extra); feed.append(r); down(); return r; }
  function typing() {
    var r = aiRow('رهگشا در حال فکر…', null, true), b = r.querySelector('.rg-bub');
    b.style.cssText = 'color:var(--hint);font-size:13px'; feed.append(r); charge(true); down(); return r;
  }
  function renderMsgs(list) {
    feed.replaceChildren();
    if (!list.length) { ST.lastId = 0; hero(); return; }
    list.forEach(function (m) { feed.append(msgRow(m)); });
    ST.lastId = list[list.length - 1].id || 0;
    down();
  }
  function confirmBox(c) {
    var box = el('div', 'rg-cf'), t = el('div', 'rg-t'), bt = el('div', 'rg-bt'), y = el('button', 'rg-y', '✅ انجام بده'), n = el('button', 'rg-n', '❌ لغو');
    y.type = n.type = 'button'; lines(t, String(c.text || '').split('\n')); bt.append(y, n); box.append(t, bt);
    y.onclick = function () {
      y.disabled = n.disabled = true; var ty = typing();
      call('/hub/api/rg/confirm', { yes: true }).then(function (r) { ty.remove(); charge(false); box.remove(); ai(r.reply || 'انجام شد ✅'); quiet(); },
        function (e) { ty.remove(); charge(false); y.disabled = n.disabled = false; toast(errMsg(e)); });
    };
    n.onclick = function () {
      y.disabled = n.disabled = true;
      call('/hub/api/rg/confirm', { yes: false }).then(function (r) { box.remove(); ai(r.reply || 'لغو شد.'); quiet(); },
        function (e) { y.disabled = n.disabled = false; toast(errMsg(e)); });
    };
    return box;
  }
  function learnBox(l) {
    var box = el('div', 'rg-cf'), t = el('div', 'rg-t', l.text), bt = el('div', 'rg-bt'), y = el('button', 'rg-y', '✅ آره، همیشه'), n = el('button', 'rg-n', 'نه، ممنون');
    y.type = n.type = 'button'; bt.append(y, n); box.append(t, bt);
    y.onclick = function () {
      y.disabled = true;
      call('/hub/api/rg/learn', { yes: true, key: l.key, val: l.val }).then(function (r) { applyS(r); box.remove(); try { toast('ذخیره شد'); } catch (e) {} },
        function (e) { y.disabled = false; });
    };
    n.onclick = function () { box.remove(); };
    return box;
  }
  function quiet() {
    call('/hub/api/rg/session/0').then(function (r) {
      var m = r.messages || []; if (r.session_id) ST.sid = r.session_id; ST.lastId = m.length ? m[m.length - 1].id : 0;
    }, function () {});
  }
  function sync() {
    call('/hub/api/rg/session/0').then(function (r) {
      if (busy) return;
      var m = r.messages || [], last = m.length ? m[m.length - 1].id : 0;
      if ((r.session_id || null) !== ST.sid || last !== ST.lastId) { ST.sid = r.session_id || null; renderMsgs(m); }
    }, function () {});
  }
  function send(t) {
    t = (t || '').trim(); if (!t || busy) return;
    if (!ST.access.usable) { return; }
    if ($('.rg-hero', feed) || $('.rg-ctr', feed)) feed.replaceChildren();
    user(t); inp.value = ''; inp.style.height = ''; busy = true; goBtn.disabled = true;
    var ty = typing();
    call('/hub/api/rg/send', { text: t, session_id: ST.sid }).then(function (r) {
      ty.remove(); charge(false);
      if (r.session_id) ST.sid = r.session_id;
      if (r.error) { ai(r.error); return; }
      if (r.confirm) { ai('باشه، این اقدام رو آماده کردم:', confirmBox(r.confirm)); return; }
      var extra = el('div'), has = false;
      if (r.report && r.report.length) { extra.append(report(r.report)); has = true; }
      if (r.memo) { extra.append(el('div', 'rg-memo', 'Memo Updated📝')); has = true; }
      if (r.panel_sent) { extra.append(el('div', 'rg-memo', '📂 پنلِ درخواستی در چتِ ربات برات فرستاده شد.')); has = true; }
      if (r.learn) { extra.append(learnBox(r.learn)); has = true; }
      ai((r.reply || '') + (r.extra ? '\n\n' + r.extra : ''), has ? extra : null);
      quiet();
    }, function (e) {
      ty.remove(); charge(false);
      if (e && e.data && (e.data.error === 'offline' || e.data.error === 'blocked' || e.data.error === 'nokey')) {
        ST.access = { usable: false, message: e.data.message }; applyAccess();
      }
      ai('⚠️ ' + errMsg(e));
    }).then(function () { busy = false; goBtn.disabled = !ST.access.usable || !inp.value.trim(); });
  }
  function drawer(o) { app.classList.toggle('rg-open', o); if (o) loadSessions(); }
  function drawList() {
    listEl.replaceChildren();
    if (!ST.sessions.length) { listEl.append(el('div', 'rg-note rg-ctr', 'هنوز چتی نداری.')); return; }
    var g = null;
    ST.sessions.forEach(function (s) {
      if (s.group !== g) { g = s.group; listEl.append(el('h4', null, g)); }
      var b = el('button', 'rg-it' + (s.id === ST.sid ? ' rg-on' : ''), s.title); b.type = 'button';
      b.onclick = function () { openSession(s.id); };
      listEl.append(b);
    });
  }
  function loadSessions() {
    drawList();
    call('/hub/api/rg/sessions').then(function (r) { ST.sessions = r.sessions || []; drawList(); }, function () {});
  }
  function openSession(id) {
    if (busy) { toast('صبر کن رهگشا جواب بده'); return; }
    drawer(false); feed.replaceChildren(el('div', 'rg-note rg-ctr', 'در حال بارگذاری…'));
    call('/hub/api/rg/session/' + id).then(function (r) { ST.sid = r.session_id; renderMsgs(r.messages || []); },
      function (e) { feed.replaceChildren(el('div', 'rg-note rg-ctr', '⚠️ ' + errMsg(e))); });
  }
  function fresh() {
    if (busy) return;
    drawer(false);
    call('/hub/api/rg/new', {}).then(function (r) { ST.sid = r.session_id; renderMsgs([]); },
      function (e) { toast0(errMsg(e)); });
  }
  function toast0(t) { try { ctx.toast(t); } catch (e) {} }
  function boot() {
    var seq = ++loadSeq;
    feed.replaceChildren(el('div', 'rg-note rg-ctr', 'در حال بارگذاری…'));
    return call('/hub/api/rg/boot').then(function (r) {
      if (seq !== loadSeq) return;
      ST.me = r.me || ST.me; ST.access = r.access || { usable: true }; ST.sid = r.session_id || null; ST.sessions = r.sessions || [];
      applyS(r); applyAccess(); renderMsgs(r.messages || []);
    }, function (e) {
      if (seq !== loadSeq) return;
      feed.replaceChildren(el('div', 'rg-note rg-ctr', '⚠️ ' + errMsg(e)));
    });
  }

  /* وسط‌چینِ دقیقِ جوهرِ خطِ نستعلیق داخل جزیره (افقی و عمودی) */
  function fit() {
    if (!isOpen || !isl) return;
    var b = $('.rg-ttl b'); if (!b || !isl.clientHeight) return;
    var old = b.querySelector('i'); if (old) old.remove(); b.style.transform = '';
    var mk = el('i'); mk.style.cssText = 'display:inline-block;width:0;height:0;vertical-align:baseline'; b.append(mk);
    var cx = doc.createElement('canvas').getContext('2d');
    cx.font = getComputedStyle(b).font; cx.direction = 'rtl'; cx.textAlign = 'left'; cx.textBaseline = 'alphabetic';
    var m = cx.measureText('رهگشا');
    if (m.actualBoundingBoxLeft == null) { mk.remove(); return; }
    var dx = m.width / 2 - (m.actualBoundingBoxRight - m.actualBoundingBoxLeft) / 2;
    var dy = isl.clientHeight / 2 - (mk.offsetTop + (m.actualBoundingBoxDescent - m.actualBoundingBoxAscent) / 2);
    b.style.transform = 'translate(' + dx.toFixed(1) + 'px,' + dy.toFixed(1) + 'px)';
  }

  /* ── ساختِ صفحه (یک‌بار) ── */
  var I_BACK = '<svg viewBox="0 0 24 24"><path d="M9 5l7 7-7 7"/></svg>';
  var I_HIST = '<svg viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h10"/></svg>';
  var I_NEW = '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>';
  var I_SEND = '<svg viewBox="0 0 24 24"><path d="M21 3 10 14M21 3l-7 18-4-7-7-4z"/></svg>';
  function ib(icon, label, cls) { var b = el('button', 'rg-ib' + (cls ? ' ' + cls : '')); b.type = 'button'; b.setAttribute('aria-label', label); b.innerHTML = icon; return b; }

  function build() {
    app = el('div', 'rg-app');
    var head = el('header', 'rg-head'), back = ib(I_BACK, 'بازگشت'), hist = ib(I_HIST, 'تاریخچه'), neu = ib(I_NEW, 'گفتگوی جدید');
    hist.style.marginInlineStart = 'auto';
    isl = el('div', 'rg-ttl'); isl.setAttribute('role', 'button'); isl.tabIndex = 0; isl.setAttribute('aria-label', 'رهگشا — برای تنظیمات نگه دارید');
    isl.append(el('b', null, 'رهگشا'));
    head.append(back, isl, hist, neu);
    feed = el('div', 'rg-feed');
    var form = el('form', 'rg-form'); inp = el('textarea', 'rg-in'); inp.rows = 1; inp.setAttribute('enterkeyhint', 'send');
    inp.placeholder = 'به رهگشا بگو…';
    goBtn = el('button', 'rg-send'); goBtn.type = 'submit'; goBtn.disabled = true; goBtn.setAttribute('aria-label', 'ارسال'); goBtn.innerHTML = I_SEND;
    form.append(inp, goBtn);
    var scrim = el('div', 'rg-scrim');
    dr = el('aside', 'rg-drawer');
    var dh = el('div', 'rg-dh'); dh.append(logo(26), el('b', null, 'گفتگوها'));
    var newc = el('button', 'rg-newc', 'گفتگوی جدید'); newc.type = 'button';
    listEl = el('div', 'rg-list');
    dr.append(dh, newc, listEl);
    sp = el('div', 'rg-sp');
    app.append(head, feed, form, scrim, dr, sp);
    root.replaceChildren(app);

    form.onsubmit = function (e) { e.preventDefault(); send(inp.value); };
    inp.addEventListener('input', function () { goBtn.disabled = busy || !ST.access.usable || !inp.value.trim(); inp.style.height = 'auto'; inp.style.height = Math.min(inp.scrollHeight, 120) + 'px'; });
    inp.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey && matchMedia('(hover:hover)').matches) { e.preventDefault(); send(inp.value); } });
    hist.onclick = function () { drawer(true); }; scrim.onclick = function () { drawer(false); };
    neu.onclick = fresh; newc.onclick = fresh; back.onclick = function () { api.back(); };

    /* نگه‌داشتنِ جزیره → تنظیمات. متنِ جزیره هرگز سلکت/کپی نمی‌شود */
    var ht = 0, sx = 0, sy = 0;
    function cancel() { clearTimeout(ht); isl.classList.remove('rg-hold'); }
    isl.addEventListener('pointerdown', function (e) {
      if (e.pointerType === 'mouse' && e.button > 0) return;
      sx = e.clientX; sy = e.clientY; nosel(); isl.classList.add('rg-hold'); clearTimeout(ht);
      ht = setTimeout(function () { isl.classList.remove('rg-hold'); nosel(); openSp(); }, 420);
    });
    isl.addEventListener('pointermove', function (e) { if (Math.abs(e.clientX - sx) > 12 || Math.abs(e.clientY - sy) > 12) cancel(); });
    ['pointerup', 'pointerleave', 'pointercancel'].forEach(function (n) { isl.addEventListener(n, cancel); });
    ['selectstart', 'contextmenu', 'dragstart', 'gesturestart', 'copy', 'cut'].forEach(function (n) { isl.addEventListener(n, function (e) { e.preventDefault(); }); });
    isl.addEventListener('touchstart', function (e) { e.preventDefault(); nosel(); }, { passive: false });
    isl.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openSp(); } });
    doc.addEventListener('selectionchange', function () { if (isOpen && (isl.classList.contains('rg-hold') || spOpen)) nosel(); });
    doc.addEventListener('keydown', function (e) { if (e.key === 'Escape' && isOpen) api.back(); });
    doc.addEventListener('visibilitychange', function () { if (doc.visibilityState === 'visible' && isOpen && !busy && !spOpen) sync(); });
    window.addEventListener('resize', function () { fit(); if (isOpen) down(); });
    if (doc.fonts && doc.fonts.ready) doc.fonts.ready.then(fit);
    built = true;
  }

  var api = {
    mount: function (r, c) { root = r; ctx = c; },
    open: function () {
      if (!built) build();
      isOpen = true; tipOff(); ST.ADM = null; ST.admLoading = false;
      requestAnimationFrame(function () {
        fit();
        var f = doc.fonts && doc.fonts.load ? doc.fonts.load("19px 'Noto Nastaliq Urdu'", 'رهگشا') : null;
        if (f && f.then) f.then(fit, fit);
        var seen = false; try { seen = !!ctx.LS.get('hub:rg:tip'); } catch (e) {}
        if (!seen) app.append(el('div', 'rg-tip', 'برای تنظیمات رهگشا، جزیره را نگه دار'));
      });
      boot();
    },
    /* true = خودش مصرفش کرد؛ در غیر این صورت چت بسته می‌شود */
    back: function () {
      if (!isOpen) return;
      if (spOpen) { spBack(); return; }
      if (app.classList.contains('rg-open')) { drawer(false); return; }
      ctx.close();
    },
    onClose: function () {
      isOpen = false; cancelHold(); drawer(false);
      if (spOpen) { spOpen = false; app.classList.remove('rg-sopen'); sp.style.display = 'none'; sp.replaceChildren(); }
    }
  };
  function cancelHold() { if (isl) isl.classList.remove('rg-hold'); }
  window.HubRahgosha = api;
})();
