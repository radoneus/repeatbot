from telethon import TelegramClient, events
import asyncio
import re
import os
import time
import signal

from database import DB, init_db

# ============ УТИЛІТИ ============

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
    last_sent = row['last_sent_time']
    if last_sent == 0:
        return row['delay']
    return max(0, (last_sent + row['delay']) - int(time.time()))

def load_accounts() -> list[dict]:
    """Завантажує акаунти з .env. Формат: ACCOUNT_1_API_ID, ACCOUNT_1_API_HASH, ACCOUNT_1_PHONE"""
    accounts = []
    i = 1
    while True:
        api_id   = os.getenv(f'ACCOUNT_{i}_API_ID')
        api_hash = os.getenv(f'ACCOUNT_{i}_API_HASH')
        phone    = os.getenv(f'ACCOUNT_{i}_PHONE')
        if not api_id or not api_hash or not phone:
            break
        accounts.append({
            'account_id': f'account_{i}',
            'api_id':     int(api_id),
            'api_hash':   api_hash,
            'phone':      phone,
        })
        i += 1
    return accounts

# ============ КЛАС АКАУНТА ============

class Account:
    def __init__(self, account_id: str, api_id: int, api_hash: str, phone: str) -> None:
        self.account_id = account_id
        self.phone      = phone
        self.username   = account_id  # замінюється на реальний після авторизації
        self.db         = DB(account_id)

        session_dir = os.path.join('data', account_id)
        os.makedirs(session_dir, exist_ok=True)

        self.client     = TelegramClient(
            os.path.join('data', account_id, 'session'),
            api_id, api_hash
        )
        self.log_chat: int | str = 'me'
        self.active_tasks: dict[int, dict[str, asyncio.Task]] = {}
        self._register_handlers()

    # ============ УТИЛІТИ ============

    def _log(self, message: str) -> None:
        """Системний лог в консоль з префіксом акаунта."""
        print(f"[{self.username}] {message}")

    def _cleanup_task(self, chat_id: int, task_id: str) -> None:
        if chat_id in self.active_tasks:
            self.active_tasks[chat_id].pop(task_id, None)
            if not self.active_tasks[chat_id]:
                del self.active_tasks[chat_id]

    def _start_task(self, chat_id: int, task_id: str, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.active_tasks.setdefault(chat_id, {})[task_id] = task
        return task

    async def log(self, message: str) -> None:
        try:
            if isinstance(self.log_chat, int):
                try:
                    entity = await self.client.get_entity(self.log_chat)
                    await self.client.send_message(entity, message)
                except ValueError:
                    async for dialog in self.client.iter_dialogs():
                        if dialog.id == self.log_chat:
                            await self.client.send_message(dialog, message)
                            return
                    raise
            else:
                await self.client.send_message(self.log_chat, message)
        except Exception as e:
            self._log(f"[ERROR] Помилка логування: {e}")

    async def get_chat_name(self, chat_id: int) -> str:
        try:
            chat = await self.client.get_entity(chat_id)
            return getattr(chat, 'title', None) or getattr(chat, 'first_name', f'ID: {chat_id}')
        except Exception:
            return f"ID: {chat_id}"

    async def get_chat_info(self, chat_id: int) -> str:
        return f"ℹ️ Команда з чату: **{await self.get_chat_name(chat_id)}**\n\n"

    # ============ ЯДРО РОЗСИЛКИ ============

    async def send_spam_messages(
        self,
        chat_id: int,
        task_id: str,
        message: str,
        delay: int,
        count: int,
        original_msg=None,
        initial_wait: int = 0,
    ) -> None:
        i = 0
        chat_name = await self.get_chat_name(chat_id)
        time_fmt = format_time(delay)

        try:
            if initial_wait > 0:
                await asyncio.sleep(initial_wait)

            for i in range(1, count + 1):
                if chat_id not in self.active_tasks or task_id not in self.active_tasks[chat_id]:
                    break

                if i == 1 and original_msg:
                    await original_msg.edit(message)
                else:
                    await self.client.send_message(chat_id, message)

                await self.log(
                    f"📤 [{task_id}] Відправлено {i}/{count}\n"
                    f"👤 Чат: {chat_name}\n"
                    f"💬 Текст: {message}\n"
                    f"⏱ Затримка: {time_fmt}"
                )
                self.db.update_sent_count(task_id, i)

                if i < count:
                    await asyncio.sleep(delay)

            if chat_id in self.active_tasks and task_id in self.active_tasks[chat_id]:
                await self.log(
                    f"✅ [{task_id}] Розсилку завершено!\n\n"
                    f"👤 Чат: {chat_name}\n"
                    f"📊 Відправлено: {count}\n"
                    f"💬 Текст: {message}\n"
                    f"⏱ Затримка: {time_fmt}"
                )
                self.db.remove_spam_task(task_id)
                self._cleanup_task(chat_id, task_id)

        except asyncio.CancelledError:
            self._cleanup_task(chat_id, task_id)
            raise

        except Exception as e:
            await self.log(f"❌ [{task_id}] Помилка!\n👤 {chat_name}\n💬 {message}\n⚠️ {e}")
            self.db.remove_spam_task(task_id)
            self._cleanup_task(chat_id, task_id)

    # ============ ДОПОМІЖНІ ДЛЯ КОМАНД ============

    async def _stop_one(self, task_id: str, chat_info: str) -> None:
        row = self.db.get_spam_task(task_id)
        if not row:
            await self.log(f"{chat_info}❌ Розсилку `{task_id}` не знайдено.")
            return
        chat_id = row['chat_id']
        task = self.active_tasks.get(chat_id, {}).get(task_id)
        if task:
            task.cancel()
            await asyncio.sleep(0)
        self.db.remove_spam_task(task_id)
        self._cleanup_task(chat_id, task_id)
        await self.log(
            f"{chat_info}⛔️ [{task_id}] Зупинено і видалено.\n"
            f"👤 {await self.get_chat_name(chat_id)} · 📊 {row['sent_count']}/{row['total_count']}"
        )

    async def _stop_all(self, chat_info: str) -> None:
        all_tasks = self.db.get_all_spam_tasks()
        if not all_tasks and not self.active_tasks:
            await self.log(f"{chat_info}ℹ️ Немає активних розсилок.")
            return
        for chat_tasks in self.active_tasks.values():
            for task in chat_tasks.values():
                task.cancel()
        await asyncio.sleep(0)
        self.active_tasks.clear()
        for row in all_tasks:
            self.db.remove_spam_task(row['task_id'])
        await self.log(f"{chat_info}⛔️ Зупинено і видалено {len(all_tasks)} розсилок.")

    async def _pause_one(self, task_id: str, chat_info: str) -> None:
        row = self.db.get_spam_task(task_id)
        if not row:
            await self.log(f"{chat_info}❌ Розсилку `{task_id}` не знайдено.")
            return
        chat_id = row['chat_id']
        task = self.active_tasks.get(chat_id, {}).get(task_id)
        if task:
            task.cancel()
            await asyncio.sleep(0)
        self.db.set_task_status(task_id, 'paused')
        await self.log(
            f"⏸ [{task_id}] Призупинено.\n"
            f"👤 {await self.get_chat_name(chat_id)} · 📊 {row['sent_count']}/{row['total_count']}\n"
            f"`!continue {task_id}` — продовжити"
        )

    async def _pause_all(self, chat_info: str) -> None:
        all_tasks = self.db.get_all_spam_tasks(status='active')
        if not all_tasks and not self.active_tasks:
            await self.log(f"{chat_info}ℹ️ Немає активних розсилок.")
            return
        for chat_tasks in self.active_tasks.values():
            for task in chat_tasks.values():
                task.cancel()
        await asyncio.sleep(0)
        for row in all_tasks:
            self.db.set_task_status(row['task_id'], 'paused')
        await self.log(
            f"{chat_info}⏸ Призупинено {len(all_tasks)} розсилок.\n"
            f"`!continueall` — відновити всі"
        )

    async def _resume_one(self, task_id: str, chat_info: str, silent: bool = False) -> bool:
        row = self.db.get_spam_task(task_id)
        if not row:
            if not silent:
                await self.log(f"{chat_info}❌ Розсилку `{task_id}` не знайдено.")
            return False
        if row['status'] != 'paused':
            if not silent:
                await self.log(f"{chat_info}⚠️ [{task_id}] не призупинена (статус: {row['status']}).")
            return False

        chat_id   = row['chat_id']
        remaining = row['total_count'] - row['sent_count']
        if remaining <= 0:
            self.db.remove_spam_task(task_id)
            if not silent:
                await self.log(f"{chat_info}ℹ️ [{task_id}] вже завершена, видалено з БД.")
            return False

        initial_wait = get_remaining_wait(row)
        self.db.set_task_status(task_id, 'active')

        if not silent:
            wait_str = f"чекати {format_time(initial_wait)}" if initial_wait > 0 else "відправляє одразу"
            await self.log(
                f"▶️ [{task_id}] Відновлено ({wait_str}).\n"
                f"👤 {await self.get_chat_name(chat_id)} · 📊 {row['sent_count']}/{row['total_count']}"
            )

        self._start_task(chat_id, task_id,
            self.send_spam_messages(chat_id, task_id, row['message'], row['delay'], remaining,
                                    initial_wait=initial_wait))
        return True

    # ============ ОБРОБНИКИ КОМАНД ============

    def _register_handlers(self) -> None:

        @self.client.on(events.NewMessage(outgoing=True, pattern=r'^!spam'))
        async def spam_handler(event) -> None:
            parsed = parse_command(event.raw_text)
            chat_id = event.chat_id
            chat_info = await self.get_chat_info(chat_id)

            if not parsed:
                await self.log(
                    f"{chat_info}❌ **Невірний формат!**\n\n"
                    "`!spam <час> <кількість> <текст>`\n\n"
                    "⏱ Формати: `30с`, `5хв`, `2г`, `1д`, `1г30хв`\n"
                    "Приклад: `!spam 30с 10 Привіт як справи`"
                )
                await event.delete()
                return

            delay, count, message = parsed
            if count <= 0:
                await self.log(f"{chat_info}❌ Кількість має бути > 0.")
                await event.delete()
                return

            task_id = self.db.make_task_id()
            chat_name = await self.get_chat_name(chat_id)
            existing = len(self.active_tasks.get(chat_id, {}))
            extra = f"\n⚡️ Активних у цьому чаті: {existing + 1}" if existing > 0 else ""

            self.db.add_spam_task(task_id, chat_id, message, delay, count, int(time.time()))
            await self.log(
                f"🚀 [{task_id}] Розсилку запущено!{extra}\n\n"
                f"👤 {chat_name}\n💬 {message}\n"
                f"⏱ {format_time(delay)} · 🔢 {count}\n\n"
                f"`!stop {task_id}` — зупинити | `!stop` — всі"
            )
            self._start_task(chat_id, task_id,
                self.send_spam_messages(chat_id, task_id, message, delay, count, event.message))

        @self.client.on(events.NewMessage(outgoing=True, pattern=r'^!stop'))
        async def stop_handler(event) -> None:
            chat_info = await self.get_chat_info(event.chat_id)
            parts = event.raw_text.strip().split()
            if len(parts) > 1:
                await self._stop_one(parts[1], chat_info)
            else:
                await self._stop_all(chat_info)
            await event.delete()

        @self.client.on(events.NewMessage(outgoing=True, pattern=r'^!pause(?!all)'))
        async def pause_handler(event) -> None:
            chat_info = await self.get_chat_info(event.chat_id)
            parts = event.raw_text.strip().split()
            if len(parts) < 2:
                await self.log(f"{chat_info}❌ Вкажіть ID: `!pause <id>`")
                await event.delete()
                return
            await self._pause_one(parts[1], chat_info)
            await event.delete()

        @self.client.on(events.NewMessage(outgoing=True, pattern=r'^!pauseall'))
        async def pauseall_handler(event) -> None:
            await self._pause_all(await self.get_chat_info(event.chat_id))
            await event.delete()

        @self.client.on(events.NewMessage(outgoing=True, pattern=r'^!continue(?!all)'))
        async def continue_handler(event) -> None:
            chat_info = await self.get_chat_info(event.chat_id)
            parts = event.raw_text.strip().split()
            if len(parts) < 2:
                await self.log(f"{chat_info}❌ Вкажіть ID: `!continue <id>`")
                await event.delete()
                return
            await self._resume_one(parts[1], chat_info)
            await event.delete()

        @self.client.on(events.NewMessage(outgoing=True, pattern=r'^!continueall'))
        async def continueall_handler(event) -> None:
            chat_info = await self.get_chat_info(event.chat_id)
            paused = self.db.get_all_spam_tasks(status='paused')
            if not paused:
                await self.log(f"{chat_info}ℹ️ Немає призупинених розсилок.")
                await event.delete()
                return
            resumed = 0
            for row in paused:
                if await self._resume_one(row['task_id'], chat_info, silent=True):
                    resumed += 1
            await self.log(f"{chat_info}▶️ Відновлено {resumed} розсилок.")
            await event.delete()

        @self.client.on(events.NewMessage(outgoing=True, pattern=r'^!status'))
        async def status_handler(event) -> None:
            chat_info = await self.get_chat_info(event.chat_id)
            all_tasks = self.db.get_all_spam_tasks()
            if not all_tasks:
                await self.log(f"{chat_info}ℹ️ Немає розсилок.")
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
                cname      = await self.get_chat_name(cid)
                lines.append(
                    f"• [{tid}] {cname}\n"
                    f"  {status_str}\n"
                    f"  💬 {msg[:40]}{'...' if len(msg) > 40 else ''}\n"
                    f"  📊 {sent}/{total} · ⏱ {format_time(delay)}\n"
                    f"  ⏳ Наступне через: {format_time(next_in)}\n"
                )
            await self.log(
                f"{chat_info}📊 **Розсилки:**\n\n" + "".join(lines) +
                "\n`!stop <id>` · `!stop` · `!pause <id>` · `!pauseall` · `!continue <id>` · `!continueall`"
            )
            await event.delete()

        @self.client.on(events.NewMessage(outgoing=True, pattern=r'^!help'))
        async def help_handler(event) -> None:
            chat_info = await self.get_chat_info(event.chat_id)
            await self.log(
                f"{chat_info}🤖 **Команди Userbot**\n\n"
                "📤 `!spam <час> <кількість> <текст>` — запустити розсилку\n"
                "   Можна запускати кілька паралельно в одному чаті.\n\n"
                "⛔️ `!stop <id>` — зупинити і видалити конкретну\n"
                "⛔️ `!stop` — зупинити і видалити **всі**\n\n"
                "⏸ `!pause <id>` — призупинити конкретну\n"
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

        @self.client.on(events.NewMessage(outgoing=True, pattern=r'^!setlog'))
        async def setlog_handler(event) -> None:
            chat_id = event.chat_id
            self.log_chat = chat_id
            self.db.set_config('log_chat_id', chat_id)
            await self.log(f"✅ Чат для логів: **{await self.get_chat_name(chat_id)}** (`{chat_id}`)")
            await event.delete()

        @self.client.on(events.NewMessage(outgoing=True, pattern=r'^!chatid'))
        async def chatid_handler(event) -> None:
            chat_id = event.chat_id
            await self.log(f"ℹ️ **{await self.get_chat_name(chat_id)}**\n🆔 `{chat_id}`")
            await event.delete()

        @self.client.on(events.NewMessage(outgoing=True, pattern=r'^!start'))
        async def start_handler(event) -> None:
            chat_info = await self.get_chat_info(event.chat_id)
            await self.log(
                f"{chat_info}👋 **Вітаю!**\n\n"
                "Цей бот допоможе надсилати повторювані повідомлення автоматично.\n\n"
                "**Що можна робити:**\n"
                "• Запустити розсилку: `!spam 30с 10 Текст повідомлення`\n"
                "• Кілька розсилок одночасно в одному чаті\n"
                "• Призупинити і продовжити в будь-який момент\n"
                "• Переглянути статус: `!status`\n\n"
                "**⚙️ Перший крок:**\n"
                "Відкрий чат куди хочеш отримувати звіти і напиши `!setlog`\n\n"
                "Повний список команд: `!help`"
            )
            await event.delete()

    # ============ ЗАПУСК ============

    async def start(self) -> None:
        init_db(self.account_id)

        saved = self.db.get_config('log_chat_id', default=None)
        if saved and saved != 'me':
            self.log_chat = int(saved)

        await self.client.start(phone=self.phone)

        me = await self.client.get_me()
        self.username = f"@{me.username}" if me.username else me.first_name

        self._log(f"✅ Запущено, LOG_CHAT={self.log_chat}")

        if isinstance(self.log_chat, int):
            async for dialog in self.client.iter_dialogs(limit=100):
                if dialog.id == self.log_chat:
                    self._log(f"✅ Лог-чат: {dialog.name}")
                    break

        # Відновлення активних розсилок
        for row in self.db.get_all_spam_tasks(status='active'):
            tid       = row['task_id']
            cid       = row['chat_id']
            remaining = row['total_count'] - row['sent_count']
            if remaining > 0:
                initial_wait = get_remaining_wait(row)
                self._start_task(cid, tid,
                    self.send_spam_messages(cid, tid, row['message'], row['delay'], remaining,
                                            initial_wait=initial_wait))
                self._log(f"✅ [{tid}] відновлено, залишилось: {remaining}, чекати: {initial_wait}с")
            else:
                self.db.remove_spam_task(tid)

        await self.log(
            "✅ Userbot запущено!\n\n"
            "📝 Цей чат — для логів\n"
            "`!help` — довідка · `!setlog` — змінити чат логів"
        )

    async def run(self) -> None:
        await self.start()
        await self.client.run_until_disconnected()

    def stop(self) -> None:
        for chat_tasks in self.active_tasks.values():
            for task in chat_tasks.values():
                task.cancel()


# ============ ТОЧКА ВХОДУ ============

async def main() -> None:
    accounts_cfg = load_accounts()
    if not accounts_cfg:
        print("[ERROR] Не знайдено жодного акаунта в .env!")
        print("[ERROR] Формат: ACCOUNT_1_API_ID, ACCOUNT_1_API_HASH, ACCOUNT_1_PHONE")
        return

    accounts = [Account(**cfg) for cfg in accounts_cfg]
    print(f"[INFO] Завантажено {len(accounts)} акаунт(ів)")

    stop_event = asyncio.Event()

    async def _shutdown(sig: signal.Signals) -> None:
        print(f"[INFO] Отримано сигнал {sig.name}, зберігаємо стан...")
        for acc in accounts:
            acc.stop()
        await asyncio.gather(*[acc.client.disconnect() for acc in accounts])
        print("[INFO] Виходимо.")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(_shutdown(s)))

    for acc in accounts:
        await acc.start()

    print("[INFO] ⛔️ Ctrl+C для виходу")

    # Після авторизації — запускаємо всіх паралельно
    await asyncio.gather(*[acc.client.run_until_disconnected() for acc in accounts])


if __name__ == '__main__':
    asyncio.run(main())