/* رهگشا — چتِ دستیارِ هوشمندِ CMS داخلِ «پنل من» (ماژولِ تنبل: فقط با زدنِ دکمه‌ی رهگشا در خانه بار می‌شود)
   • نسخه‌ی ظاهری: پاسخ‌ها و داده‌های ادمین/حافظه نمونه‌اند (ctx.preview). اتصال به ربات: get_prefs / save_prefs در hub_chat.py.
   • نگه‌داشتنِ جزیره (~۰٫۴۲ ثانیه) → تنظیمات مثل اپ از دلِ جزیره باز می‌شود.
   • همه‌ی رشته‌های کاربر با textContent نمایش داده می‌شوند (innerHTML فقط برای آیکون‌های ثابت). */
(function () {
  'use strict';
  var doc = document;
  var ctx = null, root = null, app = null, feed = null, inp = null, goBtn = null, isl = null, sp = null, hbEl = null;
  var built = false, isOpen = false, spOpen = false, busy = false, ir = null, replyT = 0, tT = 0;
  var realRole = 'pishva';

  function el(tag, cls, txt) { var n = doc.createElement(tag); if (cls) n.className = cls; if (txt != null) n.textContent = txt; return n; }
  function $(s, r) { return (r || root).querySelector(s); }
  function logo(w, charge) {
    var b = el('span', 'rg-bw' + (charge ? ' rg-charge' : '')); b.style.width = w + 'px';
    b.append(el('span', 'rg-bolt')); return b;
  }
  var fast = !!(window.matchMedia && matchMedia('(prefers-reduced-motion:reduce)').matches);

  /* ── داده‌ها: عیناً از ai_persona.py / ai_more.py (طبقِ پیش‌نمایش ۶) ── */
  var MODES=[['normal','🙂','نرمال','رفتار پیش‌فرض و متعادل؛ همون سبک همیشگی.'],['serious','🧐','جدی','جدی، رسمی‌تر و دقیق؛ بدون شوخی و حاشیه.'],['warm','🤗','گرم و صمیمی','خودمونی مثل یه دوست نزدیک؛ با انرژی و محبت.'],['analyst','📈','تحلیلگر','عدد، دلیل، مقایسه و نتیجه‌گیری؛ ساختارمند.'],['harsh','🔥','تند و خشن','رک و بی‌تعارف و کوتاه؛ فقط در لحن، نه در درستیِ اطلاعات.'],['kind','💗','مهربان','مهربان، صبور و دلگرم‌کننده؛ بدون تعارفِ دروغ.'],['brief','✂️','خلاصه‌گو','فوق‌العاده خلاصه: اول نتیجه، بدون مقدمه.']];
  var MD={normal:{emoji:'normal',humor:'low',length:'medium',structure:'text',formality:'casual'},serious:{emoji:'none',humor:'off',length:'medium',structure:'text',formality:'semi'},warm:{emoji:'normal',humor:'low',length:'medium',structure:'text',formality:'casual'},analyst:{emoji:'low',humor:'off',length:'long',structure:'list',formality:'semi'},harsh:{emoji:'low',humor:'high',length:'short',structure:'text',formality:'casual'},kind:{emoji:'high',humor:'low',length:'medium',structure:'text',formality:'casual'},brief:{emoji:'low',humor:'off',length:'short',structure:'text',formality:'casual'}};
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
   deliver:['📥','ساعات دریافت پیام‌های رهگشا',[['all','همیشه'],['8-22','۸ تا ۲۲'],['9-21','۹ تا ۲۱'],['10-23','۱۰ تا ۲۳']]]
  };
  var SEC={style:['🎨','سبک گفتار',['emoji','humor','profanity','formality']],format:['📏','قالب جواب',['length','structure','result_first']],behavior:['⚙️','رفتار',['ask_or_guess','confirm','proactive','auto_remind','memory','trainer','voice']],time:['🕒','زمان و دریافت',['night_mode','work_mode','deliver']]};
  var TXT={ai_name:['✍️','اسم دلخواه برای رهگشا',30,'اسمی که می‌خوای رهگشا با اون خودش رو معرفی کنه (مثلاً «یار»). «-» = حذف.'],user_nick:['🏷️','لقبِ تو',30,'رهگشا تو رو چی صدا بزنه؟ (مثلاً «داداش»، «رئیس»). «-» = حذف.'],dialect:['🗺️','گویش / سبک خاص',40,'مثلاً «کرمانشاهی» یا «ادبی». «-» = حذف.'],report_template:['🧾','قالب گزارش',300,'گزارش‌های مسابقه رو چه ترتیب و فیلدی بخوای؟ مثال: «اول جدول، بعد بازی‌های در انتظار، آخر هشدارها». «-» = حذف.'],persona_text:['📝','پرامپت اختصاصی',500,'هر چیزی درباره‌ی لحن و سبکِ دلخواهت. فقط روی سبک اثر می‌ذاره و قوانین سیستم رو تغییر نمی‌ده. «-» = حذف.'],shortcut_add:['⚡','میانبر جدید',400,'با این قالب بنویس: «کلمه = دستور کامل». مثال: «صبحانه = خلاصه صبحگاهی بده و وضعیت مسابقات رو بگو»'],profile_save:['💾','ذخیره‌ی پروفایل',20,'یه اسم برای این ترکیب تنظیمات بنویس (مثلاً «مسابقه»).']};
  var HINTS={confirm:'«فقط کارهای مهم» یعنی اخطار/اخراج/حذف/اطلاعیه/تغییر تنظیمات و … قبل از اجرا از تو تأیید می‌خواد.',deliver:'یادآورها و نتیجه‌ی اقدام‌های زمان‌بندی‌شده خارج از این ساعت‌ها نگه داشته می‌شن و اول بازه‌ی بعدی میان.',voice:'(آزمایشی) نسخه‌ی صوتیِ جواب‌های کوتاه به‌صورت فایل صوتی فرستاده می‌شه.'};
  var SAMPLE={normal:'سلام! امروز ۴ مسابقه تموم شده و ۲ مورد منتظر ثبت نتیجه‌ست.',serious:'گزارش امروز: ۴ مسابقه تمام‌شده، ۲ مسابقه در انتظار ثبت نتیجه.',warm:'سلام عزیز! امروز خیلی خوب پیش رفت؛ ۴ مسابقه تموم شد و فقط ۲ تا مونده 🤗',analyst:'• مسابقات تمام‌شده: ۴ (۶۷٪)\n• در انتظار: ۲ (۳۳٪)\n• نتیجه: روند ثبت نتایج رو به بهبوده.',harsh:'۲ تا مسابقه هنوز نتیجه نخورده. اینجوری نمیشه؛ زودتر ثبتشون کن.',kind:'دمت گرم، ۴ مسابقه رو تموم کردی 💗 دوتای باقی‌مونده رو هم آروم‌آروم ثبت می‌کنیم.',brief:'۴ تموم، ۲ در انتظار.'};
  var ROLES=[['pishva','مدیر ارشد'],['tm','مسئول مسابقات'],['sm','مسئول انتظامات']];
  var ADM=[['علی رضایی','مسئول مسابقات','۱۲ گفتگو'],['سارا احمدی','مسئول انتظامات','۷ گفتگو'],['مهدی کریمی','مسئول مسابقات','۳ گفتگو']];
  var CATS=['👥 بازیکنان','🏆 مسابقات','📣 مخابرات','👑 مدیران','⚙️ سیستم','⏰ زمان‌بندی'];
  var ABIL={pishva:[['👥','مدیریت بازیکنان','ثبت، ویرایش، اخطار، اخراج'],['🏆','مسابقات و نتایج','ثبت و اصلاح نتیجه، پیش‌بینی'],['📣','مخابرات','بیانیه، خبر، پیام مستقیم'],['👑','مدیریت مدیران','نقش، دسترسی، اخطار'],['⚙️','تنظیمات سیستم','وضعیت، تعمیرات، ساعت کاری'],['🔔','یادآوری و زمان‌بندی','اجرای کار در زمان دلخواه'],['🧠','حافظه‌ی مدیریتی','یادداشت‌های محرمانه‌ی مدیر ارشد']],tm:[['👥','دیدن بازیکنان','فقط خواندن و جست‌وجو'],['🏆','مسابقات و نتایج','ثبت نتیجه، ساخت مسابقه'],['🔔','یادآوری','برای خودت'],['🧠','حافظه‌ی شخصی','ترجیح‌ها و یادداشت‌ها']],sm:[['⚠️','اخطار بازیکنان','طبق سقف دسترسی نقش'],['🚫','درخواست اخراج','برای تأیید مدیر ارشد'],['🔔','یادآوری','برای خودت'],['🧠','حافظه‌ی شخصی','ترجیح‌ها و یادداشت‌ها']]};

  /* ── حالتِ تنظیمات (نمونه؛ هنوز به get_prefs/save_prefs وصل نیست) ── */
  var P = {}, role = 'pishva', aiOn = true, lock = true,
    PERM = CATS.map(function () { return true; }), ADMOFF = ADM.map(function () { return false; }),
    MEM = [[12, 'ترجیح می‌دم گزارش‌ها کوتاه باشن'], [17, 'جلسه‌ی مسئولان پنجشنبه‌ها ساعت ۱۷ است'], [21, 'فینال با داوری آقای احمدی']],
    SCH = [['⏰', 'یادآوری جلسه‌ی پنجشنبه', 'فردا ۰۸:۰۰'], ['📣', 'ارسال خبر به مدیران', 'پنجشنبه ۱۰:۰۰'], ['🧹', 'گزارش هفتگی غیبت', 'جمعه ۲۰:۰۰']],
    NOTES = [['نوبت اخطار دوم برای یکی از بازیکنان نزدیکه'], ['مدیر مسابقات درباره‌ی ساعت شروع سؤال داشت']];
  var stack = [], body = null, titleEl = null, backBtn = null, toastEl = null;

  function mode() { return P.mode || 'normal'; }
  function modeObj() { return MODES.filter(function (m) { return m[0] === mode(); })[0]; }
  function lab(k, v) { var l = OPT[k][2].filter(function (x) { return x[0] === v; })[0]; return l ? l[1] : String(v); }
  function val(k) { return P[k] != null ? lab(k, P[k]) : 'پیش‌فرض'; }
  function eff(k) {
    if (P[k] != null) return P[k];
    var d = { result_first: mode() === 'brief' ? '1' : '0', ask_or_guess: 'ask', confirm: 'never', profanity: 'auto' };
    return MD[mode()][k] || d[k] || '0';
  }
  function toast(t) {
    if (!toastEl) return;
    toastEl.textContent = t; toastEl.classList.add('rg-on');
    clearTimeout(tT); tT = setTimeout(function () { toastEl.classList.remove('rg-on'); }, 1800);
    try { ctx.hx.sel(); } catch (e) {}
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
    var f = doc.createDocumentFragment(), m = modeObj(), isP = role === 'pishva';
    var hc = el('div', 'rg-hc'), tx = el('div');
    var cps = el('div', 'rg-cps');
    [lab('length', eff('length')), 'اموجی: ' + lab('emoji', eff('emoji')), 'تأیید: ' + lab('confirm', eff('confirm'))].forEach(function (t) { cps.append(el('span', 'rg-cp', t)); });
    tx.append(el('b', null, m[1] + ' ' + m[2]), cps);
    hc.append(logo(46), tx); f.append(hc);
    if (ctx.preview && realRole === 'pishva') {
      var seg = el('div', 'rg-seg');
      ROLES.forEach(function (r) {
        var b = el('button', r[0] === role ? 'rg-on' : '', r[1]); b.type = 'button';
        b.onclick = function () { role = r[0]; refresh(); }; seg.append(b);
      });
      f.append(seg, el('small', null, 'فقط در نسخه‌ی نمایشی: دیدنِ تنظیمات به‌عنوانِ هر نقش'));
    }
    grp(f, 'شخصی‌سازی', [
      row({ ic: '🎭', l: 'حالت رفتار', v: m[1] + ' ' + m[2], go: 1, fn: function () { go('حالت رفتار', pgMode); } }),
      row({ ic: SEC.style[0], l: SEC.style[1], go: 1, fn: function () { go(SEC.style[1], function () { return pgSec('style'); }); } }),
      row({ ic: SEC.format[0], l: SEC.format[1], go: 1, fn: function () { go(SEC.format[1], function () { return pgSec('format'); }); } }),
      row({ ic: SEC.behavior[0], l: SEC.behavior[1], go: 1, fn: function () { go(SEC.behavior[1], function () { return pgSec('behavior'); }); } }),
      row({ ic: SEC.time[0], l: SEC.time[1], go: 1, fn: function () { go(SEC.time[1], function () { return pgSec('time'); }); } }),
      row({ ic: '✍️', l: 'اسم‌ها و گویش', go: 1, fn: function () { go('اسم‌ها و گویش', pgNames); } })]);
    grp(f, 'حافظه و میانبر', [
      row({ ic: '🧠', l: 'حافظه‌ی من', v: P.memory === '0' ? 'خاموش' : 'روشن', go: 1, fn: function () { go('حافظه‌ی من', pgMem); } }),
      row({ ic: '⚡', l: 'میانبرها', v: String(Object.keys(P.shortcuts || {}).length), go: 1, fn: function () { go('میانبرها', pgSc); } }),
      row({ ic: '🧾', l: 'قالب گزارش', v: P.report_template ? 'تنظیم شده' : '—', go: 1, fn: function () { go(TXT.report_template[1], function () { return pgTxt('report_template'); }); } }),
      row({ ic: '📝', l: 'پرامپت اختصاصی', v: P.persona_text ? 'تنظیم شده' : '—', go: 1, fn: function () { go(TXT.persona_text[1], function () { return pgTxt('persona_text'); }); } }),
      row({ ic: '💾', l: 'پروفایل‌ها', v: String(Object.keys(P.profiles || {}).length), go: 1, fn: function () { go('پروفایل‌ها', pgProf); } })]);
    grp(f, 'ابزارها', [
      row({ ic: '🤖', l: 'کارهای زمان‌بندی‌شده', v: String(SCH.length), go: 1, fn: function () { go('کارهای زمان‌بندی‌شده', pgSch); } }),
      row({ ic: '🛠️', l: 'توانایی‌های من', go: 1, fn: function () { go('توانایی‌های من', pgAbil); } }),
      row({ ic: '❓', l: 'چرا این جواب؟', go: 1, fn: function () { go('چرا این جواب؟', pgWhy); } }),
      row({ ic: '👁', l: 'پیش‌نمایش سبک', go: 1, fn: function () { go('پیش‌نمایش سبک', pgPrev); } }),
      row({ ic: '📖', l: 'راهنمای رهگشا', go: 1, fn: function () { go('راهنمای رهگشا', pgHelp); } })]);
    if (isP) grp(f, '👑 مدیریت دستیار (فقط مدیر ارشد)', [
      row({ ic: '🔌', l: 'هوش مصنوعی', s: 'خاموش‌کردن یعنی رهگشا برای همه قطع می‌شه', sw: aiOn, fn: function () { aiOn = !aiOn; refresh(); } }),
      row({ ic: '🔐', l: 'قفل دستور مدیر ارشد', s: 'اجرای اقدام‌های حساس فقط با دستور شما', sw: lock, fn: function () { lock = !lock; refresh(); } }),
      row({ ic: '🛠️', l: 'اختیارات دستیار', go: 1, fn: function () { go('اختیارات دستیار', pgPerm); } }),
      row({ ic: '🗂️', l: 'سوابق چت مدیران', go: 1, fn: function () { go('سوابق چت مدیران', pgLog); } }),
      row({ ic: '🔕', l: 'خاموشی برای ادمین', go: 1, fn: function () { go('خاموشی برای ادمین', pgOff); } }),
      row({ ic: '🎭', l: 'تنظیمات رهگشای مدیران', go: 1, fn: function () { go('تنظیمات رهگشای مدیران', pgAdm); } }),
      row({ ic: '🛰️', l: 'نمای لحظه‌ای', go: 1, fn: function () { go('نمای لحظه‌ای', pgLive); } }),
      row({ ic: '📒', l: 'یادداشت‌های مدیریتی', go: 1, fn: function () { go('یادداشت‌های مدیریتی', pgNotes); } })]);
    grp(f, null, [row({ ic: '♻️', l: 'بازنشانی تنظیمات', dg: 1, fn: askReset })]);
    f.append(el('div', 'rg-note', isP ? '👁️ شما به‌عنوان مدیر ارشد به چت مدیران با رهگشا دسترسی دارید.' : '👁️ یادآوری شفافیت: چت‌های شما با رهگشا برای مدیر ارشد قابل مشاهده است.'));
    return f;
  }
  function pgMode() {
    var f = doc.createDocumentFragment();
    grp(f, null, MODES.map(function (m) { return row({ ic: m[1], l: m[2], s: m[3], ck: mode() === m[0], fn: function () { P.mode = m[0]; toast('حالت «' + m[2] + '» فعال شد'); refresh(); } }); }));
    grp(f, null, [row({ ic: '↩️', l: 'پیروی از پیش‌فرض', fn: function () { delete P.mode; refresh(); } })]);
    return f;
  }
  function pgSec(s) {
    var f = doc.createDocumentFragment();
    grp(f, null, SEC[s][2].map(function (k) { return row({ ic: OPT[k][0], l: OPT[k][1], v: val(k), go: 1, fn: function () { go(OPT[k][1], function () { return pgOpt(k); }); } }); }));
    return f;
  }
  function pgOpt(k) {
    var f = doc.createDocumentFragment();
    grp(f, null, OPT[k][2].map(function (c) { return row({ l: c[1], ck: P[k] === c[0], fn: function () { P[k] = c[0]; toast('ذخیره شد'); refresh(); } }); }));
    if (k !== 'memory') grp(f, null, [row({ ic: '↩️', l: 'پیروی از حالت / پیش‌فرض', fn: function () { delete P[k]; refresh(); } })]);
    if (HINTS[k]) f.append(el('div', 'rg-hint', HINTS[k]));
    return f;
  }
  function pgNames() {
    var f = doc.createDocumentFragment();
    grp(f, null, ['ai_name', 'user_nick', 'dialect'].map(function (k) { return row({ ic: TXT[k][0], l: TXT[k][1], v: P[k] || '—', go: 1, fn: function () { go(TXT[k][1], function () { return pgTxt(k); }); } }); }));
    return f;
  }
  function pgTxt(k) {
    var f = doc.createDocumentFragment(), T = TXT[k], cur = (k === 'shortcut_add' || k === 'profile_save') ? '' : (P[k] || '');
    var ta = el('textarea', 'rg-ta'); ta.maxLength = T[2]; ta.value = cur; ta.placeholder = T[1]; if (T[2] <= 40) ta.style.minHeight = '56px';
    var c = el('div', 'rg-cnt', cur.length + ' / ' + T[2]); ta.oninput = function () { c.textContent = ta.value.length + ' / ' + T[2]; };
    f.append(ta, c, el('div', 'rg-hint', T[3]));
    var s = el('button', 'rg-b1', 'ذخیره'); s.type = 'button';
    s.onclick = function () {
      var v = ta.value.trim().replace(/\s+/g, ' '); if (!v) return;
      if (k === 'shortcut_add') {
        var i = v.indexOf('='); if (i < 1 || !v.slice(i + 1).trim()) { toast('قالب: کلمه = دستور'); return; }
        P.shortcuts = P.shortcuts || {}; P.shortcuts[v.slice(0, i).trim()] = v.slice(i + 1).trim();
      } else if (k === 'profile_save') { P.profiles = P.profiles || {}; P.profiles[v] = { mode: P.mode || null }; }
      else if (v === '-') delete P[k]; else P[k] = v;
      toast('ذخیره شد'); stack.pop(); render(-1);
    };
    f.append(s);
    if (cur) { var d = el('button', 'rg-b2', 'حذف'); d.type = 'button'; d.onclick = function () { delete P[k]; toast('حذف شد'); stack.pop(); render(-1); }; f.append(d); }
    return f;
  }
  function pgMem() {
    var f = doc.createDocumentFragment();
    grp(f, null, [row({ ic: '🧠', l: 'حافظه‌ی شخصی', sw: P.memory !== '0', fn: function () { P.memory = P.memory === '0' ? '1' : '0'; refresh(); } })]);
    if (MEM.length) grp(f, 'یادداشت‌ها', MEM.map(function (m, i) { return row({ l: '#' + m[0] + ' ' + m[1], v: '🗑', fn: function () { MEM.splice(i, 1); toast('حذف شد'); refresh(); } }); }));
    else f.append(el('div', 'rg-note', 'چیزی ذخیره نشده.'));
    if (MEM.length) grp(f, null, [row({ ic: '🧹', l: 'پاک‌کردن همه', dg: 1, fn: function () { MEM = []; refresh(); } })]);
    if (role === 'pishva') grp(f, null, [row({ ic: '📒', l: 'یادداشت‌های مدیریتیِ مدیر ارشد', go: 1, fn: function () { go('یادداشت‌های مدیریتی', pgNotes); } })]);
    return f;
  }
  function pgSc() {
    var f = doc.createDocumentFragment(), sc = P.shortcuts || {}, ks = Object.keys(sc);
    f.append(el('div', 'rg-note', 'یه کلمه‌ی کوتاه که به‌جای یه دستور بلند به رهگشا می‌گی.'));
    if (ks.length) grp(f, null, ks.map(function (k) { return row({ l: '«' + k + '»', s: sc[k], v: '🗑', fn: function () { delete P.shortcuts[k]; refresh(); } }); }));
    else f.append(el('div', 'rg-note', 'میانبری نداری.'));
    if (ks.length < 15) grp(f, null, [row({ ic: '➕', l: 'میانبر جدید', go: 1, fn: function () { go('میانبر جدید', function () { return pgTxt('shortcut_add'); }); } })]);
    return f;
  }
  function pgProf() {
    var f = doc.createDocumentFragment(), pr = P.profiles || {}, ks = Object.keys(pr);
    f.append(el('div', 'rg-note', 'ترکیب‌های ذخیره‌شده؛ با یک دکمه عوض می‌شن.'));
    if (ks.length) grp(f, null, ks.map(function (k) { return row({ ic: '▶️', l: k, v: '🗑', fn: function () { delete P.profiles[k]; refresh(); } }); }));
    else f.append(el('div', 'rg-note', 'پروفایلی نداری.'));
    if (ks.length < 5) grp(f, null, [row({ ic: '➕', l: 'ذخیره‌ی تنظیمات فعلی', go: 1, fn: function () { go('ذخیره‌ی پروفایل', function () { return pgTxt('profile_save'); }); } })]);
    return f;
  }
  function pgSch() { var f = doc.createDocumentFragment(); if (!SCH.length) f.append(el('div', 'rg-note', '📭 چیزی زمان‌بندی نشده')); else info(f, SCH, function (i) { SCH.splice(i, 1); toast('لغو شد'); refresh(); }); return f; }
  function pgAbil() { var f = doc.createDocumentFragment(); info(f, ABIL[role]); f.append(el('div', 'rg-note', 'سقف دسترسی هر نقش همیشه روی تنظیمات سبک اولویت داره.')); return f; }
  function pgWhy() { var f = doc.createDocumentFragment(); info(f, [['🔧', 'ابزارهای استفاده‌شده', 'register_match_result'], ['🎭', 'حالت', modeObj()[2]], ['🧠', 'موارد حافظه‌ی به‌کار رفته', String(MEM.length) + ' مورد']]); return f; }
  function pgHelp() { var f = doc.createDocumentFragment(); info(f, [['💬', 'گفت‌وگو', 'هرچی بخوای بگو؛ اگه کاری باشه خودش انجام می‌ده.'], ['⚡', 'میانبر', 'کلمه‌ی کوتاه به‌جای دستور بلند.'], ['🛡️', 'تأیید', 'برای کارهای مهم تأیید می‌گیره.'], ['🔔', 'یادآوری', '«فردا ۸ یادم بنداز» کافیه.']]); return f; }
  function pgPrev() {
    var f = doc.createDocumentFragment(), t = SAMPLE[mode()];
    if (eff('emoji') === 'none') t = t.replace(/[\u{1F300}-\u{1FAFF}\u2600-\u27BF]/gu, '').trim();
    if (P.user_nick) t = '«' + P.user_nick + '»، ' + t;
    if (P.ai_name) t += '\n— ' + P.ai_name;
    f.append(el('div', 'rg-pvw', t.replace(/\\n/g, '\n')));
    f.append(el('div', 'rg-hint', 'نمونه‌ی یک جواب با تنظیمات فعلی.\nحالت: ' + modeObj()[2] + ' · ' + lab('length', eff('length')) + ' · اموجی: ' + lab('emoji', eff('emoji'))));
    return f;
  }
  function pgPerm() { var f = doc.createDocumentFragment(); grp(f, null, CATS.map(function (c, i) { return row({ l: c, sw: PERM[i], fn: function () { PERM[i] = !PERM[i]; refresh(); } }); })); f.append(el('div', 'rg-note', 'خاموش‌کردن یک دسته، اجرای ابزارهای اون دسته رو واقعاً می‌بنده.')); return f; }
  function pgLog() { var f = doc.createDocumentFragment(); grp(f, null, ADM.map(function (a) { return row({ ic: '👤', l: a[0], s: a[1], v: a[2], go: 1, fn: function () { toast('این بخش در نسخه‌ی واقعی تاریخچه‌ی چت رو باز می‌کنه'); } }); })); return f; }
  function pgOff() { var f = doc.createDocumentFragment(); grp(f, null, ADM.map(function (a, i) { return row({ ic: '👤', l: a[0], s: ADMOFF[i] ? 'رهگشا خاموش' : 'رهگشا روشن', sw: !ADMOFF[i], fn: function () { ADMOFF[i] = !ADMOFF[i]; refresh(); } }); })); return f; }
  function pgAdm() { var f = doc.createDocumentFragment(); grp(f, null, ADM.map(function (a) { return row({ ic: '👤', l: a[0], s: a[1], go: 1, fn: function () { go(a[0], function () { return pgAdmOne(a); }); } }); })); return f; }
  function pgAdmOne() { var f = doc.createDocumentFragment(); info(f, [['🎭', 'حالت', 'نرمال'], ['📏', 'طول جواب', 'متوسط'], ['🛡️', 'تأیید قبل از اقدام', 'فقط کارهای مهم']]); grp(f, null, [row({ ic: '♻️', l: 'بازنشانی تنظیمات این مدیر', dg: 1, fn: function () { toast('بازنشانی شد'); } })]); return f; }
  function pgLive() { var f = doc.createDocumentFragment(); info(f, [['🟢', 'علی رضایی', 'در حال گفتگو با رهگشا'], ['⚪', 'سارا احمدی', '۱۲ دقیقه پیش'], ['⚪', 'مهدی کریمی', 'دیروز']]); return f; }
  function pgNotes() { var f = doc.createDocumentFragment(); info(f, NOTES.map(function (n) { return ['📒', n[0]]; })); return f; }
  function askReset() {
    var d = el('div', 'rg-dlg'), w = el('div'), p1 = el('p'), p2 = el('p');
    p1.append(el('b', null, 'بازنشانی همه‌ی تنظیمات؟')); p2.textContent = 'همه‌ی گزینه‌ها به پیش‌فرض برمی‌گردن.'; p2.style.cssText = 'color:var(--hint);font-size:13px';
    var y = el('button', 'rg-b1', 'بازنشانی'), n = el('button', 'rg-b2', 'انصراف'); y.type = n.type = 'button';
    y.onclick = function () { P = {}; d.remove(); toast('بازنشانی شد'); refresh(); }; n.onclick = function () { d.remove(); };
    w.append(p1, p2, y, n); d.append(w); sp.append(d);
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

  /* ── چت (پاسخ‌ها در نسخه‌ی نمایشی ساختگی‌اند) ── */
  var QUICK = ['گزارش امروز رو بده', 'نتیجه‌ی یه مسابقه ثبت کن', 'به بازیکن غایب اخطار بده', 'فردا ساعت ۸ یادم بنداز'];
  function charge(on) { if (isl) isl.classList.toggle('rg-charge', on); }
  function down() { feed.scrollTop = feed.scrollHeight; }
  function lines(box, arr) { arr.forEach(function (t, i) { if (i) box.append(doc.createElement('br')); box.append(doc.createTextNode(t)); }); }
  function hero() {
    feed.replaceChildren();
    var h = el('div', 'rg-hero'), hb = el('div', 'rg-hb');
    hb.innerHTML = '<svg class="rg-ol" viewBox="0 0 1280 1240"><polygon pathLength="100" points="282,5 655,495 663,222 1220,1228 637,707 635,1013 158,273 500,580"/></svg>';
    hb.append(logo(112));
    var p = el('p', null, 'هرچی بخوای بگو؛ کار انجام می‌دم، گزارش می‌دم یا فقط حرف می‌زنیم.'), chips = el('div', 'rg-chips');
    QUICK.forEach(function (t) { var b = el('button', 'rg-chip', t); b.type = 'button'; b.onclick = function () { send(t); }; chips.append(b); });
    h.append(hb);
    if (ctx.preview) h.append(el('div', 'rg-ban', 'نسخه‌ی نمایشی · پاسخ‌ها ساختگی‌اند و کاری اجرا نمی‌شود'));
    h.append(p, chips); feed.append(h);
  }
  function user(t) { var r = el('div', 'rg-row rg-me'); r.append(el('div', 'rg-bub', t)); feed.append(r); down(); }
  function ai(text, extra) {
    var r = el('div', 'rg-row rg-ai'), av = el('span', 'rg-av'), b = el('div', 'rg-bub', text);
    av.append(logo(22)); if (extra) b.append(extra); r.append(av, b); feed.append(r); down(); return r;
  }
  function typing() {
    var r = el('div', 'rg-row rg-ai'), av = el('span', 'rg-av'), b = el('div', 'rg-bub', 'رهگشا در حال فکر…');
    av.append(logo(22, true)); b.style.cssText = 'color:var(--hint);font-size:13px'; r.append(av, b); feed.append(r); charge(true); down(); return r;
  }
  function report(arr) { var r = el('div', 'rg-rep'); r.append(el('b', null, '📋 گزارش سیستم')); r.append(doc.createElement('br')); lines(r, arr); return r; }
  function confirmBox(arr) {
    var c = el('div', 'rg-cf'), t = el('div', 'rg-t'), bt = el('div', 'rg-bt'), y = el('button', 'rg-y', '✅ انجام بده'), n = el('button', 'rg-n', '❌ لغو');
    y.type = n.type = 'button'; lines(t, ['🛡️ قبل از اجرا تأیید می‌خوام:'].concat(arr)); bt.append(y, n); c.append(t, bt);
    y.onclick = function () {
      c.remove(); var ty = typing();
      replyT = setTimeout(function () { ty.remove(); charge(false); ai('انجام شد ✅', report(['انجام شد؛ اخطار ثبت و به مدیر ارشد اطلاع داده شد.'])); }, 900);
    };
    n.onclick = function () { c.remove(); ai('لغو شد؛ چیزی اجرا نشد.'); };
    return c;
  }
  function reply(t) {
    if (/اخطار|حذف|اخراج/.test(t)) return ai('باشه، این اقدام رو آماده کردم:', confirmBox(['🛠 warn_player', '📝 reason=غیبت بدون هماهنگی']));
    if (/ثبت|نتیجه/.test(t)) return ai('نتیجه ثبت شد.', report(['register_match_result: بازی سفید ۱ – ۰ سیاه ثبت شد.']));
    if (/گزارش|امروز/.test(t)) return ai('امروز ۱۲ بازیکن فعال بودن، ۴ مسابقه تموم شد و ۲ مسابقه هنوز منتظر ثبت نتیجه‌ست. بخوای جزئیات هر کدوم رو بگم.');
    if (/یادم بنداز|یادآور/.test(t)) return ai('یادآوری تنظیم شد؛ فردا ساعت ۸ صبح بهت پیام می‌دم.', report(['set_reminder: فردا ۰۸:۰۰']));
    return ai('متوجه شدم. بیشتر توضیح بده تا دقیق‌تر کمکت کنم؛ یا بگو چه کاری رو انجام بدم.');
  }
  function send(t) {
    t = (t || '').trim(); if (!t || busy) return;
    if ($('.rg-hero', feed)) feed.replaceChildren();
    user(t); inp.value = ''; inp.style.height = ''; goBtn.disabled = true; busy = true;
    var ty = typing();
    replyT = setTimeout(function () { ty.remove(); charge(false); reply(t); busy = false; goBtn.disabled = !inp.value.trim(); }, 1100);
  }
  function drawer(o) { app.classList.toggle('rg-open', o); }
  function fresh() { drawer(false); clearTimeout(replyT); busy = false; charge(false); hero(); goBtn.disabled = !inp.value.trim(); }

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
    inp.placeholder = ctx.preview ? 'به رهگشا بگو… (نسخه‌ی نمایشی)' : 'به رهگشا بگو…';
    goBtn = el('button', 'rg-send'); goBtn.type = 'submit'; goBtn.disabled = true; goBtn.setAttribute('aria-label', 'ارسال'); goBtn.innerHTML = I_SEND;
    form.append(inp, goBtn);
    var scrim = el('div', 'rg-scrim'), dr = el('aside', 'rg-drawer');
    var dh = el('div', 'rg-dh'); dh.append(logo(26), el('b', null, 'گفتگوها'));
    var newc = el('button', 'rg-newc', 'گفتگوی جدید'); newc.type = 'button';
    var list = el('div', 'rg-list');
    [['امروز', ['ثبت نتیجه‌ی مسابقه‌ی امروز', 'گزارش بازیکنان غایب']], ['دیروز', ['اخطار به یک مدیر', 'یادآوری جلسه‌ی پنجشنبه']], ['این هفته', ['تحلیل رتبه‌بندی ماه', 'پیام به همه‌ی مدیرها']]].forEach(function (g, gi) {
      list.append(el('h4', null, g[0]));
      g[1].forEach(function (t, i) {
        var b = el('button', 'rg-it' + (gi === 0 && i === 0 ? ' rg-on' : ''), t); b.type = 'button';
        b.onclick = function () {
          list.querySelectorAll('.rg-it').forEach(function (x) { x.classList.remove('rg-on'); }); b.classList.add('rg-on'); drawer(false);
          clearTimeout(replyT); busy = false; charge(false); feed.replaceChildren(); user(t);
          ai('این گفتگوی قبلیه؛ توی نسخه‌ی واقعی پیام‌ها از تاریخچه‌ی ذخیره‌شده بارگذاری می‌شن.');
        };
        list.append(b);
      });
    });
    dr.append(dh, newc, list);
    sp = el('div', 'rg-sp');
    app.append(head, feed, form, scrim, dr, sp);
    root.replaceChildren(app);

    form.onsubmit = function (e) { e.preventDefault(); send(inp.value); };
    inp.addEventListener('input', function () { goBtn.disabled = busy || !inp.value.trim(); inp.style.height = 'auto'; inp.style.height = Math.min(inp.scrollHeight, 120) + 'px'; });
    inp.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey && matchMedia('(hover:hover)').matches) { e.preventDefault(); send(inp.value); } });
    hist.onclick = function () { drawer(true); }; scrim.onclick = function () { drawer(false); };
    neu.onclick = fresh; newc.onclick = fresh; back.onclick = function () { api.back(); };

    /* نگه‌داشتنِ جزیره → تنظیمات. متنِ جزیره هرگز سلکت/کپی نمی‌شود:
       user-select:none (CSS) + جلوگیری از selectstart/contextmenu/dragstart + touchstart (iOS callout) + پاک‌کردنِ هر سلکشن */
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
    window.addEventListener('resize', function () { fit(); if (isOpen) down(); });
    if (doc.fonts && doc.fonts.ready) doc.fonts.ready.then(fit);
    hero();
    built = true;
  }

  var api = {
    mount: function (r, c) { root = r; ctx = c; realRole = c.role || 'pishva'; if (!built) role = realRole; },
    open: function () {
      if (!built) build();
      isOpen = true; tipOff();
      /* نقشِ واقعی؛ در نسخه‌ی نمایشی فقط مدیر ارشد می‌تواند نقش را عوض کند */
      realRole = ctx.role || realRole; if (!ctx.preview || realRole !== 'pishva') role = realRole;
      requestAnimationFrame(function () {
        fit(); down();
        var f = doc.fonts && doc.fonts.load ? doc.fonts.load("19px 'Noto Nastaliq Urdu'", 'رهگشا') : null;
        if (f && f.then) f.then(fit, fit);
        var seen = false; try { seen = !!ctx.LS.get('hub:rg:tip'); } catch (e) {}
        if (!seen) app.append(el('div', 'rg-tip', 'برای تنظیمات رهگشا، جزیره را نگه دار'));
      });
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
