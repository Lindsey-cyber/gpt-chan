import aiosqlite
import sqlite3
from pathlib import Path
from uuid import uuid4
from app.image_state import ImageState

class Storage:
    def __init__(self, path: str):
        self.path = path

    async def init(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        for suffix in ('.pre-reliability.bak', '.pre-companion.bak', '.pre-chatflow.bak'):
            backup = Path(self.path + suffix)
            if Path(self.path).is_file() and not backup.exists():
                with sqlite3.connect(self.path) as source, sqlite3.connect(backup) as target:
                    source.backup(target)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS image_captions (user_id INTEGER PRIMARY KEY, sequence INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS persona_choices (user_id INTEGER PRIMARY KEY, persona TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS persona_moods (user_id INTEGER NOT NULL, persona TEXT NOT NULL, mood TEXT NOT NULL, remaining INTEGER NOT NULL, PRIMARY KEY(user_id,persona));
                CREATE TABLE IF NOT EXISTS user_counters (user_id INTEGER PRIMARY KEY, exchanges INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS image_requests (user_id INTEGER NOT NULL, created_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_image_requests_user ON image_requests(user_id,created_at);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id,id);
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    fact TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id,fact)
                );
                CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id,id);
                CREATE TABLE IF NOT EXISTS character_state (user_id INTEGER PRIMARY KEY, mood TEXT NOT NULL DEFAULT 'steady', remaining INTEGER NOT NULL DEFAULT 0);\n                CREATE TABLE IF NOT EXISTS image_tasks (user_id INTEGER PRIMARY KEY, prompt TEXT NOT NULL, status TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
            """)
            columns = await (await db.execute('PRAGMA table_info(image_tasks)')).fetchall()
            for name, sql_type in (('task_id', 'TEXT'), ('nsfw', 'INTEGER'), ('self_portrait', 'INTEGER')):
                if name not in {column[1] for column in columns}:
                    await db.execute(f'ALTER TABLE image_tasks ADD COLUMN {name} {sql_type}')
            await db.execute('UPDATE image_tasks SET status=? WHERE status=?',
                             (ImageState.INTERRUPTED, ImageState.GENERATING))
            await db.commit()

    async def history(self, uid: int, max_messages: int, max_chars: int):
        async with aiosqlite.connect(self.path) as db:
            rows = await (await db.execute('SELECT role,content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?', (uid,max_messages))).fetchall()
        result, size = [], 0
        for role, content in rows:
            if size + len(content) > max_chars:
                break
            result.append({'role':role, 'content':content})
            size += len(content)
        return list(reversed(result))

    async def add_exchange(self, uid: int, question: str, answer: str, max_messages: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('INSERT INTO user_counters(user_id,exchanges) VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET exchanges=exchanges+1', (uid,))
            await db.executemany('INSERT INTO messages(user_id,role,content) VALUES(?,?,?)',[(uid,'user',question),(uid,'assistant',answer)])
            await db.execute('DELETE FROM messages WHERE user_id=? AND id NOT IN (SELECT id FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?)',(uid,uid,max_messages))
            await db.commit()

    async def clear_history(self, uid: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('DELETE FROM messages WHERE user_id=?',(uid,))
            await db.execute('DELETE FROM character_state WHERE user_id=?',(uid,))
            await db.execute('DELETE FROM persona_moods WHERE user_id=?',(uid,))
            await db.execute('DELETE FROM image_tasks WHERE user_id=?',(uid,))
            await db.commit()

    async def memories(self, uid: int, limit: int):
        async with aiosqlite.connect(self.path) as db:
            return await (await db.execute('SELECT id,fact FROM memories WHERE user_id=? ORDER BY id DESC LIMIT ?',(uid,limit))).fetchall()

    async def save_memories(self, uid: int, facts: list[str], limit: int):
        async with aiosqlite.connect(self.path) as db:
            for fact in facts[:3]:
                fact=fact.strip()[:250]
                if fact:
                    await db.execute('INSERT OR IGNORE INTO memories(user_id,fact) VALUES(?,?)',(uid,fact))
            await db.execute('DELETE FROM memories WHERE user_id=? AND id NOT IN (SELECT id FROM memories WHERE user_id=? ORDER BY id DESC LIMIT ?)',(uid,uid,limit))
            await db.commit()

    async def forget(self, uid: int, memory_id: int | None):
        async with aiosqlite.connect(self.path) as db:
            if memory_id is None:
                await db.execute('DELETE FROM memories WHERE user_id=?',(uid,))
            else:
                await db.execute('DELETE FROM memories WHERE user_id=? AND id=?',(uid,memory_id))
            await db.commit()

    async def character_state(self, uid: int, persona="bailongji"):
        if persona != "bailongji":
            async with aiosqlite.connect(self.path) as db:
                row = await (await db.execute("SELECT mood,remaining FROM persona_moods WHERE user_id=? AND persona=?",(uid,persona))).fetchone()
            return row if row else ('steady',0)
        async with aiosqlite.connect(self.path) as db:
            row = await (await db.execute('SELECT mood,remaining FROM character_state WHERE user_id=?',(uid,))).fetchone()
        return row if row else ('steady',0)

    async def set_character_state(self, uid: int, mood: str, remaining: int, persona="bailongji"):
        if persona != "bailongji":
            async with aiosqlite.connect(self.path) as db:
                await db.execute("INSERT INTO persona_moods(user_id,persona,mood,remaining) VALUES(?,?,?,?) ON CONFLICT(user_id,persona) DO UPDATE SET mood=excluded.mood,remaining=excluded.remaining", (uid,persona,mood,max(0,remaining)))
                await db.commit()
            return
        async with aiosqlite.connect(self.path) as db:
            await db.execute('INSERT INTO character_state(user_id,mood,remaining) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET mood=excluded.mood,remaining=excluded.remaining',(uid,mood,max(0,remaining)))
            await db.commit()

    async def exchange_count(self, uid: int) -> int:
        async with aiosqlite.connect(self.path) as db:
            row = await (await db.execute("SELECT COUNT(*) FROM messages WHERE user_id=? AND role='user'",(uid,))).fetchone()
        return row[0]

    async def image_task(self, uid: int):
        async with aiosqlite.connect(self.path) as db:
            return await (await db.execute('SELECT prompt,status FROM image_tasks WHERE user_id=?',(uid,))).fetchone()

    async def set_image_task(self, uid: int, prompt: str, status: str):
        status = ImageState(status)
        async with aiosqlite.connect(self.path) as db:
            await db.execute('''INSERT INTO image_tasks(user_id,prompt,status,task_id) VALUES(?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET prompt=excluded.prompt,status=excluded.status,
                task_id=CASE WHEN excluded.status='generating' THEN excluded.task_id ELSE image_tasks.task_id END,
                nsfw=CASE WHEN excluded.status='generating' THEN NULL ELSE image_tasks.nsfw END,
                self_portrait=CASE WHEN excluded.status='generating' THEN NULL ELSE image_tasks.self_portrait END,
                updated_at=CURRENT_TIMESTAMP''', (uid,prompt,status,uuid4().hex))
            await db.commit()

    async def image_task_id(self, uid: int):
        async with aiosqlite.connect(self.path) as db:
            row = await (await db.execute('SELECT task_id FROM image_tasks WHERE user_id=?', (uid,))).fetchone()
        return row[0] if row else None

    async def set_image_route(self, uid: int, nsfw: bool, self_portrait: bool):
        if type(nsfw) is not bool or type(self_portrait) is not bool:
            raise ValueError('Image route flags must be booleans')
        async with aiosqlite.connect(self.path) as db:
            await db.execute('UPDATE image_tasks SET nsfw=?,self_portrait=? WHERE user_id=?',
                             (int(nsfw), int(self_portrait), uid))
            await db.commit()

    async def image_route(self, uid: int):
        async with aiosqlite.connect(self.path) as db:
            row = await (await db.execute('SELECT nsfw,self_portrait FROM image_tasks WHERE user_id=?', (uid,))).fetchone()
        if not row or None in row:
            return None
        return {'nsfw': bool(row[0]), 'self_portrait': bool(row[1])}


    async def should_extract(self, uid):
        async with aiosqlite.connect(self.path) as db:
            row = await (await db.execute('SELECT exchanges FROM user_counters WHERE user_id=?', (uid,))).fetchone()
        return bool(row and row[0] % 4 == 0)

    async def reserve_image(self, uid, hourly_limit, cooldown):
        import time
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute('BEGIN IMMEDIATE')
            row = await (await db.execute('SELECT COUNT(*),MAX(created_at) FROM image_requests WHERE user_id=? AND created_at>?', (uid, now-3600))).fetchone()
            if row[0] >= hourly_limit or (row[1] is not None and now-row[1] < cooldown):
                await db.rollback()
                return False
            await db.execute('DELETE FROM image_requests WHERE created_at<?', (now-3600,))
            await db.execute('INSERT INTO image_requests(user_id,created_at) VALUES(?,?)', (uid, now))
            await db.commit()
            return True

    async def add_assistant(self, uid, text, max_messages):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("INSERT INTO messages(user_id,role,content) VALUES(?,'assistant',?)", (uid,text))
            await db.execute('DELETE FROM messages WHERE user_id=? AND id NOT IN (SELECT id FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?)', (uid,uid,max_messages))
            await db.commit()

    async def selected_persona(self, uid):
        from app.personas import PROFILES
        async with aiosqlite.connect(self.path) as db:
            row = await (await db.execute('SELECT persona FROM persona_choices WHERE user_id=?',(uid,))).fetchone()
        return row[0] if row and row[0] in PROFILES else 'bailongji'

    async def select_persona(self, uid, persona):
        from app.personas import PROFILES
        if persona not in PROFILES:
            raise ValueError('Unknown persona')
        async with aiosqlite.connect(self.path) as db:
            await db.execute('INSERT INTO persona_choices(user_id,persona) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET persona=excluded.persona',(uid,persona))
            await db.commit()

    async def next_caption_index(self, uid):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('BEGIN IMMEDIATE')
            row = await (await db.execute('SELECT sequence FROM image_captions WHERE user_id=?',(uid,))).fetchone()
            index = row[0] if row else 0
            await db.execute('INSERT INTO image_captions(user_id,sequence) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET sequence=excluded.sequence',(uid,index+1))
            await db.commit()
            return index
