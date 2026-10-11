"""
لایه سازگاری بین کد فعلی (که با سینتکس aiosqlite نوشته شده) و Turso.
هدف: بدون تغییر دادن منطق فایل database.py، فقط دیتابیس زیرینش عوض بشه.

فقط کافیه در بالای database.py این خط عوض بشه:
    import aiosqlite   ->   import turso_db as aiosqlite

بقیه کد (aiosqlite.connect, db.execute, db.row_factory, cur.fetchall و ...)
دقیقاً همون‌طور که هست کار می‌کنه.
"""

import base64
import logging
import os

import httpx

logger = logging.getLogger("turso_db")

_client = None


# ─── کلاینت مستقیم HTTP برای Turso (Hrana-over-HTTP v2) ────────────────
# قبلاً این‌جا از پکیج libsql_client استفاده می‌شد، ولی اون پکیج یک باگ
# شناخته‌شده داره: وقتی Turso به یک statement با خطا جواب می‌ده (مثلاً
# "no such table"، "no such column"، تعداد پارامتر اشتباه و ...)، پاسخ
# دیکشنری‌ای که برمی‌گرده {"type": "error", "error": {...}} هست، نه
# {"type": "ok", "response": {"result": ...}}. libsql_client به‌جای چک
# کردن "type" مستقیم می‌ره سراغ response["result"] که چون اصلاً وجود
# نداره، یک KeyError('result') خام پرت می‌کنه و پیام واقعی خطای SQL
# (که دقیقاً همون چیزیه که برای دیباگ لازمه) گم می‌شه.
# با پیاده‌سازی مستقیمِ خودِ پروتکل (که مستنداته و ساده‌ست) این مشکل کلاً
# حل می‌شه: خطای واقعیِ Turso رو می‌خونیم و با پیام روشن بالا می‌بریم.
class _TursoHttpClient:
    def __init__(self, url: str, token: str):
        if url.startswith("libsql://"):
            url = "https://" + url[len("libsql://"):]
        self._base_url = url.rstrip("/")
        self._token = token
        self._http = httpx.AsyncClient(
            timeout=30.0,
            http2=True,
            # FIX (کندیِ حسِ‌شده روی دکمه‌هایی که چند کوئریِ مستقل رو هم‌زمان
            # می‌زنن، مثل منوی شطرنج یا پروفایلِ مدیر): با HTTP/1.1، هر
            # asyncio.gather یِ چند کوئری یعنی باز شدنِ چند اتصالِ TCP/TLS
            # جداگانه به Turso (هر کدوم با هندشیکِ خودش). با HTTP/2، همه‌ی
            # این درخواست‌های هم‌زمان روی یک اتصال مالتی‌پلکس می‌شن — یک
            # هندشیک، به‌جای چندتا.
            limits=httpx.Limits(max_keepalive_connections=20, keepalive_expiry=120.0),
        )

    async def _pipeline(self, requests):
        endpoint = f"{self._base_url}/v2/pipeline"
        headers = {"Authorization": f"Bearer {self._token}"}
        resp = await self._http.post(endpoint, json={"requests": requests}, headers=headers)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _check_pipeline_error(entry, sql, args):
        if entry.get("type") == "error":
            err = entry.get("error") or {}
            raise RuntimeError(
                f"Turso SQL error: {err.get('message', 'unknown error')} "
                f"(code={err.get('code')}) | sql={sql!r} args={args!r}"
            )

    async def execute(self, sql: str, args=None):
        sql = _cur_sql(sql)   # چندسرور: جدول‌های سرورِ جاری
        args = list(args) if args is not None else []
        requests = [
            {"type": "execute", "stmt": {"sql": sql, "args": [_encode_value(a) for a in args]}},
            {"type": "close"},
        ]
        data = await self._pipeline(requests)
        results = data.get("results") or []
        if not results:
            raise RuntimeError(f"Turso: empty pipeline response | sql={sql!r} args={args!r}")
        entry = results[0]
        self._check_pipeline_error(entry, sql, args)
        response = entry.get("response") or {}
        result = response.get("result")
        if result is None:
            raise RuntimeError(
                f"Turso: unexpected response shape | sql={sql!r} args={args!r} | raw={entry!r}"
            )
        return _to_exec_result(result)

    async def execute_pipeline(self, statements):
        """مثلِ batch() ولی برخلافش نتیجه‌ی هرکدوم از کوئری‌ها رو هم برمی‌گردونه
        (batch() فقط برای نوشتن‌هاست و نتیجه رو دور می‌ریزه). همه‌ی statements
        با یک درخواستِ شبکه‌ی واحد اجرا می‌شن — یعنی N تا کوئری = ۱ رفت‌وبرگشت،
        نه N تا. برای صفحاتی مثل خوش‌آمدگویی که چند تا SELECT مستقل و «حتماً
        تازه» (بدون کش) لازم دارن، همین یکی‌کردنِ درخواست‌ها منشأِ اصلیِ کندیه."""
        statements = [(_cur_sql(q), a) for q, a in statements]   # چندسرور
        requests = [
            {"type": "execute", "stmt": {"sql": sql, "args": [_encode_value(a) for a in (args or [])]}}
            for sql, args in statements
        ]
        requests.append({"type": "close"})
        data = await self._pipeline(requests)
        results = data.get("results") or []
        out = []
        for (sql, args), entry in zip(statements, results):
            self._check_pipeline_error(entry, sql, args)
            response = entry.get("response") or {}
            result = response.get("result")
            if result is None:
                raise RuntimeError(
                    f"Turso: unexpected response shape | sql={sql!r} args={args!r} | raw={entry!r}"
                )
            out.append(_to_exec_result(result))
        return out

    async def batch(self, statements):
        """statements: list of (sql, args) tuples، همه با یک درخواست شبکه اجرا می‌شن."""
        statements = [(_cur_sql(q), a) for q, a in statements]   # چندسرور
        requests = [
            {"type": "execute", "stmt": {"sql": sql, "args": [_encode_value(a) for a in (args or [])]}}
            for sql, args in statements
        ]
        requests.append({"type": "close"})
        data = await self._pipeline(requests)
        results = data.get("results") or []
        for (sql, args), entry in zip(statements, results):
            self._check_pipeline_error(entry, sql, args)


def _encode_value(v):
    """پایتون -> فرمت مقدارِ تایپ‌دارِ Hrana."""
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, (bytes, bytearray)):
        return {"type": "blob", "base64": base64.b64encode(bytes(v)).decode("ascii")}
    return {"type": "text", "value": str(v)}


def _decode_value(v):
    """مقدارِ تایپ‌دارِ Hrana -> پایتون."""
    t = v.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(v["value"])
    if t == "float":
        return float(v["value"])
    if t == "text":
        return v["value"]
    if t == "blob":
        return base64.b64decode(v.get("base64") or v.get("value") or "")
    return v.get("value")


class _ExecResult:
    def __init__(self, columns, rows, last_insert_rowid):
        self.columns = columns
        self.rows = rows
        self.last_insert_rowid = last_insert_rowid


def _to_exec_result(result: dict) -> _ExecResult:
    cols = [c.get("name") for c in (result.get("cols") or [])]
    rows = [[_decode_value(v) for v in row] for row in (result.get("rows") or [])]
    last_insert_rowid = result.get("last_insert_rowid")
    if last_insert_rowid is not None:
        last_insert_rowid = int(last_insert_rowid)
    return _ExecResult(cols, rows, last_insert_rowid)


# ─── چندسرور: «همه‌ی سرورها در یک اکانت/یک دیتابیسِ Turso، در بخش‌های جدا» ───
# سرور ۱ (اصلی) جدول‌های همیشگی را دارد. سرور N (۲..۶) مجموعه‌ی جداگانه‌ی جدول‌هایی با پیشوندِ «sN_» دارد
# (s2_players, s2_admins, ...). هیچ جدولی بینِ سرورها مشترک نیست؛ پس داده قاطی نمی‌شود، و فقط یک
# اکانت/توکن لازم است (هیچ متغیرِ محیطیِ اضافه‌ای نمی‌خواهد).
# بازنویسیِ SQL در پایین‌ترین لایه (execute/pipeline/batch) انجام می‌شود، پس همه‌ی کدِ پروژه
# بدونِ تغییر روی «سرورِ جاری» (srvctx) کار می‌کند.
import re as _re

_STR_RE = _re.compile(r"'(?:[^']|'')*'")
_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
# بعد از این کلمه‌ها نامِ جدول می‌آید (نامِ تابع مثل json_each( برای FROM/JOIN کنار گذاشته می‌شود)
_RE_FROMJOIN = _re.compile(r"\b(FROM|JOIN)(\s+)(" + _NAME + r")(?!\w)(?!\s*\()", _re.I)
_RE_INTO = _re.compile(r"\b(INTO)(\s+)(" + _NAME + r")(?!\w)", _re.I)
_RE_UPDATE = _re.compile(r"\b(UPDATE(?:\s+OR\s+\w+)?)(\s+)(" + _NAME + r")(?!\w)", _re.I)
_RE_TABLE = _re.compile(r"\b(TABLE(?:\s+IF\s+(?:NOT\s+)?EXISTS)?)(\s+)(" + _NAME + r")(?!\w)", _re.I)
_RE_REFS = _re.compile(r"\b(REFERENCES)(\s+)(" + _NAME + r")(?!\w)", _re.I)
_RE_INDEX = _re.compile(r"\b(INDEX(?:\s+IF\s+(?:NOT\s+)?EXISTS)?)(\s+)(" + _NAME + r")(\s+ON\s+)(" + _NAME + r")", _re.I)
_RE_DROPIDX = _re.compile(r"\b(DROP\s+INDEX(?:\s+IF\s+EXISTS)?)(\s+)(" + _NAME + r")", _re.I)
_RE_PRAGMA = _re.compile(r"\b(PRAGMA\s+\w+\s*\(\s*)(" + _NAME + r")(\s*\))", _re.I)
_SKIP = {"set", "select", "values", "default", "temp", "temporary", "if", "not", "exists", "where"}
_REWRITE_CACHE = {}


def _prefix_ok(name: str) -> bool:
    n = name.lower()
    return n not in _SKIP and not n.startswith("sqlite_") and not _re.match(r"s\d+_", n)


def _rewrite_segment(seg: str, pfx: str, tables: set) -> str:
    def sub3(m):
        name = m.group(3)
        if not _prefix_ok(name):
            return m.group(0)
        tables.add(name)
        return f"{m.group(1)}{m.group(2)}{pfx}{name}"
    for rx in (_RE_FROMJOIN, _RE_INTO, _RE_UPDATE, _RE_TABLE, _RE_REFS):
        seg = rx.sub(sub3, seg)

    def sub_idx(m):
        idx, tbl = m.group(3), m.group(5)
        if _prefix_ok(tbl):
            tables.add(tbl)
            tbl = pfx + tbl
        if _prefix_ok(idx):
            idx = pfx + idx
        return f"{m.group(1)}{m.group(2)}{idx}{m.group(4)}{tbl}"
    seg = _RE_INDEX.sub(sub_idx, seg)
    seg = _RE_DROPIDX.sub(lambda m: f"{m.group(1)}{m.group(2)}{pfx + m.group(3) if _prefix_ok(m.group(3)) else m.group(3)}", seg)

    def sub_pragma(m):
        return f"{m.group(1)}{pfx + m.group(2) if _prefix_ok(m.group(2)) else m.group(2)}{m.group(3)}"
    return _RE_PRAGMA.sub(sub_pragma, seg)


def _isolate_sql(sql: str, sid: int) -> str:
    """SQL را برای سرورِ sid بازنویسی می‌کند (نامِ همه‌ی جدول‌ها/ایندکس‌ها ← sN_نام). برای سرور اصلی دست‌نخورده."""
    if sid == 1 or not sql:
        return sql
    key = (sid, sql)
    hit = _REWRITE_CACHE.get(key)
    if hit is not None:
        return hit
    pfx = f"s{sid}_"
    parts, last, tables = [], 0, set()
    segs = []                                    # (متن، آیا رشته‌ی داخلِ کوتیشن است)
    for m in _STR_RE.finditer(sql):
        segs.append((sql[last:m.start()], False)); segs.append((m.group(0), True)); last = m.end()
    segs.append((sql[last:], False))
    out = [(_rewrite_segment(t, pfx, tables) if not q else t, q) for t, q in segs]
    # ارجاع‌های «جدول.ستون» برای جدول‌هایی که بی‌نام‌مستعار آمده‌اند
    if tables:
        qual = _re.compile(r"(?<![\w.])(" + "|".join(sorted(map(_re.escape, tables), key=len, reverse=True)) + r")\.(?=[\w*])")
        out = [(qual.sub(lambda m: pfx + m.group(1) + ".", t) if not q else t, q) for t, q in out]
    res = "".join(t for t, _q in out)
    if len(_REWRITE_CACHE) > 4000:
        _REWRITE_CACHE.clear()
    _REWRITE_CACHE[key] = res
    return res


_BLOCKED_SIDS = set()


def block_server(sid: int):
    """هر SQLی برای این سرور رد می‌شود (حین/بعد از پاک‌سازیِ نهایی)، تا هیچ تسکِ معوقی جدول را دوباره نسازد."""
    _BLOCKED_SIDS.add(int(sid))
    for k in [k for k in _REWRITE_CACHE if k[0] == int(sid)]:
        _REWRITE_CACHE.pop(k, None)


def unblock_server(sid: int):
    _BLOCKED_SIDS.discard(int(sid))


def _cur_sql(sql):
    import srvctx
    sid = srvctx.current()
    if sid in _BLOCKED_SIDS:
        raise RuntimeError(f"سرور {sid} پاک شده است؛ دسترسی به دیتابیسش مسدود است.")
    return _isolate_sql(sql, sid)


def _main_client():
    global _client
    if _client is None:
        url = os.getenv("TURSO_URL")
        token = os.getenv("TURSO_AUTH_TOKEN")
        if not url or not token:
            raise RuntimeError(
                "متغیرهای TURSO_URL و TURSO_AUTH_TOKEN تنظیم نشدن. "
                "این‌ها باید در Railway -> Variables اضافه شده باشن."
            )
        _client = _TursoHttpClient(url, token)
    return _client


def _get_client():
    return _main_client()


class Row:
    """فقط برای سازگاری با aiosqlite.Row - این کلاس عملاً استفاده نمیشه،
    چون ردیف‌ها همیشه هم با ایندکس و هم با اسم ستون قابل دسترسی هستن."""
    pass


class _ResultRow:
    def __init__(self, columns, values):
        self._columns = list(columns)
        self._values = list(values)

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._values[self._columns.index(key)]
        return self._values[key]

    def get(self, key, default=None):
        """سازگاری با کدی که ردیف رو مثل دیکشنری صدا می‌زنه (admin.get(...) و مشابه).
        sqlite3.Row/aiosqlite.Row هم این متد رو ندارن، ولی چون کد پروژه جاهای زیادی
        فرض کرده ردیف‌ها دیکشنری‌ان، این‌جا اضافه‌ش می‌کنیم تا دیگه با
        AttributeError کرش نکنه."""
        try:
            return self[key]
        except (ValueError, IndexError):
            return default

    def keys(self):
        return self._columns

    def __contains__(self, key):
        return key in self._columns

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return f"Row({dict(zip(self._columns, self._values))})"


class _Cursor:
    def __init__(self, result):
        self._rows = (
            [_ResultRow(result.columns, row) for row in result.rows]
            if result.rows else []
        )
        self._i = 0
        self.lastrowid = getattr(result, "last_insert_rowid", None)

    async def fetchone(self):
        if self._i < len(self._rows):
            row = self._rows[self._i]
            self._i += 1
            return row
        return None

    async def fetchall(self):
        rows = self._rows[self._i:]
        self._i = len(self._rows)
        return rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _split_sql_script(script: str):
    return [s.strip() for s in script.split(";") if s.strip()]


class _ExecuteAwaitable:
    """می‌تونه هم await بشه (await db.execute(...))
    هم به‌عنوان async context manager استفاده بشه (async with db.execute(...) as cur:)
    - دقیقاً مثل رفتار cursor در aiosqlite."""

    def __init__(self, query, params):
        self._query = query
        self._params = params
        self._cursor = None

    async def _run(self):
        if self._cursor is None:
            client = _get_client()
            try:
                if self._params is None:
                    result = await client.execute(self._query)
                else:
                    result = await client.execute(self._query, list(self._params))
            except Exception as exc:
                # _TursoHttpClient خودش پیام واقعیِ خطای Turso رو استخراج می‌کنه
                # (به‌جای KeyError('result') خامی که پکیج قدیمی libsql_client
                # می‌داد). این‌جا فقط برای اطمینان لاگ می‌کنیم و دوباره بالا
                # می‌بریم تا صدا‌زننده هم بتونه بگیردش.
                logger.error(
                    "Turso query failed | query=%r | params=%r | error=%r",
                    self._query, self._params, exc,
                )
                raise RuntimeError(
                    f"Turso query failed: {exc}\n"
                    f"query={self._query!r}\n"
                    f"params={self._params!r}"
                ) from exc
            self._cursor = _Cursor(result)
        return self._cursor

    def __await__(self):
        return self._run().__await__()

    async def __aenter__(self):
        return await self._run()

    async def __aexit__(self, *exc):
        return False


class _NoopAwaitable:
    """داخل یک batch برگردونده می‌شه چون کوئری واقعی هنوز اجرا نشده
    (تا پایان بلاک batch صف می‌مونه). await کردنش فقط یک cursor خالی می‌ده."""

    async def _run(self):
        return _Cursor(type("R", (), {"columns": [], "rows": [], "last_insert_rowid": None})())

    def __await__(self):
        return self._run().__await__()

    async def __aenter__(self):
        return await self._run()

    async def __aexit__(self, *exc):
        return False


class _Connection:
    def __init__(self):
        self.row_factory = None  # نادیده گرفته میشه، همیشه فعاله
        self._batch_stmts = None  # وقتی None نیست یعنی داخل یک batch هستیم

    def execute(self, query, params=None):
        if self._batch_stmts is not None:
            # داخل یک batch: به‌جای رفتن به شبکه، فقط صف می‌کنیم.
            # نتیجه‌ای برای برگردوندن نیست (batch فقط برای نوشتن‌هاست).
            self._batch_stmts.append((query, params))
            return _NoopAwaitable()
        return _ExecuteAwaitable(query, params)

    def batch(self):
        """استفاده: async with conn.batch(): چند تا execute پشت‌سرهم.
        همه‌ی کوئری‌ها با یک درخواست شبکه (نه یکی‌یکی) اجرا می‌شن.
        نکته: نتیجه‌ی هر execute داخل بلاک batch در دسترس نیست، چون
        همه با هم در انتهای بلاک اجرا می‌شن. برای کوئری‌هایی که فقط
        نوشتن هستن (insert/update/delete) استفاده کن، نه select."""
        return _BatchCM(self)

    async def executescript(self, script):
        client = _get_client()
        for stmt in _split_sql_script(script):
            try:
                await client.execute(stmt)
            except Exception as exc:
                logger.error("Turso executescript failed | stmt=%r | error=%r", stmt, exc)
                raise RuntimeError(f"Turso executescript failed: {exc!r}\nstmt={stmt!r}") from exc

    async def commit(self):
        # Turso هر دستور رو فوری ثبت می‌کنه، نیازی به commit جدا نیست
        pass

    async def close(self):
        pass


class _BatchCM:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        self._conn._batch_stmts = []
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        stmts = self._conn._batch_stmts
        self._conn._batch_stmts = None
        if exc_type is not None or not stmts:
            return False  # خطا شد یا چیزی صف نشده، چیزی رو اجرا نکن
        client = _get_client()
        batch_items = [(q, list(p) if p is not None else []) for q, p in stmts]
        try:
            await client.batch(batch_items)
        except Exception as exc:
            logger.error("Turso batch failed | stmts=%r | error=%r", stmts, exc)
            raise RuntimeError(f"Turso batch failed: {exc!r}\nstmts={stmts!r}") from exc
        return False


class _ConnectCM:
    async def __aenter__(self):
        return _Connection()

    async def __aexit__(self, *exc):
        return False


async def execute_pipeline(statements):
    """statements: لیستی از تاپل‌های (sql, args). همه با یک درخواستِ شبکه‌ی
    واحد به Turso اجرا می‌شن و لیستی از _Cursor (به همون شکلی که db.execute()
    عادی برمی‌گردونه) پس داده می‌شه — همون ترتیبِ ورودی."""
    client = _get_client()
    results = await client.execute_pipeline(statements)
    return [_Cursor(r) for r in results]


def connect(path=None):
    """امضای تابع مثل aiosqlite.connect(DB_PATH) هست ولی آرگومانش
    نادیده گرفته میشه چون آدرس دیتابیس از TURSO_URL خونده میشه."""
    return _ConnectCM()
