"""
srvctx.py — «سرورِ جاری» برای چندسرورِ ایزوله (بدون هیچ وابستگی به بقیه‌ی پروژه).

هر درخواست (آپدیتِ تلگرام / درخواستِ هاب / جابِ زمان‌بندی‌شده) در یک «سرور» اجرا می‌شود.
سرور ۱ = سرورِ اصلی (همین پروژه‌ی فعال). سرورهای ۲..۶ = دنیاهای جدا، هرکدام با دیتابیسِ کاملاً مستقل.

ContextVar برای هر تسکِ asyncio جداست؛ پس دو کاربر در دو سرورِ مختلف هرگز هم‌دیگر را نمی‌بینند.
هر ماژولی که بخواهد وضعیتِ در-حافظه نگه دارد، باید از PerServer استفاده کند تا بین سرورها قاطی نشود.
"""
import contextlib
import contextvars

MAIN_ID = 1
MAX_SERVERS = 6          # حداکثرِ کل سرورها (۱ اصلی + ۵ مجموعه)

_CUR = contextvars.ContextVar("cms_server_id", default=MAIN_ID)


def current() -> int:
    return _CUR.get()


def is_main() -> bool:
    return _CUR.get() == MAIN_ID


def set_current(sid: int):
    """سرورِ جاریِ همین تسک را عوض می‌کند (برای روتر؛ توکنِ reset برمی‌گرداند)."""
    return _CUR.set(int(sid))


def reset(token) -> None:
    try:
        _CUR.reset(token)
    except ValueError:      # توکن از Contextِ دیگری بود؛ بی‌خطر
        _CUR.set(MAIN_ID)


@contextlib.contextmanager
def use(sid: int):
    """with srvctx.use(2): ...  — هرچه داخلش اجرا شود روی سرور ۲ است."""
    token = _CUR.set(int(sid))
    try:
        yield
    finally:
        try:
            _CUR.reset(token)
        except ValueError:
            _CUR.set(MAIN_ID)


class PerServer:
    """دیکشنریِ جداگانه برای هر سرور، با همان APIِ dict (get / [] / pop / clear / ...).
    جایگزینِ بی‌دردسرِ هر dictِ سراسریِ ماژول که داده‌ی یک سرور را نگه می‌دارد."""

    def __init__(self, factory=dict):
        self._d = {}
        self._f = factory

    def _cur(self):
        sid = _CUR.get()
        d = self._d.get(sid)
        if d is None:
            d = self._d[sid] = self._f()
        return d

    def for_server(self, sid: int):
        d = self._d.get(int(sid))
        if d is None:
            d = self._d[int(sid)] = self._f()
        return d

    def drop_server(self, sid: int) -> None:
        self._d.pop(int(sid), None)

    # ---- رابطِ dict ----
    def get(self, k, default=None): return self._cur().get(k, default)
    def __getitem__(self, k): return self._cur()[k]
    def __setitem__(self, k, v): self._cur()[k] = v
    def __delitem__(self, k): del self._cur()[k]
    def __contains__(self, k): return k in self._cur()
    def __len__(self): return len(self._cur())
    def __iter__(self): return iter(list(self._cur()))
    def __bool__(self): return bool(self._cur())
    def pop(self, k, *a): return self._cur().pop(k, *a)
    def setdefault(self, k, v=None): return self._cur().setdefault(k, v)
    def clear(self): self._cur().clear()
    def keys(self): return self._cur().keys()
    def values(self): return self._cur().values()
    def items(self): return self._cur().items()
    def update(self, *a, **k): return self._cur().update(*a, **k)
    def copy(self): return self._cur().copy()


class PerServerSet(PerServer):
    def __init__(self):
        super().__init__(set)

    def add(self, x): self._cur().add(x)
    def discard(self, x): self._cur().discard(x)
    def remove(self, x): self._cur().remove(x)


class PerServerValue:
    """یک مقدارِ تکی (مثلاً پرچم) به‌ازای هر سرور."""

    def __init__(self, default=None):
        self._d = {}
        self._default = default

    def get(self):
        return self._d.get(_CUR.get(), self._default)

    def set(self, v):
        self._d[_CUR.get()] = v
