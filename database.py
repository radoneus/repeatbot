# database.py
import sqlite3
import os

DB_PATH = os.path.join('data', 'userbot.db')

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
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
                status TEXT NOT NULL DEFAULT 'active'
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
                    status TEXT NOT NULL DEFAULT 'active'
                )
            """)
        elif 'status' not in cols:
            c.execute("ALTER TABLE spam_tasks ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
        if 'last_sent_time' not in cols:
            c.execute("ALTER TABLE spam_tasks ADD COLUMN last_sent_time INTEGER NOT NULL DEFAULT 0")
        conn.commit()

# --- Конфігурація ---

def set_config(key, value):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()

def get_config(key, default=None):
    with sqlite3.connect(DB_PATH) as conn:
        r = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return r[0] if r else default

# --- Завдання спаму ---

def add_spam_task(task_id, chat_id, message, delay, total_count, start_time):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO spam_tasks
                (task_id, chat_id, message, delay, total_count, sent_count, start_time, status)
            VALUES (?, ?, ?, ?, ?, 0, ?, 'active')
        """, (task_id, chat_id, message, delay, total_count, start_time))
        conn.commit()

def get_spam_task(task_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM spam_tasks WHERE task_id = ?", (task_id,)).fetchone()

def get_all_spam_tasks(status=None):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if status:
            return conn.execute("SELECT * FROM spam_tasks WHERE status = ?", (status,)).fetchall()
        return conn.execute("SELECT * FROM spam_tasks").fetchall()

def get_spam_tasks_by_chat(chat_id, status=None):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if status:
            return conn.execute(
                "SELECT * FROM spam_tasks WHERE chat_id = ? AND status = ?", (chat_id, status)
            ).fetchall()
        return conn.execute("SELECT * FROM spam_tasks WHERE chat_id = ?", (chat_id,)).fetchall()

def update_sent_count(task_id, sent_count):
    import time
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE spam_tasks SET sent_count = ? WHERE task_id = ?", (sent_count, task_id))
        conn.commit()

def update_last_sent_time(task_id):
    import time
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE spam_tasks SET last_sent_time = ? WHERE task_id = ?",
                     (int(time.time()), task_id))
        conn.commit()

def set_task_status(task_id, status):
    """status: 'active' | 'paused'"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE spam_tasks SET status = ? WHERE task_id = ?", (status, task_id))
        conn.commit()

def remove_spam_task(task_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM spam_tasks WHERE task_id = ?", (task_id,))
        conn.commit()

def remove_all_spam_tasks_by_chat(chat_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM spam_tasks WHERE chat_id = ?", (chat_id,))
        conn.commit()