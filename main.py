from telethon import TelegramClient, events
import asyncio
import re
import os
import time
import signal
import datetime

from database import DB, init_db

# ============ ПАРСИНГ ============

def parse_time(time_str: str) -> int | None:
    """Парсить затримку: 30с, 5хв, 2г, 1д."""
    time_str = time_str.lower().strip()
    units = {
        'с': 1, 's': 1, 'хв': 60, 'м': 60, 'm': 60,
        'г': 3600, 'ч': 3600, 'h': 3600, 'д': 86400, 'd': 86400,
    }
    matches = re.findall(r'(\d+)\s*([a-zа-яії]+)', time_str)
    if not matches:
        return None
    total = 0
    for val, unit in matches:
        mult = next((m for k, m in units.items() if unit.startswith(k)), None)
        if mult is None:
            return None
        total += int(val) * mult
    return total if total > 0 else None


def parse_time_of_day(time_str: str) -> tuple[int, int] | None:
    """Парсить час доби: 14:30, 2:30pm. Повертає (години, хвилини)."""
    time_str = time_str.lower().strip()
    
    # 12-годинний з am/pm
    m = re.match(r'^(\d{1,2}):(\d{2})\s*(am|pm|ам|пм)$', time_str)
    if m:
        h, mn, period = int(m.group(1)), int(m.group(2)), m.group(3)
        if h < 1 or h > 12 or mn > 59:
            return None
        if period in ('pm', 'пм') and h != 12:
            h += 12
        elif period in ('am', 'ам') and h == 12:
            h = 0
        return h, mn
    
    # 24-годинний
    m = re.match(r'^(\d{1,2}):(\d{2})$', time_str)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        if h > 23 or mn > 59:
            return None
        return h, mn
    
    return None


def parse_weekdays(days_str: str) -> list[int] | None:
    """Парсить дні: пн,ср,пт або mo,we,fr. Повертає список 0-6."""
    days_map = {
        'пн': 0, 'mo': 0, 'вт': 1, 'tu': 1, 'ср': 2, 'we': 2,
        'чт': 3, 'th': 3, 'пт': 4, 'fr': 4, 'сб': 5, 'sa': 5, 'нд': 6, 'su': 6,
    }
    parts = [p.strip().lower() for p in days_str.split(',')]
    result = []
    for p in parts:
        if p not in days_map:
            return None
        if days_map[p] not in result:
            result.append(days_map[p])
    return sorted(result) if result else None


def parse_command(text: str) -> tuple[str, int, int, tuple[int, int] | None, list[int] | None] | None:
    """
    !spam <текст> <затримка> <кількість> [час] [дні]
    Повертає: (message, delay, count, time_of_day, weekdays)
    """
    # Прибираємо префікс
    text = text.strip()
    if not text.lower().startswith('!spam'):
        return None
    rest = text[5:].strip()
    if not rest:
        return None

    tokens = rest.split()

    # Шукаємо з кінця:
    # - weekdays — останній токен якщо містить лише літери/коми
    # - time_of_day — передостанній якщо схожий на час
    # - count — перший числовий токен з кінця після опціональних
    # - delay — токен перед count
    # - message — все що залишилось

    weekdays = None
    time_of_day = None

    # Пробуємо зняти weekdays з кінця
    if tokens and re.match(r'^[а-яa-z,]+$', tokens[-1], re.IGNORECASE):
        parsed_wd = parse_weekdays(tokens[-1])
        if parsed_wd is not None:
            weekdays = parsed_wd
            tokens = tokens[:-1]

    # Пробуємо зняти time_of_day з кінця
    if tokens and re.match(r'^[0-9:apmапмАМПМ]+$', tokens[-1], re.IGNORECASE):
        parsed_t = parse_time_of_day(tokens[-1])
        if parsed_t is not None:
            time_of_day = parsed_t
            tokens = tokens[:-1]

    # Тепер з кінця: count (ціле число), delay, решта = message
    if len(tokens) < 3:
        return None

    # count
    if not tokens[-1].isdigit():
        return None
    count = int(tokens[-1])
    tokens = tokens[:-1]

    # delay — останній токен що залишився перед message
    delay = parse_time(tokens[-1])
    if delay is None:
        return None
    tokens = tokens[:-1]

    # message — все що залишилось
    if not tokens:
        return None
    message = ' '.join(tokens)

    if count <= 0:
        return None

    return message, delay, count, time_of_day, weekdays


def format_time(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}с"
    elif seconds < 3600:
        mn, s = divmod(seconds, 60)
        return f"{mn}хв" + (f" {s}с" if s else "")
    elif seconds < 86400:
        h, mn = divmod(seconds, 3600)[0], divmod(seconds, 3600)[1] // 60
        return f"{h}г" + (f" {mn}хв" if mn else "")
    else:
        d, h = divmod(seconds, 86400)[0], divmod(seconds, 86400)[1] // 3600
        return f"{d}д" + (f" {h}г" if h else "")


def parse_weekdays_from_db(s: str | None) -> list[int] | None:
    return [int(d) for d in s.split(',')] if s else None


def calculate_next_send_time(last_sent: int, delay: int, weekdays: list[int] | None) -> int:
    """
    Рахує наступний час відправлення після першого.
    scheduled_time більше не потрібен — він лише для першого відправлення.
    """
    next_time = last_sent + delay

    if not weekdays:
        return next_time

    # Перевіряємо чи день підходить
    next_dt = datetime.datetime.fromtimestamp(next_time)
    if next_dt.weekday() in weekdays:
        return next_time

    # Зсуваємо на наступний дозволений день, зберігаючи час доби
    target_time = next_dt.time()
    current_date = next_dt.date()
    current_wd = next_dt.weekday()

    days_ahead = next((wd - current_wd for wd in sorted(weekdays) if wd > current_wd), None)
    if days_ahead is None:
        days_ahead = 7 - current_wd + weekdays[0]

    target_dt = datetime.datetime.combine(
        current_date + datetime.timedelta(days=days_ahead), target_time
    )
    return int(target_dt.timestamp())


def get_first_send_time(scheduled_time: int | None, weekdays: list[int] | None) -> int:
    """Рахує час першого відправлення."""
    now = datetime.datetime.now()
    
    if not scheduled_time:
        # Без фіксованого часу — відправляємо одразу якщо день підходить
        if not weekdays or now.weekday() in weekdays:
            return int(now.timestamp())
        
        # Шукаємо наступний дозволений день о 00:00
        current_wd = now.weekday()
        days_ahead = next((wd - current_wd for wd in sorted(weekdays) if wd > current_wd), None)
        if days_ahead is None:
            days_ahead = 7 - current_wd + weekdays[0]
        
        target_dt = datetime.datetime.combine(now.date() + datetime.timedelta(days=days_ahead), datetime.time(0, 0))
        return int(target_dt.timestamp())
    
    # Є фіксований час
    h, mn = divmod(scheduled_time, 60)
    target_time = datetime.time(h, mn)
    target_dt = datetime.datetime.combine(now.date(), target_time)
    
    # Якщо час ще не минув сьогодні і день підходить
    if target_dt > now and (not weekdays or now.weekday() in weekdays):
        return int(target_dt.timestamp())
    
    # Інакше — завтра або наступний дозволений день
    next_date = now.date() + datetime.timedelta(days=1)
    next_dt = datetime.datetime.combine(next_date, target_time)
    
    if not weekdays or next_dt.weekday() in weekdays:
        return int(next_dt.timestamp())
    
    # Шукаємо наступний дозволений день
    current_wd = next_dt.weekday()
    days_ahead = next((wd - current_wd for wd in sorted(weekdays) if wd > current_wd), None)
    if days_ahead is None:
        days_ahead = 7 - current_wd + weekdays[0]
    
    target_dt = datetime.datetime.combine(next_date + datetime.timedelta(days=days_ahead), target_time)
    return int(target_dt.timestamp())


def load_accounts() -> list[dict]:
    accounts = []
    i = 1
    while True:
        api_id = os.getenv(f'ACCOUNT_{i}_API_ID')
        api_hash = os.getenv(f'ACCOUNT_{i}_API_HASH')
        phone = os.getenv(f'ACCOUNT_{i}_PHONE')
        if not api_id or not api_hash or not phone:
            break
        accounts.append({
            'account_id': f'account_{i}',
            'api_id': int(api_id),
            'api_hash': api_hash,
            'phone': phone,
        })
        i += 1
    return accounts


# ============ КЛАС АКАУНТА ============

class Account:
    def __init__(self, account_id: str, api_id: int, api_hash: str, phone: str) -> None:
        self.account_id = account_id
        self.phone = phone
        self.username = account_id
        self.db = DB(account_id)

        session_dir = os.path.join('data', account_id)
        os.makedirs(session_dir, exist_ok=True)

        self.client = TelegramClient(os.path.join(session_dir, 'session'), api_id, api_hash)
        self.log_chat: int | str = 'me'
        self.active_tasks: dict[int, dict[str, asyncio.Task]] = {}
        self._register_handlers()

    def _log(self, msg: str) -> None:
        print(f"[{self.username}] {msg}")

    def _cleanup(self, cid: int, tid: str) -> None:
        if cid in self.active_tasks:
            self.active_tasks[cid].pop(tid, None)
            if not self.active_tasks[cid]:
                del self.active_tasks[cid]

    def _start(self, cid: int, tid: str, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.active_tasks.setdefault(cid, {})[tid] = task
        return task

    async def log(self, msg: str) -> None:
        try:
            if isinstance(self.log_chat, int):
                try:
                    await self.client.send_message(await self.client.get_entity(self.log_chat), msg)
                except ValueError:
                    async for d in self.client.iter_dialogs():
                        if d.id == self.log_chat:
                            await self.client.send_message(d, msg)
                            return
                    raise
            else:
                await self.client.send_message(self.log_chat, msg)
        except Exception as e:
            self._log(f"[ERROR] {e}")

    async def get_chat_name(self, cid: int) -> str:
        try:
            c = await self.client.get_entity(cid)
            return getattr(c, 'title', None) or getattr(c, 'first_name', f'ID:{cid}')
        except Exception:
            return f"ID:{cid}"

    async def _spam(self, cid: int, tid: str, msg: str, delay: int, count: int, 
                    original=None, scheduled_time: int | None = None, weekdays: list[int] | None = None) -> None:
        i = 0
        cname = await self.get_chat_name(cid)
        
        try:
            # Перше відправлення
            first_time = get_first_send_time(scheduled_time, weekdays)
            wait = max(0, first_time - int(time.time()))
            
            if wait > 0:
                next_dt = datetime.datetime.fromtimestamp(first_time)
                self._log(f"[{tid}] Перше повідомлення: {next_dt.strftime('%d.%m %H:%M')} (через {format_time(wait)})")
                await asyncio.sleep(wait)

            for i in range(1, count + 1):
                if cid not in self.active_tasks or tid not in self.active_tasks[cid]:
                    break

                if i == 1 and original and wait == 0:
                    await original.edit(msg)
                else:
                    await self.client.send_message(cid, msg)

                current = int(time.time())
                await self.log(f"📤 [{tid}] {i}/{count}\n👤 {cname}\n💬 {msg}")
                self.db.update_sent_count(tid, i)

                if i < count:
                    next_time = calculate_next_send_time(current, delay, weekdays)  # ← прибрали scheduled_time
                    wait_sec = max(0, next_time - int(time.time()))
                    
                    if wait_sec > delay + 3600:
                        ndt = datetime.datetime.fromtimestamp(next_time)
                        self._log(f"[{tid}] Наступне: {ndt.strftime('%d.%m %H:%M')} (через {format_time(wait_sec)})")
                    
                    await asyncio.sleep(wait_sec)

            if cid in self.active_tasks and tid in self.active_tasks[cid]:
                await self.log(f"✅ [{tid}] Завершено\n👤 {cname} · 📊 {count}")
                self.db.remove_spam_task(tid)
                self._cleanup(cid, tid)

        except asyncio.CancelledError:
            self._cleanup(cid, tid)
            raise
        except Exception as e:
            await self.log(f"❌ [{tid}] Помилка\n👤 {cname}\n⚠️ {e}")
            self.db.remove_spam_task(tid)
            self._cleanup(cid, tid)

    # ============ ОБРОБНИКИ КОМАНД ============

    async def _handle_spam(self, e) -> None:
        parsed = parse_command(e.raw_text)
        cid = e.chat_id
        
        if not parsed:
            await self.log(
                "❌ Невірний формат\n\n"
                "`!spam <текст> <затримка> <кількість> [час] [дні]`\n\n"
                "Приклади:\n"
                "`!spam Привіт 1д 10` — щодня в цей же час\n"
                "`!spam Привіт 1д 10 14:30` — щодня о 14:30\n"
                "`!spam Привіт 1д 10 2:30pm пн,ср` — о 14:30 тільки пн/ср"
            )
            await e.delete()
            return

        message, delay, count, time_of_day, weekdays = parsed
        if count <= 0:
            await self.log("❌ Кількість > 0")
            await e.delete()
            return

        tid = self.db.make_task_id()
        scheduled_time = time_of_day[0] * 60 + time_of_day[1] if time_of_day else None
        
        self.db.add_spam_task(tid, cid, message, delay, count, int(time.time()), weekdays, scheduled_time)
        
        wd_names = {0:'пн',1:'вт',2:'ср',3:'чт',4:'пт',5:'сб',6:'нд'}
        info = f"\n📅 {','.join(wd_names[d] for d in weekdays)}" if weekdays else ""
        if time_of_day:
            info += f" о {time_of_day[0]:02d}:{time_of_day[1]:02d}"
        
        await self.log(f"🚀 [{tid}] Запущено{info}\n👤 {await self.get_chat_name(cid)}\n💬 {message}")
        
        should_delete = False
        if weekdays or time_of_day:
            first_time = get_first_send_time(scheduled_time, weekdays)
            should_delete = first_time > int(time.time()) + 60
        
        self._start(cid, tid, self._spam(cid, tid, message, delay, count, 
                                         None if should_delete else e.message, 
                                         scheduled_time, weekdays))
        if should_delete:
            await e.delete()

    async def _handle_stop(self, e) -> None:
        parts = e.raw_text.strip().split()
        if len(parts) > 1:
            tid = parts[1]
            row = self.db.get_spam_task(tid)
            if row:
                t = self.active_tasks.get(row['chat_id'], {}).get(tid)
                if t:
                    t.cancel()
                    await asyncio.sleep(0)
                self.db.remove_spam_task(tid)
                self._cleanup(row['chat_id'], tid)
                await self.log(f"⛔️ [{tid}] Зупинено")
            else:
                await self.log(f"❌ [{tid}] не знайдено")
        else:
            all_t = self.db.get_all_spam_tasks()
            for ct in self.active_tasks.values():
                for t in ct.values():
                    t.cancel()
            await asyncio.sleep(0)
            self.active_tasks.clear()
            for r in all_t:
                self.db.remove_spam_task(r['task_id'])
            await self.log(f"⛔️ Зупинено {len(all_t)}")
        await e.delete()

    async def _handle_pause(self, e) -> None:
        parts = e.raw_text.strip().split()
        if len(parts) < 2:
            await self.log("❌ `!pause <id>`")
            await e.delete()
            return
        tid = parts[1]
        row = self.db.get_spam_task(tid)
        if row:
            t = self.active_tasks.get(row['chat_id'], {}).get(tid)
            if t:
                t.cancel()
                await asyncio.sleep(0)
            self.db.set_task_status(tid, 'paused')
            await self.log(f"⏸ [{tid}] Призупинено")
        else:
            await self.log(f"❌ [{tid}] не знайдено")
        await e.delete()

    async def _handle_pauseall(self, e) -> None:
        all_t = self.db.get_all_spam_tasks(status='active')
        for ct in self.active_tasks.values():
            for t in ct.values():
                t.cancel()
        await asyncio.sleep(0)
        for r in all_t:
            self.db.set_task_status(r['task_id'], 'paused')
        await self.log(f"⏸ Призупинено {len(all_t)}")
        await e.delete()

    async def _handle_continue(self, e) -> None:
        parts = e.raw_text.strip().split()
        if len(parts) < 2:
            await self.log("❌ `!continue <id>`")
            await e.delete()
            return
        tid = parts[1]
        row = self.db.get_spam_task(tid)
        if not row or row['status'] != 'paused':
            await self.log(f"❌ [{tid}] не знайдено або не призупинена")
            await e.delete()
            return
        
        remaining = row['total_count'] - row['sent_count']
        if remaining <= 0:
            self.db.remove_spam_task(tid)
            await self.log(f"ℹ️ [{tid}] завершена")
            await e.delete()
            return
        
        self.db.set_task_status(tid, 'active')
        await self.log(f"▶️ [{tid}] Відновлено")
        
        wd = parse_weekdays_from_db(row['weekdays'] if 'weekdays' in row.keys() else None)
        st = row['scheduled_time'] if 'scheduled_time' in row.keys() else None
        
        self._start(row['chat_id'], tid,
                   self._spam(row['chat_id'], tid, row['message'], row['delay'], remaining,
                             scheduled_time=st, weekdays=wd))
        await e.delete()

    async def _handle_continueall(self, e) -> None:
        paused = self.db.get_all_spam_tasks(status='paused')
        resumed = 0
        for r in paused:
            remaining = r['total_count'] - r['sent_count']
            if remaining > 0:
                self.db.set_task_status(r['task_id'], 'active')
                wd = parse_weekdays_from_db(r['weekdays'] if 'weekdays' in r.keys() else None)
                st = r['scheduled_time'] if 'scheduled_time' in r.keys() else None
                self._start(r['chat_id'], r['task_id'],
                           self._spam(r['chat_id'], r['task_id'], r['message'], r['delay'], remaining,
                                     scheduled_time=st, weekdays=wd))
                resumed += 1
            else:
                self.db.remove_spam_task(r['task_id'])
        await self.log(f"▶️ Відновлено {resumed}")
        await e.delete()

    async def _handle_status(self, e) -> None:
        all_t = self.db.get_all_spam_tasks()
        if not all_t:
            await self.log("ℹ️ Немає розсилок")
            await e.delete()
            return
        lines = []
        for r in all_t:
            st = "▶️" if r['status'] == 'active' else "⏸"
            cn = await self.get_chat_name(r['chat_id'])
            msg_short = r['message'][:40] + ('...' if len(r['message']) > 40 else '')
            lines.append(
                f"• [{r['task_id']}] {st} {cn}\n"
                f"  💬 {msg_short}\n"
                f"  📊 {r['sent_count']}/{r['total_count']}\n"
            )
        await self.log("📊 Розсилки:\n\n" + "".join(lines))
        await e.delete()

    async def _handle_help(self, e) -> None:
        await self.log(
            "🤖 Команди\n\n"
            "📤 `!spam <текст> <затримка> <кількість> [час] [дні]`\n"
            "⛔️ `!stop <id>` | `!stop`\n"
            "⏸ `!pause <id>` | `!pauseall`\n"
            "▶️ `!continue <id>` | `!continueall`\n"
            "📊 `!status` · 🆔 `!chatid` · ⚙️ `!setlog` · 🚀 `!start`"
        )
        await e.delete()

    async def _handle_setlog(self, e) -> None:
        self.log_chat = e.chat_id
        self.db.set_config('log_chat_id', e.chat_id)
        await self.log(f"✅ Лог-чат: {await self.get_chat_name(e.chat_id)}")
        await e.delete()

    async def _handle_chatid(self, e) -> None:
        await self.log(f"🆔 {await self.get_chat_name(e.chat_id)}: `{e.chat_id}`")
        await e.delete()

    async def _handle_start(self, e) -> None:
        await self.log(
            "👋 Вітаю!\n\n"
            "Бот надсилає повторювані повідомлення.\n\n"
            "`!spam Привіт 1д 10` — щодня в цей час\n"
            "`!spam Привіт 1д 10 14:30 пн,ср` — о 14:30 тільки пн/ср\n\n"
            "Перший крок: `!setlog` в потрібному чаті\n"
            "`!help` — всі команди"
        )
        await e.delete()

    def _register_handlers(self) -> None:
        self.client.on(events.NewMessage(outgoing=True, pattern=r'^!spam'))(self._handle_spam)
        self.client.on(events.NewMessage(outgoing=True, pattern=r'^!stop'))(self._handle_stop)
        self.client.on(events.NewMessage(outgoing=True, pattern=r'^!pause(?!all)'))(self._handle_pause)
        self.client.on(events.NewMessage(outgoing=True, pattern=r'^!pauseall'))(self._handle_pauseall)
        self.client.on(events.NewMessage(outgoing=True, pattern=r'^!continue(?!all)'))(self._handle_continue)
        self.client.on(events.NewMessage(outgoing=True, pattern=r'^!continueall'))(self._handle_continueall)
        self.client.on(events.NewMessage(outgoing=True, pattern=r'^!status'))(self._handle_status)
        self.client.on(events.NewMessage(outgoing=True, pattern=r'^!help'))(self._handle_help)
        self.client.on(events.NewMessage(outgoing=True, pattern=r'^!setlog'))(self._handle_setlog)
        self.client.on(events.NewMessage(outgoing=True, pattern=r'^!chatid'))(self._handle_chatid)
        self.client.on(events.NewMessage(outgoing=True, pattern=r'^!start'))(self._handle_start)

    async def start(self) -> None:
        init_db(self.account_id)
        saved = self.db.get_config('log_chat_id', default=None)
        if saved and saved != 'me':
            self.log_chat = int(saved)

        await self.client.start(phone=self.phone)
        me = await self.client.get_me()
        self.username = f"@{me.username}" if me.username else me.first_name
        self._log("✅ Запущено")

        if isinstance(self.log_chat, int):
            async for d in self.client.iter_dialogs(limit=100):
                if d.id == self.log_chat:
                    self._log(f"✅ Лог-чат: {d.name}")
                    break

        for r in self.db.get_all_spam_tasks(status='active'):
            remaining = r['total_count'] - r['sent_count']
            if remaining > 0:
                wd = parse_weekdays_from_db(r['weekdays'] if 'weekdays' in r.keys() else None)
                st = r['scheduled_time'] if 'scheduled_time' in r.keys() else None
                self._start(r['chat_id'], r['task_id'],
                           self._spam(r['chat_id'], r['task_id'], r['message'], r['delay'], remaining,
                                     scheduled_time=st, weekdays=wd))
                self._log(f"✅ [{r['task_id']}] відновлено")
            else:
                self.db.remove_spam_task(r['task_id'])

        await self.log("✅ Userbot запущено\n`!help` — довідка")

    def stop(self) -> None:
        for ct in self.active_tasks.values():
            for t in ct.values():
                t.cancel()


# ============ MAIN ============

async def main() -> None:
    accounts_cfg = load_accounts()
    if not accounts_cfg:
        print("[ERROR] Не знайдено акаунтів в .env")
        return

    accounts = [Account(**c) for c in accounts_cfg]
    print(f"[INFO] Завантажено {len(accounts)} акаунт(ів)")

    stop_event = asyncio.Event()

    async def _shutdown(sig: signal.Signals) -> None:
        print(f"[INFO] {sig.name}, зберігаємо стан...")
        for a in accounts:
            a.stop()
        await asyncio.gather(*[a.client.disconnect() for a in accounts])
        print("[INFO] Виходимо")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(_shutdown(s)))

    for a in accounts:
        await a.start()

    print("[INFO] ⛔️ Ctrl+C для виходу")
    await asyncio.gather(*[a.client.run_until_disconnected() for a in accounts])


if __name__ == '__main__':
    asyncio.run(main())