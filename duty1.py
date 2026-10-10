import asyncio
import calendar
import html
import json
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================= SOZLAMALAR =================
TOKEN = "8191598505:AAHIYmkp8k4E6wZ_5Iv3oAGKmp0RKHTShdw"
ADMIN_ID = 6361189243
SECOND_ADMIN_ID = 0  # 2-admin (starosta yordamchisi) Telegram ID si. 0 bo'lsa — hali qo'yilmagan
MAX_MEMBERS = 16
DB = "navbatchi.db"

ATTENDANCE_RETENTION_DAYS = 135

DAYS = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma"]

SCHEDULE = {
    0: [
        {"period": 1, "subject": "Rus tili", "teacher": "Soliyeva M.", "room": "B-01", "time": "08:00-09:20"},
        {"period": 2, "subject": "Amaliy matematika (Ma)", "teacher": "Vaxobov V.", "room": "B-220", "time": "09:30-10:50"},
        {"period": 3, "subject": "AKT (Ma)", "teacher": "Aynakulov Sh.", "room": "B-220", "time": "11:00-12:20"},
    ],
    1: [
        {"period": 1, "subject": "Iqtisodiyot nazariyasi", "teacher": "Xushmotov N.", "room": "B-109", "time": "08:00-09:20"},
        {"period": 2, "subject": "Amaliy matematika (A)", "teacher": "Xidoyatova M.", "room": "B-06", "time": "09:30-10:50"},
        {"period": 3, "subject": "Ingliz tili", "teacher": "Yoqubova N.", "room": "B-114", "time": "11:00-12:20"},
    ],
    2: [
        {"period": 1, "subject": "Rus tili", "teacher": "Soliyeva M.", "room": "B-01", "time": "08:00-09:20"},
        {"period": 2, "subject": "AKT (A)", "teacher": "Pardayeva O.", "room": "B-02", "time": "09:30-10:50"},
        {"period": 3, "subject": "Iqtisodiy ta'limotlar tarixi (A)", "teacher": "Yax'yayev O.", "room": "B-206", "time": "11:00-12:20"},
    ],
    3: [
        {"period": 2, "subject": "Kelajak soati", "teacher": "Xaydarov Z.", "room": "G-2 zal", "time": "09:30-10:50"},
        {"period": 3, "subject": "Iqtisodiyot nazariyasi (A)", "teacher": "Jumanova G.", "room": "B-305", "time": "11:00-12:20"},
    ],
    4: [
        {"period": 2, "subject": "Ingliz tili", "teacher": "Yoqubova N.", "room": "B-117a", "time": "09:30-10:50"},
        {"period": 3, "subject": "Iqtisodiy ta'limotlar tarixi (Ma)", "teacher": "Sangirova U.", "room": "B-220", "time": "11:00-12:20"},
    ],
}

logging.basicConfig(level=logging.INFO)
bot = Bot(TOKEN)
dp = Dispatcher()
ADMIN_IDS = [i for i in (ADMIN_ID, SECOND_ADMIN_ID) if i]
is_admin = F.from_user.id.in_(ADMIN_IDS)
try:
    TZ = ZoneInfo("Asia/Tashkent")
except Exception:  # tz bazasi topilmasa: Toshkent = UTC+5 (yozgi vaqt yo'q)
    TZ = timezone(timedelta(hours=5))


def tz_now():
    return datetime.now(TZ)


def tz_today():
    return tz_now().date()


async def notify_admins(text, **kwargs):
    """Barcha adminlarga xabar yuboradi (biriga yuborilmasa, qolganlariga to'xtamaydi)."""
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, **kwargs)
        except Exception as e:
            logging.warning("Adminga xabar yuborilmadi (%s): %s", admin_id, e)

WELCOME = (
    "👋 Sinf navbatchilik botiga xush kelibsiz!\n\n"
    "Bu bot navbatchilikni tartibli yuritish uchun kerak:\n"
    "• har kuni ertalab hammaga \"darsga kelasizmi?\" so'rovi boradi\n"
    "• navbatdagi kishi \"Ha\" desa, o'sha kuni navbatchi bo'ladi\n"
    "• u \"Yo'q\" desa, navbat avtomatik keyingi kishiga o'tadi\n\n"
    "/bugun — bugungi navbatchini ko'rsatadi\n"
    "/yordam — shu xabarni qayta ko'rsatadi"
)

menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📞 Bog'lanish"), KeyboardButton(text="📅 Raspisaniya")],
        [KeyboardButton(text="👥 Navbatchilik ro'yxati")],
    ],
    resize_keyboard=True,
)

def db():
    return sqlite3.connect(DB)

def init_db():
    with db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS members(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER UNIQUE,
                full_name TEXT,
                birth_date TEXT,
                joined_at TEXT)"""
        )
        c.execute("CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT)")
        c.execute(
            """CREATE TABLE IF NOT EXISTS attendance(
                slot TEXT,
                tg_id INTEGER,
                status TEXT,
                reason TEXT,
                PRIMARY KEY(slot, tg_id))"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS homework(
                subject TEXT PRIMARY KEY,
                items TEXT,
                author_id INTEGER,
                author_name TEXT,
                lesson_date TEXT,
                saved_at TEXT)"""
        )

def get_state(key, default=None):
    with db() as c:
        row = c.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else default

def set_state(key, value):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (key, str(value)))

def all_members():
    with db() as c:
        return c.execute(
            "SELECT tg_id, full_name, birth_date FROM members ORDER BY id"
        ).fetchall()

def set_attendance(slot, tg_id, status, reason=None):
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO attendance(slot, tg_id, status, reason) VALUES(?,?,?,?)",
            (slot, tg_id, status, reason),
        )

def get_attendance(slot):
    with db() as c:
        rows = c.execute(
            "SELECT tg_id, status, reason FROM attendance WHERE slot=?", (slot,)
        ).fetchall()
    return {tg_id: (status, reason) for tg_id, status, reason in rows}

def cleanup_old_attendance():
    cutoff = (date.today() - timedelta(days=ATTENDANCE_RETENTION_DAYS)).isoformat()
    with db() as c:
        cur = c.execute("DELETE FROM attendance WHERE slot < ?", (cutoff,))
        return cur.rowcount

class Reg(StatesGroup):
    name = State()
    birth = State()

class AdminMsg(StatesGroup):
    choose = State()
    text = State()

class AttendanceReason(StatesGroup):
    waiting = State()

class ContactAdmin(StatesGroup):
    waiting = State()

class AdminReply(StatesGroup):
    waiting = State()

class HomeworkFlow(StatesGroup):
    waiting = State()

class NewsFlow(StatesGroup):
    waiting = State()

class BirthdayPhoto(StatesGroup):
    waiting = State()

class Restore(StatesGroup):
    waiting = State()

@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await m.answer(WELCOME)
    with db() as c:
        exists = c.execute(
            "SELECT 1 FROM members WHERE tg_id=?", (m.from_user.id,)
        ).fetchone()
    if exists:
        await m.answer("Siz allaqachon ro'yxatdasiz ✅", reply_markup=build_menu_kb(m.from_user.id))
        return
    await state.set_state(Reg.name)
    await m.answer("Boshlaymiz. Ism and familyangizni yozing (masalan: Ali Valiyev):")

@dp.message(Command("yordam"))
async def cmd_help(m: Message):
    await m.answer(WELCOME)

@dp.message(Reg.name, F.text)
async def get_name(m: Message, state: FSMContext):
    name = m.text.strip()
    if len(name.split()) < 2:
        await m.answer("Iltimos, ism va familyani birga yozing.")
        return
    await state.update_data(name=name)
    await state.set_state(Reg.birth)
    await m.answer("Tug'ilgan sanangizni yozing (KK.OO.YYYY, masalan: 15.03.2006):")

@dp.message(Reg.birth, F.text)
async def get_birth(m: Message, state: FSMContext):
    try:
        b = datetime.strptime(m.text.strip(), "%d.%m.%Y").date()
        if b >= date.today():
            raise ValueError
    except ValueError:
        await m.answer("Sana noto'g'ri. Format: KK.OO.YYYY (masalan: 15.03.2006)")
        return
    data = await state.get_data()
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO members(tg_id, full_name, birth_date, joined_at) "
            "VALUES(?,?,?,COALESCE((SELECT joined_at FROM members WHERE tg_id=?), ?))",
            (m.from_user.id, data["name"], b.isoformat(), m.from_user.id,
             datetime.now().isoformat(timespec="seconds")),
        )
    await state.clear()
    await m.answer(f"Ro'yxatdan o'tdingiz, {data['name']}! ✅", reply_markup=build_menu_kb(m.from_user.id))

    total = len(all_members())
    username = f" (@{m.from_user.username})" if m.from_user.username else ""
    await notify_admins(f"🆕 Yangi a'zo: {data['name']}{username}\nJami: {total}/{MAX_MEMBERS}")

def _current_slot():
    return date.today().isoformat()

def _format_lesson(l):
    return f"<b>{l['period']}. {l['subject']}</b>\n<i>{l['teacher']}</i> · {l['room']} · {l['time']}"

def lessons_text():
    today = date.today()
    lessons = SCHEDULE.get(today.weekday(), [])
    if not lessons:
        return "<i>Bugun dars yo'q.</i>"
    return "\n\n".join(_format_lesson(l) for l in lessons)

def init_duty_candidate():
    members = all_members()
    if not members:
        return
    slot = _current_slot()
    if get_state("duty_slot") != slot:
        idx = int(get_state("idx", 0)) % len(members)
        set_state("duty_slot", slot)
        set_state("duty_start_idx", idx)
        set_state("duty_candidate_idx", idx)
        set_state("duty_resolved_tg", "")
        set_state("duty_resolved_name", "")
        set_state("duty_exhausted", "")

def resolved_duty():
    slot = _current_slot()
    if get_state("duty_slot") != slot:
        return None
    tg = get_state("duty_resolved_tg")
    if not tg:
        return None
    return int(tg), get_state("duty_resolved_name")

async def try_resolve_duty():
    members = all_members()
    if not members:
        return
    slot = _current_slot()
    if get_state("duty_slot") != slot:
        init_duty_candidate()
        slot = get_state("duty_slot")
    if get_state("duty_resolved_tg") or get_state("duty_exhausted"):
        return
    n = len(members)
    idx = int(get_state("duty_candidate_idx", 0)) % n
    tg_id, name, _ = members[idx]
    att = get_attendance(slot).get(tg_id)
    if not att:
        return
    status = att[0]
    if status == "ha":
        set_state("duty_resolved_tg", tg_id)
        set_state("duty_resolved_name", name)
        set_state("idx", (idx + 1) % n)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✅ Xabardor bo'ldim", callback_data=f"ack:{tg_id}")]]
        )
        try:
            await bot.send_message(
                tg_id,
                f"📌 <b>Bugun siz navbatchisiz!</b>\n\n📚 <b>Bugungi darslar:</b>\n{lessons_text()}",
                reply_markup=kb,
                parse_mode="HTML",
            )
        except Exception as e:
            logging.warning("Navbatchi xabari yuborilmadi: %s", e)
        try:
            await bot.send_message(
                tg_id,
                "📤 Pastdagi menyuda «Uyga vazifa yuborish» tugmasi paydo bo'ldi.",
                reply_markup=build_menu_kb(tg_id),
            )
        except Exception as e:
            logging.warning("Navbatchi menyusi yuborilmadi: %s", e)
        await notify_admins(f"✅ Bugungi navbatchi: {name}")
    else:
        next_idx = (idx + 1) % n
        start_idx = int(get_state("duty_start_idx", idx))
        if next_idx == start_idx:
            set_state("duty_exhausted", "1")
            await notify_admins("⚠️ Bugun hech kim navbatchilikka rozi bo'lmadi (hammasi \"yo'q\" dedi).")
            return
        set_state("duty_candidate_idx", next_idx)
        await try_resolve_duty()

@dp.callback_query(F.data.startswith("ack:"))
async def on_ack(cq: CallbackQuery):
    expected_tg_id = int(cq.data.split(":")[1])
    if cq.from_user.id != expected_tg_id:
        await cq.answer("Bu tugma sizga tegishli emas.", show_alert=True)
        return
    await cq.answer("Rahmat!")
    now = datetime.now().strftime("%H:%M")
    await notify_admins(f"☑️ {cq.from_user.full_name} xabardor bo'ldi ({now})")

@dp.message(Command("bugun"))
async def cmd_today(m: Message):
    d = resolved_duty()
    if d:
        await m.answer(f"Hozirgi navbatchi: {d[1]}")
    elif get_state("duty_exhausted"):
        await m.answer("Bugun hech kim navbatchilikka rozi bo'lmadi.")
    else:
        await m.answer("Navbatchi hali aniqlanmoqda (javoblar kutilmoqda).")

def _report_delay():
    return timedelta(minutes=45)

def is_late_response():
    due = get_state("att_due_at")
    if not due:
        return False
    return datetime.now() > datetime.fromisoformat(due)

async def send_attendance_poll():
    members = all_members()
    if not members:
        return
    if date.today().weekday() > 4:
        return
    slot = _current_slot()
    prev_tg = get_state("duty_resolved_tg")
    if prev_tg and get_state("duty_slot") != slot:
        try:
            await bot.send_message(
                int(prev_tg),
                "✅ Navbatchiligingiz tugadi, rahmat!",
                reply_markup=build_menu_kb(int(prev_tg)),
                disable_notification=True,
            )
        except Exception as e:
            logging.warning("Menyu tiklanmadi: %s", e)
    set_state("last_att_slot", slot)
    set_state("att_due_at", (datetime.now() + _report_delay()).isoformat())
    init_duty_candidate()
    text = f"Bugun darsga kelasizmi?\n\n<b>Bugun sizda:</b>\n{lessons_text()}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Ha, boraman", callback_data=f"att:yes:{slot}"),
            InlineKeyboardButton(text="❌ Yo'q, bugun bormayman", callback_data=f"att:no:{slot}"),
        ]]
    )
    for tg_id, name, _ in members:
        try:
            await bot.send_message(tg_id, text, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logging.warning("Davomat so'rovi yuborilmadi (%s): %s", name, e)

@dp.callback_query(F.data.startswith("att:yes:"))
async def att_yes(cq: CallbackQuery):
    slot = cq.data.split(":", 2)[2]
    late = is_late_response()
    set_attendance(slot, cq.from_user.id, "ha")
    await cq.message.edit_text(
        f"Rahmat! O'qishingizda omad 🌟\n\n<b>Bugungi darslar:</b>\n{lessons_text()}",
        parse_mode="HTML",
    )
    await cq.answer()
    if late:
        now = datetime.now().strftime("%H:%M")
        await notify_admins(f"⏰ {cq.from_user.full_name} javobni kech belgiladi: ✅ Keladi ({now})")
    await try_resolve_duty()

@dp.callback_query(F.data.startswith("att:no:"))
async def att_no(cq: CallbackQuery):
    slot = cq.data.split(":", 2)[2]
    late = is_late_response()
    set_attendance(slot, cq.from_user.id, "yoq_pending")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Sabab!", callback_data=f"att:reason:{slot}")]]
    )
    await cq.message.edit_text("Iltimos, bugun kelmasligingiz sababini kiriting.", reply_markup=kb)
    await cq.answer()
    if late:
        now = datetime.now().strftime("%H:%M")
        await notify_admins(f"⏰ {cq.from_user.full_name} javobni kech belgiladi: ❌ Kelmaydi ({now})")
    await try_resolve_duty()

@dp.callback_query(F.data.startswith("att:reason:"))
async def att_reason(cq: CallbackQuery, state: FSMContext):
    slot = cq.data.split(":", 2)[2]
    await state.set_state(AttendanceReason.waiting)
    await state.update_data(slot=slot)
    await cq.message.edit_text(
        "Sababni yozishingiz mumkin. Iltimos, barcha xabaringizni bitta matnda kiriting.\n\n"
        "⚠️ Bu sizning bugungi darsda NB olishingizga ta'sir qilishi mumkin."
    )
    await cq.answer()

@dp.message(AttendanceReason.waiting, F.text)
async def att_reason_text(m: Message, state: FSMContext):
    data = await state.get_data()
    slot = data["slot"]
    late = is_late_response()
    set_attendance(slot, m.from_user.id, "yoq_sababli", m.text.strip())
    await state.clear()
    await m.answer("Qabul qilindi. Tez tuzalib keting! 🤒")
    if late:
        now = datetime.now().strftime("%H:%M")
        await notify_admins(f"⏰ {m.from_user.full_name} sababini kech yozdi ({now}):\n{m.text.strip()}")

async def send_attendance_report():
    members = all_members()
    if not members:
        return
    slot = get_state("last_att_slot") or _current_slot()
    rows = get_attendance(slot)
    keladi, sababli_names, sabablar, kelmaydi, nojavob = [], [], [], [], []
    for tg_id, name, _ in members:
        row = rows.get(tg_id)
        if not row:
            nojavob.append(name)
        elif row[0] == "ha":
            keladi.append(name)
        elif row[0] == "yoq_sababli":
            sababli_names.append(name)
            sabablar.append(f"{name} — {row[1]}")
        else:
            kelmaydi.append(name)

    def section(title, items):
        if not items:
            return None
        body = "\n".join(f"• {x}" for x in items)
        return f"{title} ({len(items)}):\n{body}"

    parts = [
        section("✅ Keladi", keladi),
        section("📝 Sababli kelmaydi", sababli_names),
        section("❌ Kelmaydi (sababsiz)", kelmaydi),
        section("❔ Javob bermadi", nojavob),
    ]
    parts = [p for p in parts if p]
    body = "\n\n".join(parts) if parts else "Hozircha hech kim javob bermadi."
    await notify_admins("📋 Bugungi yo'qlama hisoboti\n\n" + body)

    if sabablar:
        await notify_admins("📝 Sabablar:\n" + "\n".join(f"• {x}" for x in sabablar))

@dp.message(F.text == "📅 Raspisaniya")
async def menu_schedule(m: Message):
    lines = []
    for i, day_name in enumerate(DAYS):
        lessons = SCHEDULE.get(i, [])
        body = "\n\n".join(_format_lesson(l) for l in lessons) if lessons else "<i>Dars yo'q</i>"
        lines.append(f"📌 <b>{day_name}</b>\n{body}")
    await m.answer("\n\n➖➖➖➖➖➖\n\n".join(lines), parse_mode="HTML")

def upcoming_duty_labels(n):
    labels = []
    cur = date.today()
    while cur.weekday() > 4:
        cur += timedelta(days=1)
    for _ in range(n):
        labels.append(f"{cur.strftime('%d.%m')} ({DAYS[cur.weekday()]})")
        cur += timedelta(days=1)
        while cur.weekday() > 4:
            cur += timedelta(days=1)
    return labels

@dp.message(F.text == "👥 Navbatchilik ro'yxati")
async def menu_students(m: Message):
    members = all_members()
    if not members:
        await m.answer("Ro'yxat bo'sh.")
        return
    n = len(members)
    d = resolved_duty()
    if d:
        start_idx = next((i for i, (tg, _, _) in enumerate(members) if tg == d[0]), 0)
    else:
        start_idx = int(get_state("duty_candidate_idx", 0)) % n
    order = [members[(start_idx + i) % n] for i in range(n)]
    labels = upcoming_duty_labels(n)
    lines = [f"{i+1}. {name} — {label}" for i, ((_, name, _), label) in enumerate(zip(order, labels))]
    await m.answer("👥 Navbatchilik tartibi (bugungidan boshlab):\n\n" + "\n".join(lines))

# ================= YANGI: MENYU, UYGA VAZIFA, YANGILIKLAR =================
def build_menu_kb(tg_id):
    """Pastki doimiy menyu. Navbatchiga qo'shimcha 'Uyga vazifa yuborish', adminlarga 'Yangiliklar' tugmasi."""
    rows = [
        [KeyboardButton(text="📞 Bog'lanish"), KeyboardButton(text="📅 Raspisaniya")],
        [KeyboardButton(text="👥 Navbatchilik ro'yxati"), KeyboardButton(text="📔 Uyga vazifalar")],
    ]
    d = resolved_duty()
    if d and d[0] == tg_id:
        rows.append([KeyboardButton(text="📤 Uyga vazifa yuborish")])
    if tg_id in ADMIN_IDS:
        rows.append([KeyboardButton(text="📢 Yangiliklar")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def _all_subjects():
    seen = []
    for wd in sorted(SCHEDULE):
        for lesson in SCHEDULE[wd]:
            if lesson["subject"] not in seen:
                seen.append(lesson["subject"])
    return seen


SUBJECTS = _all_subjects()
HW_DRAFTS = {}  # navbatchi tg_id -> {"subject", "items", "panel_id", "last_group"}


def fmt_day(d):
    name = DAYS[d.weekday()] if d.weekday() <= 4 else ""
    return d.strftime("%d.%m.%Y") + (f" ({name})" if name else "")


def duty_day():
    """Navbatchilik kuni (navbatchi aniqlangan sana). Topilmasa — Toshkent bo'yicha bugun."""
    try:
        return date.fromisoformat(get_state("duty_slot"))
    except Exception:
        return tz_today()


def hw_get(subject):
    with db() as c:
        row = c.execute(
            "SELECT items, author_name, lesson_date FROM homework WHERE subject=?", (subject,)
        ).fetchone()
    if not row:
        return None
    return {"items": json.loads(row[0]), "author": row[1], "lesson_date": row[2]}


def hw_save(subject, items, author_id, author_name, lesson_date):
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO homework(subject, items, author_id, author_name, lesson_date, saved_at) "
            "VALUES(?,?,?,?,?,?)",
            (subject, json.dumps(items, ensure_ascii=False), author_id, author_name,
             lesson_date.isoformat(), tz_now().isoformat(timespec="seconds")),
        )


def next_lesson_date(subject, after):
    """`after` sanadan keyingi, shu fan bo'ladigan birinchi dars kuni."""
    for i in range(1, 15):
        d = after + timedelta(days=i)
        if d.weekday() <= 4 and any(l["subject"] == subject for l in SCHEDULE.get(d.weekday(), [])):
            return d
    return None


def hw_subjects_view(day):
    """Navbatchi uchun: shu kunning fanlari (tugmalar) + Orqaga."""
    lessons = SCHEDULE.get(day.weekday(), []) if day.weekday() <= 4 else []
    subs = []
    for lesson in lessons:
        if lesson["subject"] not in subs:
            subs.append(lesson["subject"])
    if not subs:
        return "Bugun dars yo'q, uyga vazifa yuborish shart emas.", None
    rows = []
    for sub in subs:
        saved = hw_get(sub)
        mark = "✅ " if saved and saved["lesson_date"] == day.isoformat() else ""
        rows.append([InlineKeyboardButton(text=f"{mark}{sub}", callback_data=f"hws:{SUBJECTS.index(sub)}")])
    rows.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="hws:back")])
    return "📔 Qaysi fan bo'yicha uyga vazifa yuborasiz?", InlineKeyboardMarkup(inline_keyboard=rows)


def hw_view_kb():
    """Hamma uchun: barcha fanlar ro'yxati."""
    rows = []
    for i, sub in enumerate(SUBJECTS):
        mark = "✅ " if hw_get(sub) else ""
        rows.append([InlineKeyboardButton(text=f"{mark}{sub}", callback_data=f"hwv:{i}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(F.text == "📔 Uyga vazifalar")
async def menu_hw_view(m: Message):
    await m.answer("📔 Qaysi fan bo'yicha uyga vazifani ko'rmoqchisiz?", reply_markup=hw_view_kb())


@dp.message(F.text == "📤 Uyga vazifa yuborish")
async def menu_hw_send(m: Message, state: FSMContext):
    d = resolved_duty()
    if not d or d[0] != m.from_user.id:
        await m.answer("Bu tugma faqat bugungi navbatchi uchun.")
        return
    await state.clear()
    HW_DRAFTS.pop(m.from_user.id, None)
    text, kb = hw_subjects_view(duty_day())
    await m.answer(text, reply_markup=kb)


@dp.message(F.text == "📢 Yangiliklar", is_admin)
async def menu_news(m: Message, state: FSMContext):
    await state.set_state(NewsFlow.waiting)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Orqaga", callback_data="flow:cancel")]])
    await m.answer("📢 Yangilikni yuboring (matn yoki rasm bilan). U barcha a'zolarga yetkaziladi.", reply_markup=kb)


@dp.message(F.text == "📞 Bog'lanish")
async def menu_contact(m: Message, state: FSMContext):
    await state.set_state(ContactAdmin.waiting)
    await m.answer("Xabaringizni yozing, administratorga yuboriladi:")

@dp.message(ContactAdmin.waiting, F.text)
async def contact_send(m: Message, state: FSMContext):
    await state.clear()
    username = f" (@{m.from_user.username})" if m.from_user.username else ""
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✍️ Javob yozish", callback_data=f"creply:{m.from_user.id}"),
            InlineKeyboardButton(text="☑️ Inobatga oldim", callback_data=f"cack:{m.from_user.id}"),
        ]]
    )
    await notify_admins(f"📩 {m.from_user.full_name}{username}:\n{m.text}", reply_markup=kb)
    await m.answer("Xabaringiz yuborildi ✅")

@dp.callback_query(F.data.startswith("creply:"), is_admin)
async def contact_reply_start(cq: CallbackQuery, state: FSMContext):
    tg_id = int(cq.data.split(":")[1])
    await state.set_state(AdminReply.waiting)
    await state.update_data(tg_id=tg_id)
    await cq.answer()
    await bot.send_message(cq.from_user.id, "Xabarni yozing:")

@dp.message(AdminReply.waiting, F.text, is_admin)
async def contact_reply_send(m: Message, state: FSMContext):
    data = await state.get_data()
    tg_id = data["tg_id"]
    await state.clear()
    try:
        await bot.send_message(tg_id, f"📩 Administrator javobi:\n{m.text}")
        await m.answer("Javob yuborildi ✅")
    except Exception as e:
        await m.answer(f"Yuborib bo'lmadi: {e}")

@dp.callback_query(F.data.startswith("cack:"), is_admin)
async def contact_ack(cq: CallbackQuery):
    tg_id = int(cq.data.split(":")[1])
    await cq.message.edit_reply_markup(reply_markup=None)
    await cq.answer("Inobatga olindi")
    try:
        await bot.send_message(tg_id, "✅ Xabaringizni administrator inobatga oldi.")
    except Exception as e:
        logging.warning("Inobatga olish xabari yuborilmadi: %s", e)

@dp.message(Command("stat"), is_admin)
async def cmd_stat(m: Message):
    members = all_members()
    d = resolved_duty()
    if members:
        cand_idx = int(get_state("duty_candidate_idx", 0)) % len(members)
        cand_name = members[cand_idx][1]
    else:
        cand_name = "—"
    if d:
        duty_line = d[1]
    elif get_state("duty_exhausted"):
        duty_line = "hech kim rozi bo'lmadi"
    else:
        duty_line = f"aniqlanmoqda ({cand_name} dan javob kutilmoqda)"
    await m.answer(
        f"📊 Botga qo'shilganlar: {len(members)}/{MAX_MEMBERS}\n"
        f"Qo'shilmaganlar: {max(MAX_MEMBERS - len(members), 0)}\n"
        f"Bugungi navbatchi: {duty_line}"
    )

@dp.message(Command("royxat"), is_admin)
async def cmd_list(m: Message):
    members = all_members()
    if not members:
        await m.answer("Ro'yxat bo'sh.")
        return
    lines = []
    for i, (_, name, birth) in enumerate(members, 1):
        b = datetime.fromisoformat(birth).strftime("%d.%m.%Y")
        lines.append(f"{i}. {name} — {b}")
    await m.answer("\n".join(lines))

@dp.message(Command("ochir"), is_admin)
async def cmd_remove(m: Message, command: CommandObject):
    members = all_members()
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await m.answer("Raqamni yozing. Masalan: /ochir 3\nRaqamlarni /royxat dan ko'ring.")
        return
    pos = int(arg)
    if not 1 <= pos <= len(members):
        await m.answer(f"Bunday raqam yo'q. 1 dan {len(members)} gacha yozing.")
        return
    tg_id, name, _ = members[pos - 1]
    cur = int(get_state("idx", 0)) % len(members)
    with db() as c:
        c.execute("DELETE FROM members WHERE tg_id=?", (tg_id,))
    set_state("idx", cur - 1 if pos - 1 < cur else cur)
    await m.answer(f"🗑 O'chirildi: {name}\nQolgan a'zolar: {len(members) - 1}/{MAX_MEMBERS}")

@dp.message(Command("xabar"), is_admin)
async def cmd_xabar(m: Message, state: FSMContext):
    members = all_members()
    if not members:
        await m.answer("Ro'yxat bo'sh.")
        return
    lines = [f"{i}. {name}" for i, (_, name, _) in enumerate(members, 1)]
    await state.set_state(AdminMsg.choose)
    await m.answer("Kimga xabar yuborasiz? Raqamini yozing:\n\n" + "\n".join(lines))

@dp.message(AdminMsg.choose, F.text, is_admin)
async def xabar_choose(m: Message, state: FSMContext):
    members = all_members()
    if not m.text.strip().isdigit() or not (1 <= int(m.text.strip()) <= len(members)):
        await m.answer(f"Raqamni to'g'ri yozing. 1 dan {len(members)} gacha.")
        return
    tg_id, name, _ = members[int(m.text.strip()) - 1]
    await state.update_data(tg_id=tg_id, name=name)
    await state.set_state(AdminMsg.text)
    await m.answer(f"{name} ga yuboriladigan xabar matnini yozing:")

@dp.message(AdminMsg.text, F.text, is_admin)
async def xabar_send(m: Message, state: FSMContext):
    data = await state.get_data()
    tg_id, name = data["tg_id"], data["name"]
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Xabardor bo'ldim", callback_data=f"ack:{tg_id}")]]
    )
    try:
        await bot.send_message(tg_id, m.text, reply_markup=kb)
        await m.answer(f"Yuborildi: {name}")
    except Exception as e:
        await m.answer(f"Yuborib bo'lmadi: {e}")
    await state.clear()

@dp.message(Command("tozala"), is_admin)
async def cmd_tozala(m: Message):
    count = cleanup_old_attendance()
    await m.answer(f"🧹 {count} ta eski davomat yozuvi o'chirildi ({ATTENDANCE_RETENTION_DAYS} kundan eski).")

# ================= YANGI: UYGA VAZIFA (navbatchi) =================
def _duty_ok(user_id):
    d = resolved_duty()
    return bool(d and d[0] == user_id)


_BACK_TO_SUBJECTS_KB = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="◀️ Orqaga", callback_data="hwb:subjects")]]
)


@dp.callback_query(F.data.startswith("hws:"))
async def hw_pick(cq: CallbackQuery, state: FSMContext):
    uid = cq.from_user.id
    if not _duty_ok(uid):
        await cq.answer("Bu tugma faqat bugungi navbatchi uchun.", show_alert=True)
        return
    arg = cq.data.split(":", 1)[1]
    if arg == "back":
        await state.clear()
        HW_DRAFTS.pop(uid, None)
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.answer()
        return
    if not arg.isdigit() or int(arg) >= len(SUBJECTS):
        await cq.answer()
        return
    subject = SUBJECTS[int(arg)]
    HW_DRAFTS[uid] = {"subject": subject, "items": [], "panel_id": None, "last_group": None}
    await state.set_state(HomeworkFlow.waiting)
    await cq.message.edit_text(
        f"📔 <b>{html.escape(subject)}</b>\n\n"
        "Uyga vazifani rasm va/yoki yozma ko'rinishda yuborishingiz mumkin.",
        reply_markup=_BACK_TO_SUBJECTS_KB,
        parse_mode="HTML",
    )
    await cq.answer()


@dp.callback_query(F.data == "hwb:subjects")
async def hw_back_to_subjects(cq: CallbackQuery, state: FSMContext):
    uid = cq.from_user.id
    if not _duty_ok(uid):
        await cq.answer("Bu tugma faqat bugungi navbatchi uchun.", show_alert=True)
        return
    await state.clear()
    HW_DRAFTS.pop(uid, None)
    text, kb = hw_subjects_view(duty_day())
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer()


async def hw_show_panel(chat_id, draft):
    items = draft["items"]
    photos = sum(1 for i in items if i["t"] == "photo")
    texts = len(items) - photos
    parts = []
    if photos:
        parts.append(f"{photos} ta rasm")
    if texts:
        parts.append(f"{texts} ta matn")
    summary = ", ".join(parts)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Tasdiqlash", callback_data="hwc:ok")],
        [
            InlineKeyboardButton(text="➕ Yana qo'shish", callback_data="hwc:more"),
            InlineKeyboardButton(text="🗑 O'chirish", callback_data="hwc:del"),
        ],
    ])
    old = draft.get("panel_id")
    if old:
        try:
            await bot.delete_message(chat_id, old)
        except Exception:
            pass
    msg = await bot.send_message(
        chat_id,
        f"📔 <b>{html.escape(draft['subject'])}</b>\nQabul qilindi: {summary}.\n\nTasdiqlaysizmi?",
        reply_markup=kb,
        parse_mode="HTML",
    )
    draft["panel_id"] = msg.message_id


@dp.message(HomeworkFlow.waiting, (F.text & ~F.text.startswith("/")) | F.photo)
async def hw_receive(m: Message, state: FSMContext):
    uid = m.from_user.id
    draft = HW_DRAFTS.get(uid)
    if not _duty_ok(uid) or not draft:
        await state.clear()
        HW_DRAFTS.pop(uid, None)
        await m.answer("Sessiya tugagan. «📤 Uyga vazifa yuborish» tugmasini qaytadan bosing.")
        return
    if m.photo:
        draft["items"].append({"t": "photo", "f": m.photo[-1].file_id, "c": m.caption or ""})
    else:
        draft["items"].append({"t": "text", "c": m.text})
    gid = m.media_group_id
    if gid and draft["last_group"] == gid:
        return  # albomning qolgan rasmlari uchun alohida panel chiqarmaymiz
    draft["last_group"] = gid
    if gid:
        await asyncio.sleep(1.2)  # albomning hamma rasmi kelib bo'lishini kutamiz
    await hw_show_panel(m.chat.id, draft)


@dp.message(HomeworkFlow.waiting)
async def hw_receive_other(m: Message):
    await m.answer("Iltimos, faqat rasm (oddiy rasm sifatida) yoki matn yuboring.")


@dp.callback_query(F.data.startswith("hwc:"))
async def hw_confirm_cb(cq: CallbackQuery, state: FSMContext):
    uid = cq.from_user.id
    d = resolved_duty()
    if not d or d[0] != uid:
        await cq.answer("Bu tugma faqat bugungi navbatchi uchun.", show_alert=True)
        return
    draft = HW_DRAFTS.get(uid)
    if not draft:
        await cq.answer("Sessiya tugagan. Qaytadan boshlang.", show_alert=True)
        return
    action = cq.data.split(":", 1)[1]
    sub = html.escape(draft["subject"])
    if action == "ok":
        if not draft["items"]:
            await cq.answer("Avval rasm yoki matn yuboring.", show_alert=True)
            return
        hw_save(draft["subject"], draft["items"], uid, d[1], duty_day())
        HW_DRAFTS.pop(uid, None)
        await state.clear()
        await cq.message.edit_text(f"✅ <b>{sub}</b> bo'yicha uyga vazifa tasdiqlandi.", parse_mode="HTML")
        await cq.answer()
        text, kb = hw_subjects_view(duty_day())
        await bot.send_message(uid, text, reply_markup=kb)
    elif action == "more":
        draft["panel_id"] = cq.message.message_id
        await cq.message.edit_text("➕ Yana rasm yoki matn yuboring.", reply_markup=_BACK_TO_SUBJECTS_KB)
        await cq.answer()
    elif action == "del":
        draft["items"].clear()
        draft["last_group"] = None
        draft["panel_id"] = cq.message.message_id
        await cq.message.edit_text(
            "🗑 Uyga vazifa o'chirildi. Boshqatdan yuborishingiz mumkin — rasm yoki matn yuboring.",
            reply_markup=_BACK_TO_SUBJECTS_KB,
        )
        await cq.answer()
    else:
        await cq.answer()


# ================= YANGI: UYGA VAZIFANI KO'RISH (hamma) =================
@dp.callback_query(F.data.startswith("hwv:"))
async def hw_view_pick(cq: CallbackQuery):
    arg = cq.data.split(":", 1)[1]
    if not arg.isdigit() or int(arg) >= len(SUBJECTS):
        await cq.answer()
        return
    subject = SUBJECTS[int(arg)]
    hw = hw_get(subject)
    await cq.answer()
    chat_id = cq.from_user.id
    if not hw:
        await bot.send_message(chat_id, f"📔 {subject}\n\nHozircha bu fan bo'yicha uyga vazifa kiritilmagan.")
        return
    lesson_day = date.fromisoformat(hw["lesson_date"])
    nxt = next_lesson_date(subject, lesson_day)
    header = (
        f"📔 <b>{html.escape(subject)}</b>\n"
        f"📅 Dars bo'lgan kun: {fmt_day(lesson_day)}\n"
        f"👤 Navbatchi: {html.escape(hw['author'] or '—')}\n"
        f"⏭ Keyingi dars: {fmt_day(nxt) if nxt else '—'}"
    )
    await bot.send_message(chat_id, header, parse_mode="HTML")
    for item in hw["items"]:
        try:
            if item["t"] == "photo":
                await bot.send_photo(chat_id, item["f"], caption=item.get("c") or None)
            else:
                await bot.send_message(chat_id, item["c"])
        except Exception as e:
            logging.warning("Uyga vazifa elementi yuborilmadi: %s", e)


# ================= YANGI: YANGILIKLAR (admin -> hamma) =================
@dp.callback_query(F.data == "flow:cancel")
async def flow_cancel(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await cq.message.edit_text("Bekor qilindi.")
    except Exception:
        pass
    await cq.answer()


@dp.message(NewsFlow.waiting, is_admin, (F.text & ~F.text.startswith("/")) | F.photo)
async def news_send(m: Message, state: FSMContext):
    await state.clear()
    text = m.text or m.caption or ""
    photo_id = m.photo[-1].file_id if m.photo else None
    recipients = {tg for tg, _, _ in all_members()} | set(ADMIN_IDS)
    recipients.discard(m.from_user.id)
    header = "📢 Yangilik"
    sent = failed = 0
    for tg in recipients:
        try:
            cap = f"{header}\n\n{text}" if text else header
            if photo_id:
                if len(cap) <= 1024:
                    await bot.send_photo(tg, photo_id, caption=cap)
                else:
                    await bot.send_photo(tg, photo_id, caption=header)
                    await bot.send_message(tg, text)
            else:
                await bot.send_message(tg, cap)
            sent += 1
        except Exception as e:
            failed += 1
            logging.warning("Yangilik yuborilmadi (%s): %s", tg, e)
        await asyncio.sleep(0.05)
    await m.answer(f"✅ Yuborildi: {sent} kishiga" + (f" (yuborilmadi: {failed})" if failed else ""))


@dp.message(NewsFlow.waiting, is_admin)
async def news_other(m: Message):
    await m.answer("Iltimos, matn yoki oddiy rasm yuboring.")


# ================= YANGI: TUG'ILGAN KUN TABRIGI =================
def birthdays_on(day):
    res = []
    for tg_id, name, birth in all_members():
        try:
            b = date.fromisoformat(birth)
        except Exception:
            continue
        match = (b.month, b.day) == (day.month, day.day)
        if not match and (b.month, b.day) == (2, 29) and (day.month, day.day) == (2, 28) and not calendar.isleap(day.year):
            match = True  # 29-fevralda tug'ilganlar kabisa bo'lmagan yili 28-fevralda tabriklanadi
        if match:
            res.append((tg_id, name, day.year - b.year))
    return res


def birthday_texts(name, age):
    own = (
        f"🎉 Tug'ilgan kuningiz muborak bo'lsin, {name}!\n\n"
        f"Siz bugun {age} yoshga to'ldingiz. Sizga sog'lik, omad va baxt tilaymiz! 🎂"
    )
    others = f"🎂 Bugun {name}ning tug'ilgan kuni!\n\nTabriklashni unutmang 🎉"
    return own, others


async def _send_bd(chat_id, text, photo):
    try:
        if photo:
            await bot.send_photo(chat_id, photo, caption=text)
            return
    except Exception as e:
        logging.warning("Tabrik rasmi yuborilmadi (%s): %s", chat_id, e)
    try:
        await bot.send_message(chat_id, text)
    except Exception as e:
        logging.warning("Tabrik yuborilmadi (%s): %s", chat_id, e)


async def send_birthday_greetings():
    day = tz_today()
    if get_state("birthday_done") == day.isoformat():
        return
    set_state("birthday_done", day.isoformat())
    people = birthdays_on(day)
    if not people:
        return
    photo = get_state("birthday_photo") or None
    members = all_members()
    for tg_id, name, age in people:
        own, others = birthday_texts(name, age)
        await _send_bd(tg_id, own, photo)
        for other_id, _, _ in members:
            if other_id != tg_id:
                await _send_bd(other_id, others, photo)
                await asyncio.sleep(0.05)


@dp.message(Command("tabrik"), is_admin)
async def cmd_tabrik(m: Message, state: FSMContext):
    await state.set_state(BirthdayPhoto.waiting)
    text = "🎂 Tug'ilgan kun tabrigi uchun rasm yuboring (oddiy rasm sifatida, fayl emas)."
    if get_state("birthday_photo"):
        text += "\nYangi rasm yuborsangiz, avvalgisi almashtiriladi."
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Orqaga", callback_data="flow:cancel")]])
    await m.answer(text, reply_markup=kb)


@dp.message(BirthdayPhoto.waiting, F.photo, is_admin)
async def tabrik_photo(m: Message, state: FSMContext):
    set_state("birthday_photo", m.photo[-1].file_id)
    await state.clear()
    await m.answer("✅ Tabrik rasmi saqlandi. Avvalgisi (agar bo'lsa) almashtirildi.")


@dp.message(BirthdayPhoto.waiting, is_admin)
async def tabrik_other(m: Message):
    await m.answer("Iltimos, rasmni oddiy rasm sifatida yuboring.")


@dp.message(Command("tabrik_sinov"), is_admin)
async def cmd_tabrik_sinov(m: Message):
    own, others = birthday_texts("Namuna Ism", 19)
    photo = get_state("birthday_photo") or None
    await m.answer("👇 Tabrik xabarlari namunasi (faqat sizga yuborildi):")
    await _send_bd(m.from_user.id, "[Tug'ilgan kun egasiga]\n\n" + own, photo)
    await _send_bd(m.from_user.id, "[Boshqa a'zolarga]\n\n" + others, photo)
    if not photo:
        await m.answer("ℹ️ Tabrik rasmi hali yuklanmagan. /tabrik bilan yuklang.")


# ================= YANGI: ADMIN BUYRUQLARI, BACKUP =================
ADMIN_HELP = (
    "🛠 <b>Admin buyruqlari</b>\n\n"
    "/admin — shu ro'yxat\n"
    "/stat — a'zolar soni va bugungi navbatchi\n"
    "/royxat — barcha a'zolar (ism, tug'ilgan sana)\n"
    "/ochir [raqam] — a'zoni o'chirish (raqam /royxat dan)\n"
    "/xabar — bitta a'zoga shaxsiy xabar yuborish\n"
    "/tozala — eski davomat yozuvlarini tozalash\n"
    "/backup — bazani fayl qilib yuklab olish\n"
    "/restore — zaxira faylidan bazani tiklash\n"
    "/tabrik — tug'ilgan kun rasmini yuklash/almashtirish\n"
    "/tabrik_sinov — tabrik xabarlari namunasi (faqat sizga)\n"
    "/menyu_yangila — hamma a'zoga yangi menyu tugmalarini yuborish\n\n"
    "Pastki menyudagi <b>📢 Yangiliklar</b> tugmasi — hamma a'zoga xabar (matn/rasm) yuboradi."
)


@dp.message(Command("admin"), is_admin)
async def cmd_admin(m: Message):
    await m.answer(ADMIN_HELP, parse_mode="HTML", reply_markup=build_menu_kb(m.from_user.id))


def make_backup_copy(dest_path):
    """Bazaning izchil nusxasini yaratadi (yozish paytida ham xavfsiz)."""
    src = sqlite3.connect(DB)
    dst = sqlite3.connect(dest_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def restore_db_from(path):
    """Zaxira faylini tekshirib, joriy bazaga tiklaydi. A'zolar sonini qaytaradi."""
    chk = sqlite3.connect(path)
    try:
        tables = {r[0] for r in chk.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        chk.close()
    if not {"members", "state"} <= tables:
        raise ValueError("bu bizning bot bazasi emas (members/state jadvallari yo'q)")
    src = sqlite3.connect(path)
    dst = sqlite3.connect(DB)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    init_db()  # eski zaxirada yo'q bo'lgan yangi jadvallarni qo'shadi
    return len(all_members())


@dp.message(Command("backup"), is_admin)
async def cmd_backup(m: Message):
    tmp = "navbatchi_backup_tmp.db"
    try:
        make_backup_copy(tmp)
        stamp = tz_now().strftime("%Y-%m-%d_%H-%M")
        await bot.send_document(
            m.from_user.id,
            FSInputFile(tmp, filename=f"navbatchi_{stamp}.db"),
            caption="💾 Baza zaxirasi. Saqlab qo'ying. Tiklash uchun: /restore",
        )
    except Exception as e:
        await m.answer(f"❌ Zaxira yaratib bo'lmadi: {e}")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


@dp.message(Command("restore"), is_admin)
async def cmd_restore(m: Message, state: FSMContext):
    await state.set_state(Restore.waiting)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Orqaga", callback_data="flow:cancel")]])
    await m.answer(
        "♻️ /backup bilan olgan <b>.db</b> faylingizni hujjat (fayl) sifatida yuboring.\n\n"
        "⚠️ Joriy ma'lumotlar zaxiradagisi bilan almashtiriladi.",
        parse_mode="HTML",
        reply_markup=kb,
    )


@dp.message(Restore.waiting, F.document, is_admin)
async def restore_file(m: Message, state: FSMContext):
    tmp = "navbatchi_restore_tmp.db"
    try:
        await bot.download(m.document, destination=tmp)
        count = restore_db_from(tmp)
        await state.clear()
        await m.answer(f"✅ Baza tiklandi. A'zolar soni: {count}")
    except Exception as e:
        await m.answer(f"❌ Tiklab bo'lmadi: {e}")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


@dp.message(Restore.waiting, is_admin)
async def restore_other(m: Message):
    await m.answer("Iltimos, .db faylni hujjat (fayl) sifatida yuboring.")


@dp.message(Command("menyu_yangila"), is_admin)
async def cmd_menu_update(m: Message):
    sent = 0
    for tg_id, _, _ in all_members():
        try:
            await bot.send_message(
                tg_id,
                "🆕 Bot yangilandi! Pastdagi menyuda yangi tugmalar paydo bo'ldi.",
                reply_markup=build_menu_kb(tg_id),
            )
            sent += 1
        except Exception as e:
            logging.warning("Menyu yuborilmadi (%s): %s", tg_id, e)
        await asyncio.sleep(0.05)
    await m.answer(f"✅ Yuborildi: {sent} kishiga")


# ================= VEB SERVER QISMI =================
app = web.Application()

async def handle(request):
    return web.Response(text="Bot is running!")

app.router.add_get("/", handle)

async def start_web_server():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)
    await site.start()

async def main():
    init_db()
    sched = AsyncIOScheduler(timezone="Asia/Tashkent")
    sched.add_job(send_attendance_poll, "cron", day_of_week="mon-fri", hour=7, minute=0)
    sched.add_job(send_attendance_report, "cron", day_of_week="mon-fri", hour=7, minute=45)
    sched.add_job(cleanup_old_attendance, "cron", hour=3, minute=0)
    sched.add_job(send_birthday_greetings, "cron", hour=0, minute=0, misfire_grace_time=3600, coalesce=True)
    sched.start()
    
    # Veb-serverni ishga tushiramiz (Render portni topishi uchun)
    await start_web_server()

    # Agar bot 00:00 da uxlab qolgan bo'lsa, ertalab uyg'onganda tabriklarni yetkazamiz (kuniga bir marta)
    if tz_now().hour < 12:
        asyncio.create_task(send_birthday_greetings())

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())