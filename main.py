from telethon import TelegramClient, events
import asyncio
import re
import os
import time
import signal

import database

# ============ НАЛАШТУВАННЯ ============
API_ID   = os.getenv('API_ID',   'YOUR_API_ID')
API_HASH = os.getenv('API_HASH', 'YOUR_API_HASH')
PHONE    = os.getenv('PHONE',    '+380XXXXXXXXX')
SESSION_PATH = os.getenv('SESSION_PATH', 'data/userbot_session')

LOG_CHAT = 'me'
# ======================================

client = TelegramClient(SESSION_PATH, API_ID, API_HASH)

# active_tasks: { chat_id: { task_id: asyncio.Task } }
active_tasks: dict[int, dict[str, asyncio.Task]] = {}

# ============ УТИЛІТИ ============

def make_task_id() -> str:
    """Найменше вільне число з натурального ряду серед усіх задач у БД."""
    tasks = database.get_all_spam_tasks()
    used = {int(t['task_id']) for t in tasks if t['task_id'].isdigit()}
    n = 1
    while n in used:
        n += 1
    return str(n)

def parse_time(time_str: str) -> int | None:
    time_str = time_str.lower().strip()
    time_units = {
        'с': 1,    's': 1,    'sec': 1,
        'хв': 60,  'м': 60,   'm': 60,   'min': 60,
        'г': 3600, 'ч': 3600, 'h': 3600, 'hour': 3600,
        'д': 86400,'d': 86400,'day': 86400,
    }
    matches = re.findall(r'(\d+)\s*([a-zа-яії]+)', time_str)
    if not matches:
        return None
    total = 0
    for val, unit in matches:
        mult = next((m for k, m in time_units.items() if unit.startswith(k)), None)
        if mult is None:
            return None
        total += int(val) * mult
    return total if total > 0 else None

def parse_command(text: str) -> tuple[int, int, str] | None:
    m = re.match(r'^!spam\s+([0-9a-zа-яії\s]+?)\s+(\d+)\s+(.+)$',
                 text.strip(), re.DOTALL | re.IGNORECASE)
    if not m:
        return None
    delay = parse_time(m.group(1).strip())
    if delay is None:
        return None
    return delay, int(m.group(2)), m.group(3).strip()

def format_time(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}с"
    elif seconds < 3600:
        mn = seconds // 60
        s = seconds % 60
        return f"{mn}хв {s}с" if s else f"{mn}хв"
    elif seconds < 86400:
        h = seconds // 3600
        mn = (seconds % 3600) // 60
        return f"{h}г {mn}хв" if mn else f"{h}г"
    else:
        d = seconds // 86400
        h = (seconds % 86400) // 3600
        return f"{d}д {h}г" if h else f"{d}д"

def get_remaining_wait(row) -> int:
    """Скільки секунд залишилось до наступного повідомлення."""
    last_sent = row['last_sent_time']
    if last_sent == 0:
        return row['delay']
    return max(0, (last_sent + row['delay']) - int(time.time()))

def _cleanup_task(chat_id: int, task_id: str) -> None:
    if chat_id in active_tasks:
        active_tasks[chat_id].pop(task_id, None)
        if not active_tasks[chat_id]:
            del active_tasks[chat_id]

def _start_task(chat_id: int, task_id: str, coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    active_tasks.setdefault(chat_id, {})[task_id] = task
    return task

# ============ ЛОГ / ЧАТ ============

async def log_to_chat(message: str) -> None:
    try:
        if isinstance(LOG_CHAT, int):
            try:
                entity = await client.get_entity(LOG_CHAT)
                await client.send_message(entity, message)
            except ValueError:
                async for dialog in client.iter_dialogs():
                    if dialog.id == LOG_CHAT:
                        await client.send_message(dialog, message)
                        return
                raise
        else:
            await client.send_message(LOG_CHAT, message)
    except Exception as e:
        print(f"[ERROR] Помилка логування: {e}")

async def get_chat_name(chat_id: int) -> str:
    try:
        chat = await client.get_entity(chat_id)
        return getattr(chat, 'title', None) or getattr(chat, 'first_name', f'ID: {chat_id}')
    except Exception:
        return f"ID: {chat_id}"

async def get_chat_info_for_log(chat_id: int) -> str:
    return f"ℹ️ Команда з чату: **{await get_chat_name(chat_id)}**\n\n"

# ============ ЯДРО РОЗСИЛКИ ============

async def send_spam_messages(
    chat_id: int,
    task_id: str,
    message: str,
    delay: int,
    count: int,
    original_msg=None,
    initial_wait: int = 0,
) -> None:
    i = 0
    chat_name = await get_chat_name(chat_id)
    time_fmt = format_time(delay)

    try:
        if initial_wait > 0:
            await asyncio.sleep(initial_wait)

        for i in range(1, count + 1):
            if chat_id not in active_tasks or task_id not in active_tasks[chat_id]:
                break

            if i == 1 and original_msg:
                await original_msg.edit(message)
            else:
                await client.send_message(chat_id, message)

            await log_to_chat(
                f"📤 [{task_id}] Відправлено {i}/{count}\n"
                f"👤 Чат: {chat_name}\n"
                f"💬 Текст: {message}\n"
                f"⏱ Затримка: {time_fmt}"
            )
            database.update_sent_count(task_id, i)

            if i < count:
                await asyncio.sleep(delay)

        if chat_id in active_tasks and task_id in active_tasks[chat_id]:
            await log_to_chat(
                f"✅ [{task_id}] Розсилку завершено!\n\n"
                f"👤 Чат: {chat_name}\n"
                f"📊 Відправлено: {count}\n"
                f"💬 Текст: {message}\n"
                f"⏱ Затримка: {time_fmt}"
            )
            database.remove_spam_task(task_id)
            _cleanup_task(chat_id, task_id)

    except asyncio.CancelledError:
        _cleanup_task(chat_id, task_id)
        raise  # re-raise як вимагає best practice

    except Exception as e:
        await log_to_chat(
            f"❌ [{task_id}] Помилка!\n👤 {chat_name}\n💬 {message}\n⚠️ {e}"
        )
        database.remove_spam_task(task_id)
        _cleanup_task(chat_id, task_id)

# ============ КОМАНДИ ============

@client.on(events.NewMessage(outgoing=True, pattern=r'^!spam'))
async def spam_handler(event) -> None:
    parsed = parse_command(event.raw_text)
    chat_id = event.chat_id
    chat_info = await get_chat_info_for_log(chat_id)

    if not parsed:
        await log_to_chat(
            f"{chat_info}❌ **Невірний формат!**\n\n"
            "`!spam <час> <кількість> <текст>`\n\n"
            "⏱ Формати: `30с`, `5хв`, `2г`, `1д`, `1г30хв`\n"
            "Приклад: `!spam 30с 10 Привіт!`"
        )
        await event.delete()
        return

    delay, count, message = parsed
    if count <= 0:
        await log_to_chat(f"{chat_info}❌ Кількість має бути > 0.")
        await event.delete()
        return

    task_id = make_task_id()
    chat_name = await get_chat_name(chat_id)
    existing = len(active_tasks.get(chat_id, {}))
    extra = f"\n⚡️ Активних у цьому чаті: {existing + 1}" if existing > 0 else ""

    database.add_spam_task(task_id, chat_id, message, delay, count, int(time.time()))
    await log_to_chat(
        f"🚀 [{task_id}] Розсилку запущено!{extra}\n\n"
        f"👤 {chat_name}\n💬 {message}\n"
        f"⏱ {format_time(delay)} · 🔢 {count}\n\n"
        f"`!stop {task_id}` — зупинити | `!stop` — всі"
    )
    _start_task(chat_id, task_id,
        send_spam_messages(chat_id, task_id, message, delay, count, event.message))


@client.on(events.NewMessage(outgoing=True, pattern=r'^!stop'))
async def stop_handler(event) -> None:
    """
    !stop       — зупинити і видалити всі розсилки
    !stop <id>  — зупинити і видалити конкретну
    """
    chat_info = await get_chat_info_for_log(event.chat_id)
    parts = event.raw_text.strip().split()
    target_id = parts[1] if len(parts) > 1 else None

    if target_id:
        await _stop_one(target_id, chat_info)
    else:
        await _stop_all(chat_info)
    await event.delete()

async def _stop_one(task_id: str, chat_info: str) -> None:
    row = database.get_spam_task(task_id)
    if not row:
        await log_to_chat(f"{chat_info}❌ Розсилку `{task_id}` не знайдено.")
        return
    chat_id = row['chat_id']
    task = active_tasks.get(chat_id, {}).get(task_id)
    if task:
        task.cancel()
        await asyncio.sleep(0)
    database.remove_spam_task(task_id)
    _cleanup_task(chat_id, task_id)
    await log_to_chat(
        f"{chat_info}⛔️ [{task_id}] Зупинено і видалено.\n"
        f"👤 {await get_chat_name(chat_id)} · 📊 {row['sent_count']}/{row['total_count']}"
    )

async def _stop_all(chat_info: str) -> None:
    all_tasks = database.get_all_spam_tasks()
    if not all_tasks and not active_tasks:
        await log_to_chat(f"{chat_info}ℹ️ Немає активних розсилок.")
        return
    for chat_tasks in active_tasks.values():
        for task in chat_tasks.values():
            task.cancel()
    await asyncio.sleep(0)
    active_tasks.clear()
    for row in all_tasks:
        database.remove_spam_task(row['task_id'])
    await log_to_chat(f"{chat_info}⛔️ Зупинено і видалено {len(all_tasks)} розсилок.")


@client.on(events.NewMessage(outgoing=True, pattern=r'^!pause(?!all)'))
async def pause_handler(event) -> None:
    """!pause <id> — призупинити конкретну розсилку."""
    chat_info = await get_chat_info_for_log(event.chat_id)
    parts = event.raw_text.strip().split()
    if len(parts) < 2:
        await log_to_chat(f"{chat_info}❌ Вкажіть ID: `!pause <id>`")
        await event.delete()
        return
    await _pause_one(parts[1], chat_info)
    await event.delete()

@client.on(events.NewMessage(outgoing=True, pattern=r'^!pauseall'))
async def pauseall_handler(event) -> None:
    """!pauseall — призупинити всі активні розсилки."""
    await _pause_all(await get_chat_info_for_log(event.chat_id))
    await event.delete()

async def _pause_one(task_id: str, chat_info: str) -> None:
    row = database.get_spam_task(task_id)
    if not row:
        await log_to_chat(f"{chat_info}❌ Розсилку `{task_id}` не знайдено.")
        return
    chat_id = row['chat_id']
    task = active_tasks.get(chat_id, {}).get(task_id)
    if task:
        task.cancel()
        await asyncio.sleep(0)
    database.set_task_status(task_id, 'paused')
    await log_to_chat(
        f"⏸ [{task_id}] Призупинено.\n"
        f"👤 {await get_chat_name(chat_id)} · 📊 {row['sent_count']}/{row['total_count']}\n"
        f"`!continue {task_id}` — продовжити"
    )

async def _pause_all(chat_info: str) -> None:
    all_tasks = database.get_all_spam_tasks(status='active')
    if not all_tasks and not active_tasks:
        await log_to_chat(f"{chat_info}ℹ️ Немає активних розсилок.")
        return
    for chat_tasks in active_tasks.values():
        for task in chat_tasks.values():
            task.cancel()
    await asyncio.sleep(0)
    for row in all_tasks:
        database.set_task_status(row['task_id'], 'paused')
    await log_to_chat(
        f"{chat_info}⏸ Призупинено {len(all_tasks)} розсилок.\n"
        f"`!continueall` — відновити всі"
    )


@client.on(events.NewMessage(outgoing=True, pattern=r'^!continue(?!all)'))
async def continue_handler(event) -> None:
    """!continue <id> — продовжити конкретну розсилку."""
    chat_info = await get_chat_info_for_log(event.chat_id)
    parts = event.raw_text.strip().split()
    if len(parts) < 2:
        await log_to_chat(f"{chat_info}❌ Вкажіть ID: `!continue <id>`")
        await event.delete()
        return
    await _resume_one(parts[1], chat_info)
    await event.delete()

@client.on(events.NewMessage(outgoing=True, pattern=r'^!continueall'))
async def continueall_handler(event) -> None:
    """!continueall — відновити всі призупинені розсилки."""
    chat_info = await get_chat_info_for_log(event.chat_id)
    paused = database.get_all_spam_tasks(status='paused')
    if not paused:
        await log_to_chat(f"{chat_info}ℹ️ Немає призупинених розсилок.")
        await event.delete()
        return
    resumed = sum([1 for row in paused if await _resume_one(row['task_id'], chat_info, silent=True)])
    await log_to_chat(f"{chat_info}▶️ Відновлено {resumed} розсилок.")
    await event.delete()

async def _resume_one(task_id: str, chat_info: str, silent: bool = False) -> bool:
    row = database.get_spam_task(task_id)
    if not row:
        if not silent:
            await log_to_chat(f"{chat_info}❌ Розсилку `{task_id}` не знайдено.")
        return False
    if row['status'] != 'paused':
        if not silent:
            await log_to_chat(f"{chat_info}⚠️ [{task_id}] не призупинена (статус: {row['status']}).")
        return False

    chat_id   = row['chat_id']
    remaining = row['total_count'] - row['sent_count']
    if remaining <= 0:
        database.remove_spam_task(task_id)
        if not silent:
            await log_to_chat(f"{chat_info}ℹ️ [{task_id}] вже завершена, видалено з БД.")
        return False

    initial_wait = get_remaining_wait(row)
    database.set_task_status(task_id, 'active')

    if not silent:
        wait_str = f"чекати {format_time(initial_wait)}" if initial_wait > 0 else "відправляє одразу"
        await log_to_chat(
            f"▶️ [{task_id}] Відновлено ({wait_str}).\n"
            f"👤 {await get_chat_name(chat_id)} · 📊 {row['sent_count']}/{row['total_count']}"
        )

    _start_task(chat_id, task_id,
        send_spam_messages(chat_id, task_id, row['message'], row['delay'], remaining,
                           initial_wait=initial_wait))
    return True


@client.on(events.NewMessage(outgoing=True, pattern=r'^!status'))
async def status_handler(event) -> None:
    chat_info = await get_chat_info_for_log(event.chat_id)
    all_tasks = database.get_all_spam_tasks()

    if not all_tasks:
        await log_to_chat(f"{chat_info}ℹ️ Немає розсилок.")
        await event.delete()
        return

    lines = []
    for row in all_tasks:
        tid        = row['task_id']
        cid        = row['chat_id']
        msg        = row['message']
        status     = row['status']
        sent       = row['sent_count']
        total      = row['total_count']
        delay      = row['delay']
        next_in    = get_remaining_wait(row)
        status_str = "▶️ Активна" if status == 'active' else "⏸ Призупинена"
        cname      = await get_chat_name(cid)
        lines.append(
            f"• [{tid}] {cname}\n"
            f"  {status_str}\n"
            f"  💬 {msg[:40]}{'...' if len(msg) > 40 else ''}\n"
            f"  📊 {sent}/{total} · ⏱ {format_time(delay)}\n"
            f"  ⏳ Наступне через: {format_time(next_in)}\n"
        )

    await log_to_chat(
        f"{chat_info}📊 **Розсилки:**\n\n" + "".join(lines) +
        "\n`!stop <id>` · `!stop` · `!pause <id>` · `!pauseall` · `!continue <id>` · `!continueall`"
    )
    await event.delete()


@client.on(events.NewMessage(outgoing=True, pattern=r'^!help'))
async def help_handler(event) -> None:
    chat_info = await get_chat_info_for_log(event.chat_id)
    await log_to_chat(
        f"{chat_info}🤖 **Команди Userbot**\n\n"
        "📤 `!spam <час> <кількість> <текст>` — запустити розсилку\n"
        "   Можна запускати кілька паралельно в одному чаті.\n\n"
        "⛔️ `!stop <id>` — зупинити і видалити конкретну\n"
        "⛔️ `!stop` — зупинити і видалити **всі**\n\n"
        "⏸ `!pause <id>` — призупинити конкретну (зберігається)\n"
        "⏸ `!pauseall` — призупинити **всі**\n\n"
        "▶️ `!continue <id>` — продовжити конкретну\n"
        "▶️ `!continueall` — продовжити **всі** призупинені\n\n"
        "📊 `!status` — список всіх розсилок\n"
        "🆔 `!chatid` — ID поточного чату\n"
        "⚙️ `!setlog` — встановити чат для логів\n"
        "❓ `!help` — ця довідка\n\n"
        "⏱ **Формати часу:** `30с`, `5хв`, `2г`, `1д`, `1г30хв`\n\n"
        "⚠️ Масова розсилка може призвести до блокування акаунта!"
    )
    await event.delete()


@client.on(events.NewMessage(outgoing=True, pattern=r'^!setlog'))
async def set_log_chat(event) -> None:
    global LOG_CHAT
    chat_id = event.chat_id
    LOG_CHAT = chat_id
    database.set_config('log_chat_id', chat_id)
    await log_to_chat(f"✅ Чат для логів: **{await get_chat_name(chat_id)}** (`{chat_id}`)")
    await event.delete()


@client.on(events.NewMessage(outgoing=True, pattern=r'^!chatid'))
async def chatid_handler(event) -> None:
    chat_id = event.chat_id
    await log_to_chat(f"ℹ️ **{await get_chat_name(chat_id)}**\n🆔 `{chat_id}`")
    await event.delete()


# ============ ЗАПУСК ============

async def main() -> None:
    database.init_db()

    global LOG_CHAT
    saved = database.get_config('log_chat_id', default=None)
    if saved and saved != 'me':
        LOG_CHAT = int(saved)

    await client.start(phone=PHONE)
    print(f"[INFO] ✅ Userbot запущено! LOG_CHAT={LOG_CHAT}")

    if isinstance(LOG_CHAT, int):
        print("[INFO] 🔄 Завантаження діалогів...")
        async for dialog in client.iter_dialogs(limit=100):
            if dialog.id == LOG_CHAT:
                print(f"[INFO] ✅ Лог-чат: {dialog.name}")
                break

    # Відновлення активних розсилок після перезапуску
    print("[INFO] 🔄 Відновлення розсилок з БД...")
    for row in database.get_all_spam_tasks(status='active'):
        tid       = row['task_id']
        cid       = row['chat_id']
        remaining = row['total_count'] - row['sent_count']
        if remaining > 0:
            initial_wait = get_remaining_wait(row)
            _start_task(cid, tid,
                send_spam_messages(cid, tid, row['message'], row['delay'], remaining,
                                   initial_wait=initial_wait))
            print(f"[INFO] ✅ [{tid}] відновлено, залишилось: {remaining}, чекати: {initial_wait}с")
        else:
            database.remove_spam_task(tid)

    await log_to_chat(
        "✅ Userbot запущено!\n\n"
        "📝 Цей чат — для логів\n"
        "`!help` — довідка · `!setlog` — змінити чат логів"
    )
    print("⛔️ Ctrl+C для виходу")

    stop_event = asyncio.Event()

    async def _shutdown(sig: signal.Signals) -> None:
        print(f"[INFO] Отримано сигнал {sig.name}, зберігаємо стан...")
        for chat_tasks in active_tasks.values():
            for task in chat_tasks.values():
                task.cancel()
        print("[INFO] Стан збережено, виходимо.")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(_shutdown(s)))

    await stop_event.wait()
    await client.disconnect()


if __name__ == '__main__':
    asyncio.run(main())