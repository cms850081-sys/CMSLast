/* پنل من — بخشِ «مدیریت» ۲: تیم‌ها، مخابرات، تقویم، مدیریتِ مدیران (فقط مدیر ارشد)،
   تنظیمات، وضعیتِ سیستم، لاگ، و خودِ صفحه‌ی تبِ «مدیریت». */
(function () {
  'use strict';
  var C, M, h, ic, hx, toast, api, row, riconEl, secTitle, kv, nn, avatar;
  var post, can, feat, push, root, back, refresh, lazy, swr, empty, field, input, select, btn, actBtn, toggleRow, chipsBar, confirmView, reasonView, fail, fa;

  /* ═══════════ تیم‌ها ═══════════ */
  function teamsView() {
    root('تیم‌ها', function () {
      return { body: lazy('/hub/api/teams', function (d) {
        return h('div', { class: 'group' }, d.teams.length ? d.teams.map(function (t) {
          return row({ title: t.name, sub: fa(t.members) + ' عضو • ' + fa(t.wins) + ' برد، ' + fa(t.losses) + ' باخت' + (t.warnings ? ' • ' + fa(t.warnings) + ' اخطار' : ''),
            tap: function () { teamDetail(t.id); } });
        }) : [empty('تیمی ثبت نشده.')]);
      }), foot: feat('team_create') ? btn('+ تیم جدید', '', teamCreate) : null };
    });
  }
  function teamCreate() {
    push('تیم جدید', function () {
      var n = input({ maxlength: 40, placeholder: 'نام تیم' }), s = input({ maxlength: 80, placeholder: 'شعار (اختیاری)' });
      return { body: h('div', null, field('نام تیم', n), field('شعار', s)),
        foot: actBtn('ساخت تیم', '', function () { return post('/hub/api/team/create', { name: n.value, slogan: s.value }); },
          function (r) { toast('تیم ساخته شد'); back(); teamDetail(r.id); }) };
    });
  }
  function teamDetail(id) {
    push('تیم', function () {
      return { body: lazy('/hub/api/team/' + id, function (t) {
        var w = h('div', null);
        w.append(h('div', { class: 'p-top' }, h('h3', { text: t.name }), t.slogan ? h('div', { class: 'p-sub', text: t.slogan }) : null));
        w.append(h('div', { class: 'stat3' },
          h('div', null, h('b', { class: 'num', text: t.stats.wins }), h('span', { text: 'برد' })),
          h('div', null, h('b', { class: 'num', text: t.stats.losses }), h('span', { text: 'باخت' })),
          h('div', null, h('b', { class: 'num' + (t.warnings ? ' bad' : ''), text: t.warnings }), h('span', { text: 'اخطار' }))));
        w.append(M.secTitle('اعضا', h('small', { class: 'num', text: t.members.length })));
        w.append(h('div', { class: 'group' }, t.members.length ? t.members.map(function (m) {
          return row({ lead: avatar(m.id, m.name, 'sm'), title: m.name + (t.captain_id === m.id ? ' 👑' : ''), sub: (m.cls ? 'کلاس ' + m.cls : '') + (m.status !== 'active' ? ' • غیرفعال' : ''),
            tap: function () {
              var acts = h('div', { class: 'group' },
                row({ lead: riconEl('crown', 'bg-amber'), title: t.captain_id === m.id ? 'برداشتن کاپیتانی' : 'انتخاب به‌عنوان کاپیتان', tap: function () {
                  post('/hub/api/team/' + id + '/edit', { captain_id: t.captain_id === m.id ? null : m.id }).then(function () { hx.ok(); back(); refresh(); }, fail); } }),
                row({ lead: riconEl('trash', 'bg-red'), title: 'خارج کردن از تیم', tap: function () {
                  post('/hub/api/team/' + id + '/remove-member', { player_id: m.id }).then(function () { hx.ok(); toast('از تیم خارج شد'); back(); refresh(); }, fail); } }));
              push(m.name, function () { return { body: acts }; });
            } });
        }) : [empty('عضوی نیست.')]));
        w.append(h('div', { class: 'group', style: 'margin-top:14px' },
          row({ lead: riconEl('plus', 'bg-green'), title: 'افزودن عضو', tap: function () {
            M.pickPlayer('افزودن عضو', function (p) {
              post('/hub/api/team/' + id + '/add-member', { player_id: p.id }).then(function () { hx.ok(); toast(p.name + ' اضافه شد'); refresh(); }, fail);
            }, { activeOnly: true, exclude: t.members.map(function (m) { return m.id; }) });
          } }),
          row({ lead: riconEl('pencil', 'bg-blue'), title: 'ویرایش نام و شعار', tap: function () {
            push('ویرایش تیم', function () {
              var n = input({ maxlength: 40, value: t.name }), s = input({ maxlength: 80, value: t.slogan });
              return { body: h('div', null, field('نام', n), field('شعار', s)),
                foot: actBtn('ذخیره', '', function () { return post('/hub/api/team/' + id + '/edit', { name: n.value, slogan: s.value }); }, function () { toast('ذخیره شد'); back(); back(); teamDetail(id); }) };
            });
          } }),
          can('player_warn') ? row({ lead: riconEl('alert', 'bg-amber'), title: 'اخطار به تیم', tap: function () {
            reasonView('اخطار به تیم', 'دلیل اخطار', '', function (r) { return post('/hub/api/team/' + id + '/warn', { reason: r }); }, function () { toast('اخطار ثبت شد'); refresh(); });
          } }) : null,
          row({ lead: riconEl('trash', 'bg-red'), title: 'حذف تیم', tap: function () {
            confirmView('حذف تیم', 'تیم «' + t.name + '» حذف می‌شود.', 'حذف کن', function () { return post('/hub/api/team/' + id + '/delete', { confirm: true }); },
              function () { toast('تیم حذف شد'); back(); back(); teamsView(); });
          } })));
        if (t.warn_log.length) w.append(M.secTitle('سابقه‌ی اخطار'), h('div', { class: 'group' }, t.warn_log.map(function (x) {
          return h('div', { class: 'mrow' }, h('span', { class: 'mres p', text: '!' }), h('div', { class: 'mtxt' }, h('b', { text: x.reason }), h('small', { text: x.by + ' • ' + x.at })));
        })));
        return w;
      }) };
    });
  }

  /* ═══════════ مخابرات ═══════════ */
  function commsView() {
    var st = { tab: 'inbox', data: null };
    root('مخابرات', function () {
      var box = h('div', null);
      function draw() {
        var d = st.data;
        var tabs = [['inbox', 'دریافتی'], ['sent', 'ارسالی'], ['new', 'پیام جدید'], ['ann', 'بیانیه‌ها'], ['news', 'اخبار']];
        var w = h('div', null, chipsBar(tabs, st.tab, function (k) { st.tab = k; draw(); }));
        if (st.tab === 'inbox') {
          w.append(d.inbox.length ? h('div', { class: 'group', style: 'margin-top:8px' }, d.inbox.map(function (m) {
            return row({ title: m.from + (m.read ? '' : ' •'), sub: m.text, hot: !m.read, end: h('small', { text: m.at.slice(5) }),
              tap: function () {
                push('پیام از ' + m.from, function () { return { body: h('div', { class: 'group' }, h('div', { class: 'text-block', text: m.text }), kv('زمان', m.at)) }; });
                if (!m.read) post('/hub/api/comms/read', { id: m.id }).then(function () { m.read = true; }, function () {});
              } });
          })) : empty('پیامی دریافت نشده.'));
        } else if (st.tab === 'sent') {
          w.append(d.sent.length ? h('div', { class: 'group', style: 'margin-top:8px' }, d.sent.map(function (m) {
            return row({ title: 'به ' + m.to, sub: m.text, end: h('small', { text: (m.read ? '✓ ' : '') + m.at.slice(5) }),
              tap: function () {
                confirmView('پیام ارسالی', m.text + '\n\nحذف از تاریخچه‌ی شما (و از چتِ گیرنده).', 'حذف پیام', function () { return post('/hub/api/comms/delete-sent', { id: m.id }); },
                  function () { toast('حذف شد'); load(); });
              } });
          })) : empty('پیامی نفرستاده‌اید.'));
        } else if (st.tab === 'new') {
          var sel = select(d.recipients.map(function (r) { return [r.id, r.name + ' — ' + r.role]; }), d.recipients.length ? d.recipients[0].id : '');
          var t = h('textarea', { class: 'inp', rows: 5, maxlength: 1000, placeholder: 'متن پیام…' });
          w.append(h('div', { style: 'margin-top:10px' }, d.recipients.length ? field('گیرنده', sel) : empty('گیرنده‌ای نیست.'), field('پیام', t),
            h('div', { style: 'margin:0 16px' }, actBtn('ارسال پیام', '', function () { return post('/hub/api/comms/send', { to: +sel.value, text: t.value }); },
              function () { toast('پیام ارسال شد'); t.value = ''; load(); }))));
        } else {
          var list = st.tab === 'ann' ? d.announcements : d.news;
          w.append(list.length ? h('div', { class: 'group', style: 'margin-top:8px' }, list.map(function (m) {
            return row({ title: m.text.slice(0, 70), sub: m.at, tap: function () { push(st.tab === 'ann' ? 'بیانیه' : 'خبر', function () { return { body: h('div', { class: 'group' }, h('div', { class: 'text-block', text: m.text })) }; }); } });
          })) : empty('موردی ثبت نشده.'));
          if (d.can_broadcast) {
            var bt = h('textarea', { class: 'inp', rows: 4, maxlength: 3000, placeholder: st.tab === 'ann' ? 'متن بیانیه‌ی رسمی…' : 'متن خبر فوری…' });
            w.append(M.secTitle(st.tab === 'ann' ? 'ارسال بیانیه' : 'ارسال خبر فوری'), field('متن', bt),
              h('div', { style: 'margin:0 16px' }, actBtn('ارسال برای همه', '', function () {
                if (!bt.value.trim()) { var e = new Error('x'); e.data = { message: 'متن را بنویسید.' }; throw e; }
                return post(st.tab === 'ann' ? '/hub/api/comms/announce' : '/hub/api/comms/news', { text: bt.value });
              }, function () { toast('ارسال شد'); bt.value = ''; load(); })));
          }
        }
        box.replaceChildren(w);
      }
      function load() {
        swr('/hub/api/comms/overview', function (d) { st.data = d; draw(); }, function () { box.replaceChildren(M.errBox(load)); });
      }
      box.append(M.spin()); load();
      return { body: box };
    });
  }

  /* ═══════════ تقویم ═══════════ */
  var WD = ['ش', 'ی', 'د', 'س', 'چ', 'پ', 'ج'];
  function calendarView() {
    var now = null;
    root('تقویم', function () {
      var box = h('div', null);
      var st = { tab: 'month', y: null, m: null };
      function drawMonth() {
        box.replaceChildren(M.spin());
        var q = st.y ? '?y=' + st.y + '&m=' + st.m : '';
        swr('/hub/api/calendar/month' + q, function (g) {
          st.y = g.year; st.m = g.month;
          var grid = h('div', { class: 'cal' });
          WD.forEach(function (x, i) { grid.append(h('span', { class: 'cal-h' + (i === 6 ? ' fri' : ''), text: x })); });
          for (var i = 0; i < g.first_wd; i++) grid.append(h('span', { class: 'cal-e' }));
          g.days.forEach(function (d) {
            var cls = 'cal-d' + (d.holiday ? ' hol' : '') + (d.today ? ' today' : '') + (d.custom && d.custom.type === 'event' ? ' evt' : '') + (d.wd === 6 ? ' fri' : '');
            grid.append(h('button', { type: 'button', class: cls, onclick: function () { dayView(g, d); } }, h('b', { class: 'num', text: fa(d.d) }),
              (d.official.length || d.custom) ? h('i') : null));
          });
          function nav(dm) { var y = g.year, m = g.month + dm; if (m < 1) { m = 12; y--; } if (m > 12) { m = 1; y++; } st.y = y; st.m = m; drawMonth(); }
          box.replaceChildren(h('div', { class: 'cal-head' },
            h('button', { type: 'button', class: 'chip', text: '›', 'aria-label': 'ماه بعد', onclick: function () { nav(1); } }),
            h('b', { text: g.month_name + ' ' + fa(g.year) }),
            h('button', { type: 'button', class: 'chip', text: '‹', 'aria-label': 'ماه قبل', onclick: function () { nav(-1); } })),
            grid, h('div', { class: 'cal-legend' }, h('span', null, h('i', { class: 'lg hol' }), 'تعطیل'), h('span', null, h('i', { class: 'lg evt' }), 'رویداد مدرسه'), h('span', null, h('i', { class: 'lg today' }), 'امروز')));
        }, function () { box.replaceChildren(M.errBox(drawMonth)); });
      }
      function dayView(g, d) {
        push(fa(d.d) + ' ' + g.month_name + ' ' + fa(g.year), function () {
          var w = h('div', null);
          var kids = [kv('روز', ['شنبه', 'یکشنبه', 'دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه'][d.wd])];
          if (d.wd === 6) kids.push(kv('وضعیت', 'جمعه — تعطیل'));
          d.official.forEach(function (t) { kids.push(kv('تعطیلی رسمی', t)); });
          if (d.custom) kids.push(kv(d.custom.type === 'holiday' ? 'تعطیلیِ ثبت‌شده' : 'رویداد', d.custom.title));
          if (kids.length === 1) kids.push(kv('وضعیت', 'روز عادی'));
          w.append(h('div', { class: 'group' }, kids));
          var jd = g.year + '/' + ('0' + g.month).slice(-2) + '/' + ('0' + d.d).slice(-2);
          if (g.can_edit) {
            var t = input({ maxlength: 80, placeholder: 'عنوان (مثلاً امتحانات / تعطیلی مدرسه)', value: d.custom ? d.custom.title : '' });
            function save(type) { return function () { return post('/hub/api/calendar/day', { jdate: jd, day_type: type, title: t.value }); }; }
            function done(msg) { return function () { toast(msg); back(); drawMonth(); }; }
            w.append(M.secTitle('ثبت برای مدرسه'), field('عنوان', t),
              h('div', { class: 'foot-btns', style: 'padding:0 16px' }, actBtn('ثبت تعطیلی', 'danger', save('holiday'), done('تعطیلی ثبت شد')), actBtn('ثبت رویداد', '', save('event'), done('رویداد ثبت شد'))),
              d.custom ? h('div', { style: 'margin:10px 16px 0' }, actBtn('پاک کردن ثبتِ مدرسه', 'soft', save('clear'), done('پاک شد'))) : null);
          }
          return { body: w };
        });
      }
      function drawHolidays() {
        box.replaceChildren(M.spin());
        swr('/hub/api/calendar/holidays', function (d) {
          var w = h('div', null);
          w.append(h('div', { class: 'note', text: d.note }));
          var year = null, month = null, grp = null;
          d.items.forEach(function (it) {
            if (it.jy !== year) { year = it.jy; month = null; w.append(h('div', { class: 'sec-title' }, h('span', { text: 'سال ' + fa(year) }), h('small', { class: 'num', text: d.items.filter(function (x) { return x.jy === year; }).length + ' تعطیلی' }))); }
            if (it.jm !== month) { month = it.jm; w.append(h('div', { class: 'sec-sub', text: it.month_name })); grp = h('div', { class: 'group' }); w.append(grp); }
            grp.append(h('div', { class: 'mrow' }, h('span', { class: 'mres ' + (it.on_friday ? 'p' : 'l'), text: fa(it.jd) }),
              h('div', { class: 'mtxt' }, h('b', { text: it.titles.join(' + ') }), h('small', { text: it.wd_name + (it.on_friday ? ' (مصادف با جمعه)' : '') }))));
          });
          box.replaceChildren(w);
        }, function () { box.replaceChildren(M.errBox(drawHolidays)); });
      }
      var wrap = h('div', null);
      function draw() {
        wrap.replaceChildren(chipsBar([['month', 'ماهانه'], ['hol', 'تعطیلی‌های ۳ سال آینده']], st.tab, function (k) { st.tab = k; draw(); }), box);
        if (st.tab === 'month') drawMonth(); else drawHolidays();
      }
      draw();
      return { body: wrap };
    });
  }

  /* ═══════════ مدیران (فقط مدیر ارشد) ═══════════ */
  var ROLES = [['tournament_manager', 'مسئول مسابقات'], ['security_manager', 'مسئول انتظامات']];
  function adminsView() {
    root('مدیریت مدیران', function () {
      return { body: lazy('/hub/api/admin/list', function (d) {
        return h('div', { class: 'group' }, d.admins.length ? d.admins.map(function (a) {
          return row({ lead: avatar(a.id, a.name, 'sm'), title: a.name, sub: a.role_label + (a.active ? '' : ' • غیرفعال') + (a.warnings ? ' • ' + fa(a.warnings) + ' اخطار' : ''),
            tap: function () { adminDetail(a, d); } });
        }) : [empty('مدیری ثبت نشده.')]);
      }), foot: btn('+ افزودن مدیر', '', adminCreate) };
    });
  }
  function adminCreate() {
    push('افزودن مدیر', function () {
      var id = input({ type: 'number', inputmode: 'numeric', placeholder: 'آی‌دی عددی تلگرام' }), n = input({ maxlength: 40, placeholder: 'نام' });
      var r = select(ROLES, 'tournament_manager'), u = input({ maxlength: 40, placeholder: 'یوزرنیم (اختیاری)' });
      return { body: h('div', null, field('آی‌دی عددی تلگرام', id, 'مثلاً 123456789'), field('نام', n), field('نقش', r), field('یوزرنیم', u)),
        foot: actBtn('افزودن', '', function () { return post('/hub/api/admin/create', { telegram_id: +id.value, name: n.value, role: r.value, username: u.value }); },
          function () { toast('مدیر اضافه شد'); back(); refresh(); }) };
    });
  }
  function adminDetail(a0, meta) {
    var a = a0;
    push(a.name, function () {
      var w = h('div', null);
      function upd(r) { if (r && r.admin) { a = r.admin; refresh(); } }
      w.append(h('div', { class: 'p-top' }, avatar(a.id, a.name, 'lg'), h('h3', { text: a.name }),
        h('div', { class: 'p-sub', text: a.role_label + (a.username ? ' • @' + a.username : '') }),
        h('div', { class: 'tags' }, h('span', { class: 'badge ' + (a.active ? 'elite' : 'off'), text: a.active ? 'فعال' : 'غیرفعال' }),
          a.warnings ? h('span', { class: 'badge special', text: fa(a.warnings) + ' اخطار' }) : null)));
      w.append(h('div', { class: 'group' }, kv('عضویت', a.joined || '—'), kv('آخرین فعالیت', a.last_active || '—')));

      /* قابلیت‌های پنل من — دقیقاً همان‌ها که سرور چک می‌کند */
      w.append(M.secTitle('قابلیت‌ها در پنل من', h('small', { text: 'برای همین مدیر' })));
      var groups = {};
      meta.caps_meta.forEach(function (c) { (groups[c.group] = groups[c.group] || []).push(c); });
      Object.keys(groups).forEach(function (g) {
        var gr = h('div', { class: 'group' });
        groups[g].forEach(function (c) {
          var isOver = Object.prototype.hasOwnProperty.call(a.overrides, c.key);
          gr.append(toggleRow(c.label, !!a.caps[c.key], function (v) {
            return post('/hub/api/admin/' + a.id + '/caps', { caps: (function () { var o = {}; o[c.key] = v; return o; })() }).then(function (r) { a = r.admin; return v; });
          }, isOver ? (a.caps[c.key] ? 'تنظیمِ دستی: روشن' : 'تنظیمِ دستی: خاموش') : 'پیش‌فرضِ نقش'));
        });
        w.append(h('div', { class: 'sec-sub', text: g }), gr);
      });
      var SM = { 'default': ['تنظیمِ کلی', 'default'], direct: ['مستقیم و خودکار', 'direct'], approval: ['با تأییدِ مدیر ارشد', 'approval'] };
      var SM_NEXT = { 'default': 'direct', direct: 'approval', approval: 'default' };
      w.append(M.secTitle('ثبت با عکس'), h('div', { class: 'group' }, row({ title: 'حالتِ ثبت برای این مدیر', sub: SM[a.scan_mode || 'default'][0] + ' — برای تغییر بزنید',
        tap: function () { post('/hub/api/admin/' + a.id + '/scan-mode', { mode: SM_NEXT[a.scan_mode || 'default'] }).then(function (r) { hx.ok(); upd(r); }, fail); } })),
        h('div', { class: 'sec-sub', text: 'روشن/خاموش‌بودنِ خودِ قابلیت را در «مسابقات ← ثبت با عکس» بالا تنظیم کنید.' }));
      w.append(h('div', { style: 'margin:10px 16px' }, actBtn('بازگشت همه به پیش‌فرضِ نقش', 'soft', function () { return post('/hub/api/admin/' + a.id + '/caps', { reset: true }); }, function (r) { toast('پیش‌فرض برگشت'); upd(r); })));

      w.append(M.secTitle('دسترسی‌های ربات'));
      var lg = h('div', { class: 'group' });
      meta.legacy_meta.forEach(function (p) {
        lg.append(toggleRow(p.label, !!a.perms[p.key], function (v) { return post('/hub/api/admin/' + a.id + '/perm', { perm: p.key, value: v }).then(function (r) { a = r.admin; return v; }); }));
      });
      w.append(lg);

      var role = select(ROLES, a.role);
      w.append(M.secTitle('مدیریت'), field('نقش', role),
        h('div', { style: 'margin:0 16px 10px' }, actBtn('ذخیره‌ی نقش', 'soft', function () { return post('/hub/api/admin/' + a.id + '/role', { role: role.value }); }, function (r) { toast('نقش تغییر کرد'); upd(r); })),
        h('div', { class: 'group' },
          row({ lead: riconEl('pencil', 'bg-blue'), title: 'تغییر نام نمایشی', tap: function () {
            push('تغییر نام', function () { var n = input({ maxlength: 40, value: a.name }); return { body: field('نام', n), foot: actBtn('ذخیره', '', function () { return post('/hub/api/admin/' + a.id + '/rename', { name: n.value }); }, function () { toast('ذخیره شد'); back(); back(); adminsView(); }) }; });
          } }),
          row({ lead: riconEl('alert', 'bg-amber'), title: 'ثبت اخطار', tap: function () { reasonView('اخطار برای ' + a.name, 'دلیل', '', function (t) { return post('/hub/api/admin/' + a.id + '/warn', { reason: t }); }, function (r) { toast('اخطار ثبت شد'); upd(r); }); } }),
          a.warnings ? row({ lead: riconEl('reset', 'bg-green'), title: 'پاک‌کردن اخطارها', tap: function () { post('/hub/api/admin/' + a.id + '/clear-warnings').then(function (r) { hx.ok(); toast('پاک شد'); upd(r); }, fail); } }) : null,
          row({ lead: riconEl('lock', a.active ? 'bg-red' : 'bg-green'), title: a.active ? 'اخراج مدیر' : 'بازگرداندن مدیر', tap: function () {
            confirmView(a.active ? 'اخراج مدیر' : 'بازگرداندن', a.active ? '«' + a.name + '» دسترسی‌اش قطع می‌شود.' : '«' + a.name + '» دوباره فعال می‌شود.', a.active ? 'اخراج کن' : 'فعال کن',
              function () { return post('/hub/api/admin/' + a.id + '/active', { active: !a.active }); }, function (r) { toast('انجام شد'); upd(r); }, a.active);
          } })));
      return { body: w };
    });
  }

  /* ═══════════ درخواست‌ها ═══════════ */
  function requestsView() {
    root('درخواست‌ها', function () {
      return { body: lazy('/hub/api/requests', function (d) {
        var w = h('div', null);
        function decide(url, ok, msg) { return function () { post(url).then(function () { hx.ok(); toast(msg); C.refreshBoot(); refresh(); }, fail); }; }
        w.append(M.secTitle('درخواست‌های دسترسی', h('small', { class: 'num', text: d.access.length })));
        w.append(h('div', null, d.access.length ? d.access.map(function (r) {
          return h('div', { class: 'group', style: 'margin-bottom:10px' }, kv('نام', r.name), kv('نقش', r.role), r.username ? kv('یوزرنیم', r.username) : null, r.message ? kv('پیام', r.message) : null, kv('زمان', r.at),
            h('div', { class: 'foot-btns', style: 'padding:10px 16px' }, btn('تأیید', '', decide('/hub/api/request/' + r.id + '/approve', 1, 'تأیید شد')), btn('رد', 'danger', decide('/hub/api/request/' + r.id + '/reject', 0, 'رد شد'))));
        }) : h('div', { class: 'group' }, empty('درخواستی نیست.'))));
        w.append(M.secTitle('درخواست‌های اخراج بازیکن', h('small', { class: 'num', text: d.kicks.length })));
        w.append(h('div', null, d.kicks.length ? d.kicks.map(function (r) {
          return h('div', { class: 'group', style: 'margin-bottom:10px' }, kv('درخواست‌دهنده', r.admin), kv('بازیکن', r.player), kv('زمان', r.at),
            h('div', { class: 'foot-btns', style: 'padding:10px 16px' }, btn('تأیید اخراج', 'danger', decide('/hub/api/kick-request/' + r.id + '/approve', 1, 'اخراج شد')), btn('رد', 'soft', decide('/hub/api/kick-request/' + r.id + '/reject', 0, 'رد شد'))));
        }) : h('div', { class: 'group' }, empty('درخواستِ اخراجی نیست.'))));
        var scans = d.scans || [];
        w.append(M.secTitle('ثبت نتیجه با عکس (در انتظارِ تأیید)', h('small', { class: 'num', text: scans.length })));
        w.append(h('div', null, scans.length ? scans.map(function (r) {
          var RESL = { white: 'برد سفید', black: 'برد سیاه', draw: 'تساوی' };
          var lines = r.rows.map(function (x, k) { return fa(k + 1) + '. ' + x.w + ' ⚔️ ' + x.b + ' — ' + (RESL[x.res] || 'بدون نتیجه'); });
          return h('div', { class: 'group', style: 'margin-bottom:10px' }, kv('درخواست‌دهنده', r.admin), kv('تعداد', fa(r.count) + ' مسابقه'), kv('تاریخ', r.date), kv('زمان', r.at),
            h('div', { class: 'empty', style: 'text-align:right;padding:6px 18px;white-space:pre-line;line-height:1.9', text: lines.join('\n') }),
            h('div', { class: 'foot-btns', style: 'padding:10px 16px' }, btn('تأیید و ثبت', '', decide('/hub/api/scan-request/' + r.id + '/approve', 1, 'تأیید شد و مسابقه‌ها ثبت شدند')), btn('رد', 'danger', decide('/hub/api/scan-request/' + r.id + '/reject', 0, 'رد شد؛ چیزی ثبت نشد'))));
        }) : h('div', { class: 'group' }, empty('درخواستِ ثبت با عکسی نیست.'))));
        return w;
      }) };
    });
  }

  /* ═══════════ تنظیمات و وضعیتِ سیستم ═══════════ */
  var STATUSES = [['normal', '🟢 نرمال'], ['bad', '🟡 بد'], ['danger', '🔴 خطرناک'], ['aps', '🪽 APS']];
  function settingsView() {
    root('تنظیمات', function () {
      return { body: lazy('/hub/api/settings', function (s) {
        var w = h('div', null);
        var groups = {};
        s.items.forEach(function (i) { (groups[i.group] = groups[i.group] || []).push(i); });
        Object.keys(groups).forEach(function (g) {
          var gr = h('div', { class: 'group' });
          groups[g].forEach(function (i) {
            gr.append(toggleRow(i.label, i.on, function () { return post('/hub/api/settings/toggle', { key: i.key }).then(function (r) { clearTimeout(window.__hubBootT); window.__hubBootT = setTimeout(function () { C.refreshBoot(); }, 1500); return r.items.filter(function (x) { return x.key === i.key; })[0].on; }); }));
          });
          w.append(M.secTitle(g), gr);
        });
        w.append(M.secTitle('ثبت با عکس'), h('div', { class: 'group' }, row({ title: 'حالتِ پیش‌فرضِ ثبت (برای مدیران)', sub: s.scan_default_mode === 'direct' ? 'مستقیم و خودکار' : 'با تأییدِ مدیر ارشد',
          tap: function () { post('/hub/api/settings/toggle', { key: 'scan_default_mode' }).then(function () { hx.ok(); refresh(); }, fail); } })),
          h('div', { class: 'sec-sub', text: 'هر مدیر را می‌توانید از صفحه‌ی خودش جدا تنظیم کنید. مدیر ارشد همیشه مستقیم ثبت می‌کند.' }));
        w.append(M.secTitle('نفراتِ برتر'), h('div', { class: 'group' }, row({ title: 'حالتِ انتخاب', sub: s.top_players_mode === 'manual' ? 'دستیِ مدیر ارشد' : 'خودکار (براساسِ امتیاز)',
          tap: function () { post('/hub/api/settings/toggle', { key: 'top_players_mode' }).then(function () { hx.ok(); refresh(); }, fail); } })));
        function txt(key, label, ph, hint) {
          var i = input({ value: s.texts[key], placeholder: ph || '' });
          return h('div', null, field(label, i, hint), h('div', { style: 'margin:-4px 16px 12px' }, actBtn('ذخیره', 'soft', function () { return post('/hub/api/settings/text', { key: key, value: i.value }); }, function () { toast('ذخیره شد'); })));
        }
        w.append(M.secTitle('گروه و کانال'), txt('announcement_group_id', 'آی‌دیِ گروهِ اعلانات', '-100…'), txt('announcement_channel_id', 'آی‌دیِ کانالِ اعلانات', '-100…'));
        w.append(M.secTitle('تقویم'), txt('hijri_offset', 'تصحیحِ تاریخِ قمری (روز)', '0', 'بینِ -۲ تا ۲؛ برای هم‌خوانی با رؤیتِ هلال'));
        w.append(M.secTitle('سیستم'), h('div', { class: 'group' }, row({ lead: riconEl('gear', 'bg-red'), title: 'وضعیتِ سیستم و تعمیر', sub: 'وضعیت، حالتِ تعمیر، آپدیت', tap: systemView })));
        return w;
      }) };
    });
  }
  function systemView() {
    push('وضعیتِ سیستم', function () {
      return { body: lazy('/hub/api/settings', function (s) {
        var w = h('div', null);
        w.append(h('div', { class: 'note', text: 'در حالت‌های تعمیر، آپدیت، خطرناک و APS (و وقتی ربات برای ادمین‌ها خاموش است) پنل و ربات برای همه‌ی مدیران بسته می‌شود؛ فقط شما دسترسی دارید.' }));
        w.append(M.secTitle('وضعیتِ سیستم'), chipsBar(STATUSES, s.status, function (k) {
          var label = STATUSES.filter(function (x) { return x[0] === k; })[0][1];
          confirmView('تغییر وضعیت', 'وضعیت به «' + label + '» تغییر می‌کند و برای مدیران اعلام می‌شود.', 'تغییر بده',
            function () { return post('/hub/api/system/status', { status: k }); }, function () { toast('وضعیت تغییر کرد'); C.refreshBoot(); refresh(); }, k === 'danger' || k === 'aps');
        }));
        var reason = input({ value: s.texts.repair_reason, placeholder: 'دلیل تعمیر (اختیاری)' });
        w.append(M.secTitle('حالتِ تعمیر'), field('دلیل', reason),
          h('div', { style: 'margin:0 16px 4px' }, s.repair
            ? actBtn('پایانِ تعمیر', '', function () { return post('/hub/api/system/repair', { on: false }); }, function () { toast('تعمیر پایان یافت'); refresh(); })
            : actBtn('فعال‌سازیِ حالتِ تعمیر', 'danger', function () { return post('/hub/api/system/repair', { on: true, reason: reason.value }); }, function () { toast('حالتِ تعمیر فعال شد'); refresh(); })));
        w.append(M.secTitle('آپدیت'), h('div', { class: 'group' }, toggleRow('حالتِ آپدیت (ربات برای ادمین‌ها بسته)', s.update, function (v) { return post('/hub/api/system/update', { on: v }).then(function () { return v; }); })));
        return w;
      }) };
    });
  }

  /* ═══════════ لاگ اقدامات ═══════════ */
  function logsView() {
    var st = { period: 'today', page: 0 };
    root('پیگیری اقدامات', function () {
      var box = h('div', null), bar = h('div', null);
      function draw() {
        bar.replaceChildren(chipsBar([['today', 'امروز'], ['week', 'هفته'], ['month', 'ماه'], ['all', 'همه']], st.period, function (k) { st.period = k; st.page = 0; draw(); }));
        box.replaceChildren(M.spin());
        swr('/hub/api/logs?period=' + st.period + '&page=' + st.page, function (d) {
          var w = h('div', null);
          w.append(d.rows.length ? h('div', { class: 'group' }, d.rows.map(function (r) {
            return h('div', { class: 'mrow' }, h('div', { class: 'mtxt' }, h('b', { style: 'white-space:normal', text: r.text || r.label }), h('small', { text: r.admin + ' • ' + r.label + ' • ' + r.at })));
          })) : empty('اقدامی ثبت نشده.'));
          if (d.pages > 1) w.append(h('div', { class: 'pager' },
            btn('‹ قبلی', 'soft', function () { if (st.page > 0) { st.page--; draw(); } }), h('span', { class: 'num', text: (d.page + 1) + ' / ' + d.pages }),
            btn('بعدی ›', 'soft', function () { if (st.page < d.pages - 1) { st.page++; draw(); } })));
          box.replaceChildren(w);
        }, function () { box.replaceChildren(M.errBox(draw)); });
      }
      draw();
      return { body: h('div', null, bar, box) };
    });
  }

  /* ═══════════ صفحه‌ی تبِ «مدیریت» ═══════════ */
  function renderTab() {
    var B = C.S.boot; if (!B) return;
    var tab = document.getElementById('tab-manage');
    var kids = [h('h1', { class: 'page-title', text: 'مدیریت' })];
    function grp(title, items) {
      items = items.filter(Boolean); if (!items.length) return;
      kids.push(M.secTitle(title), h('div', { class: 'group' }, items));
    }
    function R(cap, icon, bg, title, sub, fn, badge) {
      if (cap && !can(cap)) return null;
      return row({ lead: riconEl(icon, bg), title: title, sub: sub, tap: fn, end: badge ? h('span', { class: 'badge special num', text: badge }) : null });
    }
    grp('بازیکنان', [
      R('player_register', 'plus', 'bg-green', 'ثبت‌نام بازیکن', 'افزودنِ بازیکنِ جدید', M.registerPlayer),
      R('players_view', 'users', 'bg-blue', 'پنل بازیکنان', 'جستجو، مشاهده، اخطار، اخراج', function () { C.go('players', { f: 'all' }); }),
      R('classes', 'book', 'bg-violet', 'مدیریتِ کلاس‌ها', 'ساخت، تغییرِ نام، رنگ، حذف', M.classesView),
      feat('team_mode') ? R('teams', 'team', 'bg-teal', 'مدیریتِ تیم‌ها', 'تیم، اعضا، اخطار', teamsView) : null]);
    grp('مسابقات', [
      R('match_create', 'plus', 'bg-green', 'ثبتِ مسابقه', 'ساختِ یک مسابقه‌ی جدید', function () { M.createMatch(); }),
      (can('match_edit') || can('match_delete') || can('match_create')) ? R(null, 'trophy', 'bg-amber', 'مسابقه‌ها', 'ثبتِ نتیجه، ویرایش، حذف', M.matchesList) : null,
      R('match_scan', 'camera', 'bg-blue', 'ثبت نتیجه با عکس', 'عکسِ برگه ← بازبینی ← ثبت', function () { M.scanStart(); }),
      R('predictions', 'bolt', 'bg-violet', 'پیش‌بینی', 'احتمالِ پیروزی دو بازیکن', function () { M.predict(); }),
      R('elo', 'star', 'bg-amber', 'جدول Elo', 'رتبه‌بندیِ بازیکنان', M.eloBoard)]);
    grp('ارتباط و زمان', [
      R('comms', 'chat', 'bg-blue', 'مخابرات', 'پیام، بیانیه، اخبار', commsView),
      R('calendar', 'calendar', 'bg-red', 'تقویم و تعطیلی‌ها', 'تعطیلی‌های رسمیِ ۳ سالِ آینده', calendarView)]);
    if (can('admins_manage') || can('settings') || can('pishva_panel')) {
      var pend = C.S.pending || {};
      grp('مدیر ارشد', [
        R('admins_manage', 'shield', 'bg-red', 'مدیریتِ مدیران', 'نقش، دسترسی، اخطار، شخصی‌سازی', adminsView),
        R('pishva_panel', 'inbox', 'bg-amber', 'درخواست‌ها', 'دسترسی و اخراج', requestsView, pend.total ? String(pend.total) : null),
        R('settings', 'sliders', 'bg-violet', 'تنظیمات و وضعیتِ سیستم', 'همه‌ی سوییچ‌ها، تعمیر، APS', settingsView),
        R('pishva_panel', 'list', 'bg-blue', 'پیگیریِ اقدامات', 'لاگِ کاملِ مدیران', logsView)]);
    }
    tab.replaceChildren.apply(tab, kids);
    if (can('pishva_panel') && !C.S.pendingLoading) {
      C.S.pendingLoading = true;
      swr('/hub/api/requests', function (d) {
        C.S.pending = { total: d.access.length + d.kicks.length + (d.scans || []).length }; C.S.pendingLoading = false;
        if (C.cur() === 'manage') renderTab();
      }, function () { C.S.pendingLoading = false; });
    }
  }

  window.__HubManageP2 = { init: function (core, m) {
    C = core; M = m; h = core.h; ic = core.ic; hx = core.hx; toast = core.toast; api = core.api;
    row = core.row; riconEl = core.riconEl; secTitle = core.secTitle; kv = core.kv; nn = core.nn; avatar = core.avatar;
    post = m.post; can = m.can; feat = m.feat; push = m.push; root = m.root; back = m.back; refresh = m.refresh; lazy = m.lazy; swr = m.swr; empty = m.empty;
    field = m.field; input = m.input; select = m.select; btn = m.btn; actBtn = m.actBtn; toggleRow = m.toggleRow; chipsBar = m.chipsBar;
    confirmView = m.confirmView; reasonView = m.reasonView; fail = m.fail; fa = m.fa;
    M.secTitle = secTitle;
    return Object.assign(m, { renderTab: renderTab, teamsView: teamsView, commsView: commsView, calendarView: calendarView, adminsView: adminsView,
      requestsView: requestsView, settingsView: settingsView, logsView: logsView, systemView: systemView });
  } };
})();
