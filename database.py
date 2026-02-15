# database.py
import sqlite3
import os


def get_db_path(account_id: str) -> str:
    return os.path.join('data', account_id, 'userbot.db')


def init_db(account_id: str) -> None:
    db_path = get_db_path(account_id)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS spam_tasks (
                task_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                delay INTEGER NOT NULL,
                total_count INTEGER NOT NULL,
                sent_count INTEGER NOT NULL DEFAULT 0,
                start_time INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                last_sent_time INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Міграції
        c.execute("PRAGMA table_info(spam_tasks)")
        cols = [r[1] for r in c.fetchall()]
        if 'task_id' not in cols:
            c.execute("DROP TABLE spam_tasks")
            c.execute("""
                CREATE TABLE spam_tasks (
                    task_id TEXT PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    delay INTEGER NOT NULL,
                    total_count INTEGER NOT NULL,
                    sent_count INTEGER NOT NULL DEFAULT 0,
                    start_time INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    last_sent_time INTEGER NOT NULL DEFAULT 0
                )
            """)
        else:
            if 'status' not in cols:
                c.execute("ALTER TABLE spam_tasks ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
            if 'last_sent_time' not in cols:
                c.execute("ALTER TABLE spam_tasks ADD COLUMN last_sent_time INTEGER NOT NULL DEFAULT 0")
        conn.commit()


class DB:
    """Обгортка над БД конкретного акаунта."""

    def __init__(self, account_id: str) -> None:
        self.path = get_db_path(account_id)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    # --- Конфігурація ---

    def set_config(self, key: str, value) -> None:
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)))
            conn.commit()

    def get_config(self, key: str, default=None):
        with self._conn() as conn:
            r = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
            return r[0] if r else default

    # --- Завдання спаму ---

    def add_spam_task(self, task_id: str, chat_id: int, message: str,
                      delay: int, total_count: int, start_time: int) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO spam_tasks
                    (task_id, chat_id, message, delay, total_count, sent_count, start_time, status, last_sent_time)
                VALUES (?, ?, ?, ?, ?, 0, ?, 'active', 0)
            """, (task_id, chat_id, message, delay, total_count, start_time))
            conn.commit()

    def get_spam_task(self, task_id: str):
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute("SELECT * FROM spam_tasks WHERE task_id = ?", (task_id,)).fetchone()

    def get_all_spam_tasks(self, status: str = None):
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            if status:
                return conn.execute("SELECT * FROM spam_tasks WHERE status = ?", (status,)).fetchall()
            return conn.execute("SELECT * FROM spam_tasks").fetchall()

    def get_spam_tasks_by_chat(self, chat_id: int, status: str = None):
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            if status:
                return conn.execute(
                    "SELECT * FROM spam_tasks WHERE chat_id = ? AND status = ?", (chat_id, status)
                ).fetchall()
            return conn.execute("SELECT * FROM spam_tasks WHERE chat_id = ?", (chat_id,)).fetchall()

    def update_sent_count(self, task_id: str, sent_count: int) -> None:
        import time
        with self._conn() as conn:
            conn.execute(
                "UPDATE spam_tasks SET sent_count = ?, last_sent_time = ? WHERE task_id = ?",
                (sent_count, int(time.time()), task_id)
            )
            conn.commit()

    def set_task_status(self, task_id: str, status: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE spam_tasks SET status = ? WHERE task_id = ?", (status, task_id))
            conn.commit()

    def remove_spam_task(self, task_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM spam_tasks WHERE task_id = ?", (task_id,))
            conn.commit()

    def remove_all_spam_tasks_by_chat(self, chat_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM spam_tasks WHERE chat_id = ?", (chat_id,))
            conn.commit()

    def make_task_id(self) -> str:
        """Найменше вільне число з натурального ряду."""
        tasks = self.get_all_spam_tasks()
        used = {int(t['task_id']) for t in tasks if t['task_id'].isdigit()}
        n = 1
        while n in used:
            n += 1
        return str(n)