import aiosqlite
from pathlib import Path

class Storage:
    def __init__(self, path: str):
        self.path = path

    async def init(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript("""
                PRAGMA journal_mode=WAL;
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
                CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id,id);\n                CREATE TABLE IF NOT EXISTS character_state (user_id INTEGER PRIMARY KEY, mood TEXT NOT NULL DEFAULT 'steady', remaining INTEGER NOT NULL DEFAULT 0);
            """)
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
            await db.executemany('INSERT INTO messages(user_id,role,content) VALUES(?,?,?)',[(uid,'user',question),(uid,'assistant',answer)])
            await db.execute('DELETE FROM messages WHERE user_id=? AND id NOT IN (SELECT id FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?)',(uid,uid,max_messages))
            await db.commit()

    async def clear_history(self, uid: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('DELETE FROM messages WHERE user_id=?',(uid,))\n            await db.execute('DELETE FROM character_state WHERE user_id=?',(uid,))
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

    async def character_state(self, uid: int):
        async with aiosqlite.connect(self.path) as db:
            row = await (await db.execute('SELECT mood,remaining FROM character_state WHERE user_id=?',(uid,))).fetchone()
        return row if row else ('steady',0)

    async def set_character_state(self, uid: int, mood: str, remaining: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('INSERT INTO character_state(user_id,mood,remaining) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET mood=excluded.mood,remaining=excluded.remaining',(uid,mood,max(0,remaining)))
            await db.commit()

    async def exchange_count(self, uid: int) -> int:
        async with aiosqlite.connect(self.path) as db:
            row = await (await db.execute("SELECT COUNT(*) FROM messages WHERE user_id=? AND role='user'",(uid,))).fetchone()
        return row[0]
