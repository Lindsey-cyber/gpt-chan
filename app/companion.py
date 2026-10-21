"""Durable, bounded companionship, using only verified incoming private chats."""
import asyncio
import hashlib
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

log = logging.getLogger(__name__)


def local_day(now, timezone):
    return datetime.fromtimestamp(now, ZoneInfo(timezone)).date().isoformat()


def due_event(user, now, attempted):
    """Pure scheduling decision; never infer Telegram presence or replay missed windows."""
    if not user['enabled'] or now < user['snooze_until'] or now - user['last_attempt'] < 7200:
        return None
    local = datetime.fromtimestamp(now, ZoneInfo(user['timezone']))
    day = local.date().isoformat()
    gap = now - user['last_inbound']
    minute = local.hour * 60 + local.minute
    if local.hour >= 23 or local.hour < 2:
        night = local.date() if local.hour >= 23 else local.date() - timedelta(days=1)
        key = f'{night}:night'
        return (key, 'night') if 90 <= gap <= 300 and key not in attempted else None
    if local.hour < 8 or gap < 1200:
        return None
    affection = user['last_affection']
    if affection and 1200 <= now - affection <= 3600 and f'{day}:affection' not in attempted:
        return f'{day}:affection', 'affection'
    seed = int(hashlib.sha256(f"{user['user_id']}:{day}".encode()).hexdigest()[:8], 16)
    for kind, start in (('morning', 8*60), ('afternoon', 14*60), ('evening', 21*60)):
        # A stable individual time inside each two-hour window; no catch-up outside it.
        target = start + seed % 60
        key = f'{day}:{kind}'
        if target <= minute < target + 60 and key not in attempted:
            if kind != 'morning' or user['day_greeted'] != day:
                return key, kind
    if 10 <= local.hour < 23 and gap >= 4*3600 + seed % 7201 and f'{day}:idle' not in attempted:
        return f'{day}:idle', 'idle'
    return None


class CompanionStore:
    def __init__(self, path):
        self.path = path

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.executescript('''
                CREATE TABLE IF NOT EXISTS companion_users (
                    user_id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL,
                    timezone TEXT NOT NULL, enabled INTEGER NOT NULL,
                    last_inbound REAL NOT NULL, last_affection REAL,
                    day_greeted TEXT, last_attempt REAL NOT NULL DEFAULT 0,
                    snooze_until REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS companion_events (
                    user_id INTEGER NOT NULL, event_key TEXT NOT NULL,
                    day TEXT NOT NULL, claimed_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'claimed',
                    PRIMARY KEY(user_id,event_key)
                );
            ''')
            await db.commit()

    async def activity(self, uid, chat_id, now, timezone, enabled, affection=False):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('''INSERT INTO companion_users(user_id,chat_id,timezone,enabled,last_inbound,last_affection)
                VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                chat_id=excluded.chat_id,last_inbound=excluded.last_inbound,
                last_affection=COALESCE(excluded.last_affection,companion_users.last_affection)''',
                (uid,chat_id,timezone,int(enabled),now,now if affection else None))
            await db.commit()

    async def users(self):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            return [dict(row) for row in await (await db.execute('SELECT * FROM companion_users')).fetchall()]

    async def configure(self, uid, *, enabled=None, timezone=None, snooze_until=None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('''UPDATE companion_users SET enabled=COALESCE(?,enabled),
                timezone=COALESCE(?,timezone),snooze_until=COALESCE(?,snooze_until) WHERE user_id=?''',
                (None if enabled is None else int(enabled),timezone,snooze_until,uid))
            await db.commit()

    async def greet(self, uid, day):
        async with aiosqlite.connect(self.path) as db:
            result = await db.execute('UPDATE companion_users SET day_greeted=? WHERE user_id=? AND (day_greeted IS NULL OR day_greeted!=?)', (day,uid,day))
            await db.commit()
            return bool(result.rowcount)

    async def attempted(self, uid, now):
        async with aiosqlite.connect(self.path) as db:
            rows = await (await db.execute('SELECT event_key FROM companion_events WHERE user_id=? AND claimed_at>?', (uid,now-3*86400))).fetchall()
            return {row[0] for row in rows}

    async def claim(self, uid, chat_id, key, day, now, cap):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('BEGIN IMMEDIATE')
            row = await (await db.execute('SELECT enabled,chat_id,last_attempt,snooze_until FROM companion_users WHERE user_id=?', (uid,))).fetchone()
            count = (await (await db.execute('SELECT COUNT(*) FROM companion_events WHERE user_id=? AND day=?', (uid,day))).fetchone())[0]
            if not row or not row[0] or row[1] != chat_id or now-row[2] < 7200 or now < row[3] or count >= cap:
                await db.rollback()
                return False
            result = await db.execute('INSERT OR IGNORE INTO companion_events(user_id,event_key,day,claimed_at) VALUES(?,?,?,?)', (uid,key,day,now))
            if not result.rowcount:
                await db.rollback()
                return False
            await db.execute('UPDATE companion_users SET last_attempt=? WHERE user_id=?', (now,uid))
            await db.commit()
            return True

    async def finish(self, uid, key, status):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('UPDATE companion_events SET status=? WHERE user_id=? AND event_key=?', (status,uid,key))
            await db.commit()


FALLBACKS = {
    'morning': '主人早上好～小白先把角凑过来，摸一下再去忙嘛♡',
    'afternoon': '主人在干嘛呀？小白发现一颗亮晶晶的小石头，决定先替它取个很厉害的名字。',
    'evening': '主人，今天过得怎么样呀？你的小龙把翅膀收好啦，给你留了个靠近的位置。',
    'idle': '哼，主人忙起来就把小白忘在一边啦？忙完记得来摸摸角，你的小龙有一点点想你。',
    'affection': '刚才被主人宠过的小白，现在连尾巴都神气起来啦♡',
    'night': '主人，刚才还在陪小白说话呀？已经很晚啦，摸一下角就乖乖去休息好不好～',
}


class Companion:
    def __init__(self, settings, state, store, brain, bot, locks):
        self.settings, self.state, self.store = settings, state, store
        self.brain, self.bot, self.locks = brain, bot, locks

    async def tick(self, now=None):
        now = time.time() if now is None else now
        for candidate in await self.state.users():
            uid = candidate['user_id']
            if uid not in self.settings.allowed_ids or not self.settings.proactive_enabled:
                continue
            async with self.locks.setdefault(uid, asyncio.Lock()):
                # Refresh after waiting for a foreground conversation to finish.
                user = next(u for u in await self.state.users() if u['user_id'] == uid)
                event = due_event(user, now, await self.state.attempted(uid, now))
                if not event:
                    continue
                key, kind = event
                day = local_day(now, user['timezone'])
                if not await self.state.claim(uid,user['chat_id'],key,day,now,self.settings.proactive_daily_limit):
                    continue
                try:
                    history = await self.store.history(uid,6,4000)
                    memories = await self.store.memories(uid,self.settings.memory_limit)
                    persona = await self.store.selected_persona(uid)
                    text = await self.brain.proactive(kind,history,memories,persona=persona)
                except Exception as exc:
                    log.warning('proactive_generation_fallback type=%s',type(exc).__name__)
                    persona = await self.store.selected_persona(uid)
                    text = FALLBACKS[kind] if persona == 'xiaobai' else FALLBACKS[kind].replace('小白','白龙姬')
                try:
                    await self.bot.send_message(user['chat_id'],text)
                except TelegramForbiddenError:
                    await self.state.configure(uid,enabled=False)
                    await self.state.finish(uid,key,'blocked')
                    continue
                except TelegramRetryAfter as exc:
                    await self.state.configure(uid,snooze_until=now+exc.retry_after)
                    await self.state.finish(uid,key,'rate_limited')
                    continue
                except Exception as exc:
                    # A network timeout may already have delivered. Never replay it.
                    await self.state.finish(uid,key,'unknown')
                    log.warning('proactive_delivery_unconfirmed type=%s',type(exc).__name__)
                    continue
                await self.state.finish(uid,key,'sent')
                await self.store.add_assistant(uid,text,self.settings.max_context_messages)
                if kind == 'morning':
                    await self.state.greet(uid,day)
                log.info('proactive_sent kind=%s',kind)

    async def run(self):
        log.info('proactive_scheduler_started daily_limit=%d timezone=%s',self.settings.proactive_daily_limit,self.settings.proactive_timezone)
        while True:
            try:
                await self.tick()
            except Exception as exc:
                log.error('proactive_tick_failed type=%s',type(exc).__name__)
            await asyncio.sleep(60)
