"""
update_processor.py — پردازشِ هم‌زمانِ آپدیت‌ها، با ترتیبِ تضمین‌شده برای «هر کاربر».

مشکل: python-telegram-bot به‌طورِ پیش‌فرض آپدیت‌ها را «یکی‌یکی» پردازش می‌کند
(concurrent_updates=False). یعنی تا یک هندلرِ کند تمام نشود، دکمه‌ی هیچ‌کس جواب
نمی‌دهد: یک کوئریِ کندِ Turso، تحلیلِ هوش مصنوعی، خواندنِ عکسِ برگه (تا ~۶۰ ثانیه!)،
ساختنِ بکاپ... و کلِ ربات «سکته» می‌کند. این دقیقاً همان «لگِ ناگهانیِ چند ثانیه‌ای
موقعِ سوییچ بین پنل‌ها»ست.

راه‌حل: آپدیتِ کاربرهای مختلف هم‌زمان اجرا شوند، ولی آپدیت‌های «یک کاربر» همچنان
پشتِ‌سرِهم (به‌ترتیبِ رسیدن) تا ConversationHandlerها و state ها به‌هم نریزند
(دو تپِ سریع روی یک دکمه دو هندلرِ هم‌زمان نمی‌سازد).
"""

import asyncio

from telegram.ext import BaseUpdateProcessor


class PerUserUpdateProcessor(BaseUpdateProcessor):
    def __init__(self, max_concurrent_updates: int = 64):
        super().__init__(max_concurrent_updates)
        self._locks = {}          # key -> [asyncio.Lock, تعدادِ منتظر/در حالِ اجرا]

    @staticmethod
    def _key(update):
        user = getattr(update, "effective_user", None)
        if user is not None:
            return ("u", user.id)
        chat = getattr(update, "effective_chat", None)
        if chat is not None:
            return ("c", chat.id)
        return None               # آپدیتِ بدونِ کاربر/چت (مثلاً poll): بدونِ قفل

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        self._locks.clear()

    async def do_process_update(self, update, coroutine) -> None:
        key = self._key(update)
        if key is None:
            await coroutine
            return
        entry = self._locks.get(key)
        if entry is None:
            entry = self._locks[key] = [asyncio.Lock(), 0]
        entry[1] += 1
        try:
            async with entry[0]:
                await coroutine
        finally:
            entry[1] -= 1
            if entry[1] <= 0:
                self._locks.pop(key, None)     # قفلِ کاربرهای بی‌کار نمی‌ماند (نشتِ حافظه نداریم)
