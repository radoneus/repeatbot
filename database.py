# database.py
import sqlite3
import os

# Шлях до бази даних
DB_PATH = os.path.join('data', 'userbot.db')

def init_db():
    """Ініціалізує базу даних та створює таблиці, якщо їх немає."""
    # Створюємо теку 'data', якщо її немає
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # Таблиця для конфігурації (наприклад, ID чату для логів)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        
        # Таблиця для активних завдань спаму
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS spam_tasks (
                chat_id INTEGER PRIMARY KEY,
                message TEXT NOT NULL,
                delay INTEGER NOT NULL,
                total_count INTEGER NOT NULL,
                sent_count INTEGER NOT NULL DEFAULT 0,
                start_time INTEGER NOT NULL
            )
        """)
        
        conn.commit()

# --- Функції для конфігурації ---

def set_config(key, value):
    """Зберігає або оновлює ключ-значення в конфігурації."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()

def get_config(key, default=None):
    """Отримує значення за ключем з конфігурації."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM config WHERE key = ?", (key,))
        result = cursor.fetchone()
        return result[0] if result else default

# --- Функції для завдань спаму ---

def add_spam_task(chat_id, message, delay, total_count, start_time):
    """Додає нове завдання спаму в БД."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO spam_tasks (chat_id, message, delay, total_count, sent_count, start_time)
            VALUES (?, ?, ?, ?, 0, ?)
        """, (chat_id, message, delay, total_count, start_time))
        conn.commit()

def get_spam_task(chat_id):
    """Отримує інформацію про завдання спаму за ID чату."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM spam_tasks WHERE chat_id = ?", (chat_id,))
        return cursor.fetchone()

def get_all_spam_tasks():
    """Повертає всі активні завдання спаму."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM spam_tasks")
        return cursor.fetchall()

def update_sent_count(chat_id, sent_count):
    """Оновлює лічильник відправлених повідомлень для завдання."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE spam_tasks SET sent_count = ? WHERE chat_id = ?", (sent_count, chat_id))
        conn.commit()

def remove_spam_task(chat_id):
    """Видаляє завдання спаму з БД."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM spam_tasks WHERE chat_id = ?", (chat_id,))
        conn.commit()

if __name__ == '__main__':
    # При прямому запуску файлу ініціалізуємо БД
    print("Ініціалізація бази даних...")
    init_db()
    print(f"База даних створена/перевірена в {DB_PATH}")
    # Демонстрація роботи
    set_config('log_chat_id', '123456')
    print("Встановлено тестовий log_chat_id: ", get_config('log_chat_id'))
    add_spam_task(-100, 'test message', 30, 10, 0)
    print("Додано тестове завдання: ", dict(get_spam_task(-100)))
    update_sent_count(-100, 1)
    print("Оновлено лічильник: ", dict(get_spam_task(-100)))
    remove_spam_task(-100)
    print("Тестове завдання видалено.")
