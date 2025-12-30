from telethon import TelegramClient, events
import asyncio
import re
import os

import json

# ============ НАЛАШТУВАННЯ ============
API_ID = os.getenv('API_ID', 'YOUR_API_ID')
API_HASH = os.getenv('API_HASH', 'YOUR_API_HASH')
PHONE = os.getenv('PHONE', '+380XXXXXXXXX')

# Шлях до файлу сесії (для Docker volume)
SESSION_PATH = os.getenv('SESSION_PATH', 'data/userbot_session')
CONFIG_FILE = os.path.join('data', 'config.json')

# Глобальна змінна для чату логів
LOG_CHAT = 'me'  # За замовчуванням - Saved Messages
# ======================================

client = TelegramClient(SESSION_PATH, API_ID, API_HASH)

# Зберігаємо активні задачі
active_tasks = {}

def parse_time(time_str):
    """
    Парсить час у форматі: 1с, 1хв, 1г, 1д або комбінації: 1г30хв, 2д12г30хв
    Підтримує українську (с, хв, г, д) та англійську (s, m, h, d)
    
    Приклади:
    - 30с або 30s → 30 секунд
    - 5хв або 5m → 300 секунд
    - 2г або 2h → 7200 секунд
    - 1д або 1d → 86400 секунд
    - 1г30хв або 1h30m → 5400 секунд
    - 2д12г30хв5с → 218405 секунд
    """
    time_str = time_str.lower().strip()
    
    # Визначення одиниць часу (українська та англійська)
    time_units = {
        'с': 1,      's': 1,      'sec': 1,
        'хв': 60,    'м': 60,     'm': 60,    'min': 60,
        'г': 3600,   'ч': 3600,   'h': 3600,  'hour': 3600,
        'д': 86400,  'd': 86400,  'day': 86400
    }
    
    # Паттерн для пошуку числа + одиниця часу
    pattern = r'(\d+)\s*([a-zа-яії]+)'
    matches = re.findall(pattern, time_str)
    
    if not matches:
        return None
    
    total_seconds = 0
    for value, unit in matches:
        value = int(value)
        
        # Шукаємо одиницю часу
        multiplier = None
        for key, mult in time_units.items():
            if unit.startswith(key):
                multiplier = mult
                break
        
        if multiplier is None:
            return None
        
        total_seconds += value * multiplier
    
    return total_seconds if total_seconds > 0 else None

async def parse_command(text):
    """
    Парсить команду формату:
    !spam <затримка> <кількість_разів> <текст повідомлення>
    
    Приклади:
    !spam 30с 5 Привіт!
    !spam 1хв 10 Як справи?
    !spam 1г30хв 3 Тестове повідомлення
    !spam 2h30m 5 Hello!
    """
    # Паттерн: !spam + затримка + кількість + текст
    pattern = r'^!spam\s+([0-9a-zа-яії\s]+?)\s+(\d+)\s+(.+)$'
    match = re.match(pattern, text.strip(), re.DOTALL | re.IGNORECASE)
    
    if not match:
        return None
    
    time_str = match.group(1).strip()
    count = int(match.group(2))
    message = match.group(3).strip()
    
    # Парсимо час
    delay = parse_time(time_str)
    
    if delay is None:
        return None
    
    return delay, count, message

def format_time(seconds):
    """Форматує секунди в читабельний вигляд"""
    if seconds < 60:
        return f"{seconds}с"
    elif seconds < 3600:
        minutes = seconds // 60
        secs = seconds % 60
        if secs > 0:
            return f"{minutes}хв {secs}с"
        return f"{minutes}хв"
    elif seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if minutes > 0:
            return f"{hours}г {minutes}хв"
        return f"{hours}г"
    else:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        if hours > 0:
            return f"{days}д {hours}г"
        return f"{days}д"

async def log_to_chat(message):
    """Відправляє лог повідомлення в окремий чат"""
    try:
        print(f"[DEBUG] Спроба відправити лог в {LOG_CHAT}")
        print(f"[DEBUG] Повідомлення: {message[:50]}...")
        
        # Отримуємо entity (важливо для груп!)
        if isinstance(LOG_CHAT, int):
            try:
                entity = await client.get_entity(LOG_CHAT)
                await client.send_message(entity, message)
            except ValueError:
                # Якщо не знайдено entity, спробуємо напряму
                print(f"[DEBUG] Спроба отримати entity через діалоги...")
                async for dialog in client.iter_dialogs():
                    if dialog.id == LOG_CHAT:
                        await client.send_message(dialog, message)
                        print(f"[DEBUG] ✅ Лог відправлено через dialog")
                        return
                raise
        else:
            # Для 'me' або @username
            await client.send_message(LOG_CHAT, message)
        
        print(f"[DEBUG] ✅ Лог успішно відправлено")
    except Exception as e:
        print(f"[ERROR] Помилка логування: {e}")
        print(f"[ERROR] LOG_CHAT = {LOG_CHAT}")
        print(f"[ERROR] Тип LOG_CHAT: {type(LOG_CHAT)}")
        print(f"[HINT] Спробуйте написати будь-що в цю групу вручну, потім перезапустіть бота")

async def send_spam_messages(chat_id, message, delay, count, original_msg):
    """Відправляє повідомлення з затримкою, редагуючи перше."""
    i = 0
    try:
        # Отримуємо інфо про чат для логів
        try:
            chat = await client.get_entity(chat_id)
            chat_name = getattr(chat, 'title', None) or getattr(chat, 'first_name', f'Chat {chat_id}')
        except:
            chat_name = f"Chat {chat_id}"
        
        time_formatted = format_time(delay)
        
        for i in range(1, count + 1):
            await asyncio.sleep(delay)
            
            if chat_id not in active_tasks:
                break

            # Перше повідомлення редагуємо, решту - відправляємо
            if i == 1:
                await original_msg.edit(message)
            else:
                await client.send_message(chat_id, message)
            
            # Логуємо в окремий чат
            log_message = (
                f"📤 Відправлено {i}/{count}\n"
                f"👤 Чат: {chat_name}\n"
                f"💬 Текст: {message}\n"
                f"⏱ Затримка: {time_formatted}"
            )
            await log_to_chat(log_message)
        
        # Завершення
        if chat_id in active_tasks:
            final_log = (
                f"✅ Розсилку завершено!\n\n"
                f"👤 Чат: {chat_name}\n"
                f"📊 Відправлено: {count} повідомлень\n"
                f"💬 Текст: {message}\n"
                f"⏱ Затримка: {time_formatted}"
            )
            await log_to_chat(final_log)
            del active_tasks[chat_id]
    
    except asyncio.CancelledError:
        chat = await client.get_entity(chat_id)
        chat_name = getattr(chat, 'title', None) or getattr(chat, 'first_name', f'Chat {chat_id}')
        cancelled_log = (
            f"⛔️ Розсилку скасовано!\n\n"
            f"👤 Чат: {chat_name}\n"
            f"📊 Відправлено: {i}/{count}\n"
            f"💬 Текст: {message}"
        )
        await log_to_chat(cancelled_log)
        if chat_id in active_tasks:
            del active_tasks[chat_id]
    
    except Exception as e:
        chat = await client.get_entity(chat_id)
        chat_name = getattr(chat, 'title', None) or getattr(chat, 'first_name', f'Chat {chat_id}')
        error_log = (
            f"❌ Помилка розсилки!\n\n"
            f"👤 Чат: {chat_name}\n"
            f"💬 Текст: {message}\n"
            f"⚠️ Помилка: {str(e)}"
        )
        await log_to_chat(error_log)
        if chat_id in active_tasks:
            del active_tasks[chat_id]

async def get_chat_info_for_log(chat_id):
    """Отримує назву чату для логування."""
    try:
        chat = await client.get_entity(chat_id)
        chat_name = getattr(chat, 'title', None) or getattr(chat, 'first_name', f'ID: {chat_id}')
        return f"ℹ️ Команда з чату: **{chat_name}**\n\n"
    except:
        return f"ℹ️ Команда з чату: **ID: {chat_id}**\n\n"

@client.on(events.NewMessage(outgoing=True, pattern=r'^!spam'))
async def spam_handler(event):
    """Обробник команди /spam"""
    parsed = await parse_command(event.raw_text)
    
    if not parsed:
        chat_info = await get_chat_info_for_log(event.chat_id)
        error_msg = (
            "❌ **Невірний формат команди!**\n\n"
            "📝 **Використання:**\n"
            "`!spam <час> <кількість> <текст>`\n\n"
            "⏱ **Формати часу:**\n"
            "• `30с` або `30s` - секунди\n"
            "• `5хв` або `5m` - хвилини\n"
            "• `2г` або `2h` - години\n"
            "• `1д` або `1d` - дні\n"
            "• `1г30хв` або `1h30m` - комбінації\n\n"
            "📌 **Приклади:**\n"
            "• `!spam 30с 10 Привіт!`\n"
            "• `!spam 5m 5 Hello!`\n"
            "• `!spam 1г30хв 3 Тест`"
        )
        await log_to_chat(f"{chat_info}{error_msg}")
        await event.delete()
        return
    
    delay, count, message = parsed
    chat_id = event.chat_id
    
    # Перевіряємо, чи вже є активна задача для цього чату
    if chat_id in active_tasks:
        warning_msg = "⚠️ У цьому чаті вже є активна розсилка!\nВикористайте !stop для зупинки."
        await log_to_chat(warning_msg)
        await event.delete()
        return
    
    # Форматуємо час для виводу
    time_formatted = format_time(delay)
    
    # Отримуємо інфо про чат
    try:
        chat = await client.get_entity(chat_id)
        chat_name = getattr(chat, 'title', None) or getattr(chat, 'first_name', f'Chat {chat_id}')
    except:
        chat_name = f"Chat {chat_id}"
    
    # Логуємо початок
    start_log = (
        f"🚀 Розсилку запущено!\n\n"
        f"👤 Чат: {chat_name}\n"
        f"💬 Текст: {message}\n"
        f"⏱ Затримка: {time_formatted}\n"
        f"🔢 Кількість: {count}\n\n"
        f"📊 Відправлено: 0/{count}\n\n"
        f"Для зупинки: !stop"
    )
    await log_to_chat(start_log)
    
    # Створюємо та зберігаємо задачу
    task = asyncio.create_task(
        send_spam_messages(chat_id, message, delay, count, event.message)
    )
    active_tasks[chat_id] = task

@client.on(events.NewMessage(outgoing=True, pattern=r'^!stop'))
async def stop_handler(event):
    """Зупиняє активну розсилку в поточному чаті"""
    chat_id = event.chat_id
    chat_info = await get_chat_info_for_log(chat_id)
    
    if chat_id in active_tasks:
        active_tasks[chat_id].cancel()
        await log_to_chat(f"{chat_info}⛔️ Розсилку зупинено командою!")
        await event.delete()
    else:
        await log_to_chat(f"{chat_info}ℹ️ Немає активних розсилок у цьому чаті")
        await event.delete()

@client.on(events.NewMessage(outgoing=True, pattern=r'^!status'))
async def status_handler(event):
    """Показує статус всіх активних розсилок"""
    chat_info = await get_chat_info_for_log(event.chat_id)
    if not active_tasks:
        await log_to_chat(f"{chat_info}ℹ️ Немає активних розсилок")
    else:
        status_text = f"📊 Активних розсилок: {len(active_tasks)}\n\n"
        for chat_id in active_tasks:
            try:
                chat = await client.get_entity(chat_id)
                chat_name = getattr(chat, 'title', None) or getattr(chat, 'first_name', 'Невідомий чат')
                status_text += f"• {chat_name} (ID: {chat_id})\n"
            except:
                status_text += f"• Чат ID: {chat_id}\n"
        
        status_text += "\n🛑 Використайте !stop в потрібному чаті для зупинки"
        await log_to_chat(f"{chat_info}{status_text}")
    
    await event.delete()

@client.on(events.NewMessage(outgoing=True, pattern=r'^!help'))
async def help_handler(event):
    """Показує довідку"""
    chat_info = await get_chat_info_for_log(event.chat_id)
    help_text = """
🤖 **Команди Userbot**

📤 `!spam <час> <кількість> <текст>`
   Запускає розсилку повідомлень.
   
⏱ **Формати часу:**
   • `с, s` - секунди (30с)
   • `хв, m` - хвилини (5хв)
   • `г, h` - години (2г)
   • `д, d` - дні (1д)
   • Комбінації: `1г30хв`

📌 **Приклади:**
   • `!spam 30с 10 Привіт!`
   • `!spam 5m 5 Hello!`
   • `!spam 1г30хв 3 Тест`

⛔️ `!stop`
   Зупиняє розсилку в поточному чаті.

📊 `!status`
   Показує всі активні розсилки.

❓ `!help`
   Показує цю довідку.

🆔 `!chatid`
   Показує ID поточного чату.

⚙️ `!setlog`
   Встановлює поточний чат як чат для логів.

⚠️ **Увага:** Масова розсилка може призвести до блокування акаунта!
"""
    await log_to_chat(f"{chat_info}{help_text}")
    await event.delete()

@client.on(events.NewMessage(outgoing=True, pattern=r'^!setlog'))
async def set_log_chat(event):
    """Встановлює поточний чат як чат для логів та зберігає його."""
    global LOG_CHAT
    chat_id = event.chat_id
    
    # Отримуємо назву чату, в якому виконали команду
    try:
        chat = await client.get_entity(chat_id)
        chat_name = getattr(chat, 'title', None) or getattr(chat, 'first_name', f'ID: {chat_id}')
    except:
        chat_name = f"ID: {chat_id}"

    LOG_CHAT = chat_id
    save_log_chat(chat_id)  # Зберігаємо ID
    
    await log_to_chat(f"✅ Новий чат для логів встановлено: **{chat_name}** (ID: `{chat_id}`)")
    await event.delete()

@client.on(events.NewMessage(outgoing=True, pattern=r'^!chatid'))
async def chatid_handler(event):
    """Показує ID поточного чату в логах."""
    chat_id = event.chat_id
    
    try:
        chat = await client.get_entity(chat_id)
        chat_name = getattr(chat, 'title', None) or getattr(chat, 'first_name', 'Невідомий чат')
        
        info_msg = (
            f"ℹ️ **Інформація про чат, де було введено команду:**\n\n"
            f"📝 **Назва:** {chat_name}\n"
            f"🆔 **Chat ID:** `{chat_id}`"
        )
    except:
        info_msg = f"🆔 **Chat ID:** `{chat_id}`"
    
    await log_to_chat(info_msg)
    await event.delete()


def save_log_chat(chat_id):
    """Зберігає ID чату для логів у JSON файл."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump({'log_chat_id': chat_id}, f)

def load_log_chat():
    """Завантажує ID чату для логів із JSON файлу."""
    global LOG_CHAT
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            try:
                config = json.load(f)
                LOG_CHAT = config.get('log_chat_id', 'me')
            except json.JSONDecodeError:
                LOG_CHAT = 'me'
    else:
        LOG_CHAT = 'me'

async def main():
    """Запуск бота"""
    load_log_chat()  # Завантажуємо налаштування чату для логів
    await client.start(phone=PHONE)
    
    print(f"[INFO] ✅ Userbot запущено!")
    print(f"[INFO] 📋 LOG_CHAT = {LOG_CHAT}")
    print(f"[INFO] 📋 Тип LOG_CHAT: {type(LOG_CHAT)}")
    
    # Спочатку завантажимо всі діалоги в кеш
    if isinstance(LOG_CHAT, int):
        print(f"[INFO] 🔄 Завантаження діалогів в кеш...")
        dialog_found = False
        async for dialog in client.iter_dialogs(limit=100):
            if dialog.id == LOG_CHAT:
                print(f"[INFO] ✅ Знайдено лог-чат: {dialog.name}")
                dialog_found = True
                break
        
        if not dialog_found:
            print(f"[WARNING] ⚠️ Лог-чат з ID {LOG_CHAT} не знайдено в ваших діалогах!")
            print(f"[HINT] Переконайтесь що:")
            print(f"  1. Ви є учасником цієї групи")
            print(f"  2. Ви хоч раз писали в цю групу")
            print(f"  3. Chat ID правильний (напишіть /chatid в групі)")
    
    # Тестове повідомлення про запуск
    startup_message = (
        "✅ Userbot запущено!\n\n"
        "📝 Цей чат використовується для логів\n"
        "💡 Введіть !help для довідки\n"
        "⚙️ Використайте !setlog в іншому чаті для зміни чату логів"
    )
    
    try:
        await log_to_chat(startup_message)
        print("[INFO] 🎉 Startup message відправлено успішно!")
    except Exception as e:
        print(f"[ERROR] Не вдалось відправити startup message: {e}")
    
    print("📝 Введіть !help в будь-якому чаті для довідки")
    print("⛔️ Натисніть Ctrl+C для виходу\n")
    
    # Тримаємо бота запущеним
    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹ Userbot зупинено")
        # Скасовуємо всі активні задачі
        for task in active_tasks.values():
            task.cancel()