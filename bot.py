"""
Бот Софии — отвечает на КАРТА / КОФЕ / РУНА в комментариях.
Управление через кнопки: /menu в личке боту.
Автопостинг по расписанию: APScheduler (10:00 МСК ежедневно).
Кнопки с предсказаниями на постах канала.
"""

import os, re, json, random, logging
from datetime import datetime, timedelta, date
from pathlib import Path

import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN       = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_ЗДЕСЬ")
ADMIN_ID        = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID      = os.getenv("CHANNEL_ID", "@sofia_gada1ka")
TEST_CHANNEL_ID = os.getenv("TEST_CHANNEL_ID", "@dfgyugsdiufyhg")
# Дата начала кампании YYYY-MM-DD. Бот считает номер недели от неё.
CAMPAIGN_START  = os.getenv("CAMPAIGN_START", "")

MSK = pytz.timezone("Europe/Moscow")

TEXTS_FILE    = Path("texts.json")
CONTENT_FILE  = Path("content.json")
HOLIDAYS_FILE = Path("holidays.json")

# ─── Состояния диалога ────────────────────────────────────────────────────────
WAITING_TEXT, WAITING_DELETE_NUM = range(2)

# ─── Кулдауны ─────────────────────────────────────────────────────────────────
COOLDOWN_HOURS = 24
cooldowns:     dict[int, datetime]             = {}
cooldowns_btn: dict[tuple[int, str], datetime] = {}

def is_on_cooldown(user_id: int) -> bool:
    last = cooldowns.get(user_id)
    return last is not None and datetime.now() - last < timedelta(hours=COOLDOWN_HOURS)

def is_btn_on_cooldown(user_id: int, btn_type: str) -> bool:
    last = cooldowns_btn.get((user_id, btn_type))
    return last is not None and datetime.now() - last < timedelta(hours=COOLDOWN_HOURS)

# ─── Паттерны триггеров ───────────────────────────────────────────────────────
KARTA = re.compile(r"^\s*к+а+р+т[аы]?[!?.,…]?\s*$", re.IGNORECASE)
KOFE  = re.compile(r"^\s*к+о+ф+[её][!?.,…]?\s*$",   re.IGNORECASE)
RUNA  = re.compile(r"^\s*р+у+н[аы]?[!?.,…]?\s*$",   re.IGNORECASE)

KEY_LABELS = {
    "karta":         "🎴 Карта",
    "kofe":          "☕ Кофе",
    "runa":          "🌿 Руна",
    "button_love":   "❤️ Что он чувствует",
    "button_money":  "💰 Денежный совет",
    "button_cards":  "🔮 Карты отвечают",
}

# ─── Тексты по умолчанию ──────────────────────────────────────────────────────
DEFAULT_TEXTS = {
    "karta": [
        "🎴 {name}, тяну твою карту...\n\n✨ Сегодня колода говорит: доверься тому, что чувствуешь, а не тому, что «надо».\nДень просит честности с собой — там, где ты давно знаешь ответ, но откладываешь.\n\nКак тебе? 🙂 Хочешь подробнее — напиши в личку слово КАРТА 🔮",
        "🎴 {name}, карта дня для тебя...\n\n🌙 Сегодня важно замедлиться. Не гнаться — а прислушаться.\nЧто-то тихое пытается достучаться до тебя уже несколько дней. Услышь это.\n\nРезонирует? ✨",
        "🎴 {name}, вот что говорит колода...\n\n⚡ День несёт движение и перемены. Если что-то сдвинулось — это не случайно.\nИди туда, где страшновато. Там и есть рост.\n\nСовпало с тем, что сейчас происходит? 👇",
        "🎴 {name}, твоя карта сегодня...\n\n🌸 Время позаботиться о себе — без чувства вины.\nТы много даёшь другим. Сегодня — верни немного себе.\n\nКак ощущение? 💗",
        "🎴 {name}, карта вытянута...\n\n🍀 Колода видит возможность, которую ты рискуешь пропустить.\nПрисмотрись к тому, что кажется «слишком хорошим». Иногда — правда.\n\nЧто думаешь? ✨",
        "🎴 {name}, смотрю в колоду...\n\n🕯️ Сегодняшний день — про завершение. Что-то просит отпустить.\nНе потеря — освобождение. Там, где отпускаешь, появляется место для нового.\n\nЧто отзывается? 🌿",
        "🎴 {name}, вот твоё послание на сегодня...\n\n☀️ Светлый день. Позволь себе радоваться — без повода, просто так.\nИногда лёгкость и есть самый верный путь вперёд.\n\nУлыбнулось что-нибудь? 😊",
    ],
    "kofe": [
        "☕ {name}, смотрю в твою чашку...\n\nВижу дорогу — она не прямая, но ведёт куда надо.\nРядом — человек, который думает о тебе чаще, чем говорит.\nИ главное: скоро появится возможность — не торопись отказывать.\n\nЧто-то совпало? ☕",
        "☕ {name}, читаю кофейную гущу...\n\nНа дне — круг. Цикл завершается, готовься к новому витку.\nВижу ещё: беспокойство вокруг денег уходит — но медленно, не форсируй.\n\nРезонирует? 🌙",
        "☕ {name}, заглядываю в чашку...\n\nОсадок говорит о дороге — скорее всего не физической, а внутренней.\nИ о встрече. Кто-то войдёт в твою жизнь или напомнит о себе в ближайшие дни.\n\nЖдёшь кого-то? 💫",
        "☕ {name}, гуща раскрывается...\n\nВижу птицу — это свобода от чего-то, что давно держало.\nВремя принять решение, которое ты откладывала. Момент подходящий.\n\nУгадала? ☕",
        "☕ {name}, смотрю внимательно...\n\nНа дне — сердце. Отношения в фокусе сегодня: либо потеплеет, либо прояснится.\nГлавное — говори прямо. Тебя поймут.\n\nКак с этим сейчас? 💗",
    ],
    "runa": [
        "🌿 {name}, тяну рунное послание...\n\n⚡ Выпала Тейваз — руна воина и направления.\nТвоя сила сегодня в ясности намерения. Иди туда, куда ведёт внутренний компас.\n\nРезонирует? 🌿",
        "🌿 {name}, руна брошена...\n\n🌊 Выпала Лагуз — руна потока и интуиции.\nСегодня не день для логики. Доверься чувствам — они точнее любых расчётов.\n\nКак тебе? ✨",
        "🌿 {name}, руна для тебя...\n\n☀️ Выпала Соулу — руна солнца и победы.\nЭнергия сейчас высокая. Начинай то, что откладывал(а) — момент благоприятный.\n\nСовпало с ощущениями? 💫",
        "🌿 {name}, послание рун...\n\n🌱 Выпала Беркана — руна роста и нового начала.\nЧто-то новое хочет прорасти в твоей жизни. Дай этому пространство.\n\nЧто прорастает у тебя сейчас? 🌿",
        "🌿 {name}, руны говорят...\n\n🛡️ Выпала Альгиз — руна защиты.\nСегодня твоё поле сильное. Двигайся вперёд — ты под защитой.\n\nЧувствуешь эту опору? ✨",
        "🌿 {name}, тяну руну...\n\n🔄 Выпала Эваз — руна движения и изменений.\nЧто-то приходит в движение. Не сопротивляйся переменам — они в твою пользу.\n\nЧто сейчас меняется? 💫",
    ],
    "button_love": [
        "🔮 {name}, смотрю на твою ситуацию...\n\nОн чувствует интерес и тепло, но пока не решается показать. Что-то его сдерживает — скорее всего страх отказа, а не равнодушие.\n\nМой совет: не форсируй. Дай ему время проявиться.\n\n💗 Напиши «карта» в канале — получишь послание на день",
        "🔮 {name}, карты говорят об отношениях...\n\nЭтот человек думает о тебе — и чаще, чем ты думаешь. В его душе — смесь нежности и неуверенности.\n\nСейчас важно: будь собой. Твоя подлинность притягивает.\n\n💗 Напиши «карта» в канале за посланием",
        "🔮 {name}, вижу твою ситуацию...\n\nОн чувствует влечение, но боится ошибиться. Ему важно знать, что его примут.\n\nМаленький шаг с твоей стороны откроет то, что он пока прячет.\n\n❤️ Напиши «карта» в канале — там ждёт твоя карта дня",
    ],
    "button_money": [
        "💰 {name}, карты о деньгах для тебя...\n\nСейчас не время крупных трат — энергия денег накапливается. Отложи одно необязательное расходование сегодня.\n\nЕсть идея, которую ты откладываешь? Именно она — ключ к следующему финансовому шагу.\n\n✨ Напиши «карта» в канале за посланием дня",
        "💰 {name}, денежный совет от карт...\n\nВижу поворот: что-то, что казалось закрытым, скоро откроется снова. Следи за неожиданными предложениями в ближайшие дни.\n\nЕщё одно: кто-то из окружения может стать источником нужного знакомства.\n\n💫 Напиши «карта» в канале — там твоя карта дня",
        "💰 {name}, смотрю в финансовое поле...\n\nЭнергия сейчас в движении. Не стой — делай хотя бы маленький шаг к тому, что хочешь вырастить.\n\nРабота в этом направлении именно сейчас принесёт плоды быстрее, чем кажется.\n\n🌿 Напиши «карта» в канале за своим посланием",
    ],
    "button_cards": [
        "🔮 {name}, карты отвечают...\n\nДа — но не сразу. Будет промежуточный шаг, который покажется остановкой. Это не тупик — это поворот.\n\nДоверяй процессу.\n\n✨ Напиши «карта» в канале — там ждёт твоя карта дня",
        "🔮 {name}, спрашиваю карты для тебя...\n\nОтвет — «жди». Не из-за отказа, а потому что момент ещё созревает. Торопить — значит упустить.\n\nЗанимайся тем, что в твоих руках прямо сейчас.\n\n💫 Напиши «карта» в канале за посланием",
        "🔮 {name}, карты дают ответ...\n\nОбрати внимание на ощущение в теле, когда думаешь об этом. Карты говорят: тело знает ответ раньше ума.\n\nЕсли внутри лёгкость — иди. Если зажим — подожди.\n\n🌙 Напиши «карта» в канале — там твоё послание",
    ],
}

# ─── Работа с texts.json ──────────────────────────────────────────────────────
def load_texts() -> dict:
    if TEXTS_FILE.exists():
        data = json.loads(TEXTS_FILE.read_text(encoding="utf-8"))
        # Дополняем новыми ключами если файл старый (без button_*)
        changed = False
        for key, val in DEFAULT_TEXTS.items():
            if key not in data:
                data[key] = val
                changed = True
        if changed:
            save_texts(data)
        return data
    save_texts(DEFAULT_TEXTS)
    return DEFAULT_TEXTS

def save_texts(data: dict) -> None:
    TEXTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ─── Работа с content.json ────────────────────────────────────────────────────
def load_content() -> list:
    if CONTENT_FILE.exists():
        return json.loads(CONTENT_FILE.read_text(encoding="utf-8"))
    return []

def get_campaign_start() -> date:
    if CAMPAIGN_START:
        return date.fromisoformat(CAMPAIGN_START)
    return date.today()

DAYS_EN = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

def get_post_for_today(slot: str = "morning") -> dict | None:
    """Находит пост на сегодня по номеру недели и дню. slot='morning'|'evening'."""
    posts = load_content()
    if not posts:
        return None
    today    = datetime.now(tz=MSK).date()
    start    = get_campaign_start()
    delta    = (today - start).days
    week_num = (delta // 7) % 4 + 1   # недели 1-4 по кругу
    day_name = DAYS_EN[today.weekday()]
    candidates = [
        p for p in posts
        if p.get("week") == week_num
        and p.get("day") == day_name
        and p.get("slot", "morning") == slot
    ]
    return candidates[0] if candidates else None

# ─── Праздничные посты ────────────────────────────────────────────────────────
def get_holiday_post() -> dict | None:
    if not HOLIDAYS_FILE.exists():
        return None
    holidays  = json.loads(HOLIDAYS_FILE.read_text(encoding="utf-8"))
    today_str = datetime.now(tz=MSK).strftime("%d-%m")
    for h in holidays:
        if h.get("date") == today_str:
            return h.get("post")
    return None

# ─── Отправка поста в канал ───────────────────────────────────────────────────
async def send_post(bot, channel_id: str, post: dict) -> None:
    title = post.get("title", "")
    text  = post.get("text", "")
    cta   = post.get("cta", "")
    full  = f"*{title}*\n\n{text}\n\n{cta}".strip()

    keyboard = None
    if post.get("interactive_type") == "button_prediction" and post.get("button"):
        btn = post["button"]
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(btn["label"], callback_data=f"pred_{btn['type']}")
        ]])

    photo = post.get("photo_path")
    if photo and Path(photo).exists():
        with open(photo, "rb") as f:
            await bot.send_photo(chat_id=channel_id, photo=f, caption=full,
                                 parse_mode="Markdown", reply_markup=keyboard)
    else:
        await bot.send_message(chat_id=channel_id, text=full,
                               parse_mode="Markdown", reply_markup=keyboard)

# ─── Плановая отправка (APScheduler вызывает эти функции) ────────────────────
async def scheduled_morning_post(app: Application) -> None:
    holiday = get_holiday_post()
    post    = holiday or get_post_for_today("morning")
    if not post:
        logger.warning("Нет поста на сегодня (утро) — пропуск")
        return
    await send_post(app.bot, CHANNEL_ID, post)
    logger.info(f"✅ Утренний пост → {CHANNEL_ID}")

async def scheduled_evening_post(app: Application) -> None:
    post = get_post_for_today("evening")
    if not post:
        return
    await send_post(app.bot, CHANNEL_ID, post)
    logger.info(f"✅ Вечерний пост → {CHANNEL_ID}")

# ─── Команды администратора ───────────────────────────────────────────────────
async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправить сегодняшний пост в ТЕСТОВЫЙ канал для проверки."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет доступа.")
        return
    post = get_post_for_today()
    if not post:
        today     = datetime.now(tz=MSK).date()
        start     = get_campaign_start()
        delta     = (today - start).days
        week_num  = (delta // 7) % 4 + 1
        day_name  = DAYS_EN[today.weekday()]
        await update.message.reply_text(
            f"❌ Нет поста на сегодня в content.json\n"
            f"Ищу: week={week_num}, day={day_name}\n\n"
            f"Проверь CAMPAIGN_START и content.json"
        )
        return
    await send_post(context.bot, TEST_CHANNEL_ID, post)
    await update.message.reply_text(
        f"✅ Тест-пост отправлен в {TEST_CHANNEL_ID}\n"
        f"Пост: *{post.get('title', '—')}*",
        parse_mode="Markdown"
    )

async def cmd_testpost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправить конкретный пост по id в тестовый канал. Использование: /testpost 2"""
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /testpost <id>\nПример: /testpost 2")
        return
    try:
        post_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом. Пример: /testpost 2")
        return
    posts = load_content()
    found = next((p for p in posts if p.get("id") == post_id), None)
    if not found:
        ids = [p.get("id") for p in posts]
        await update.message.reply_text(f"Пост с id={post_id} не найден.\nДоступные id: {ids}")
        return
    await send_post(context.bot, TEST_CHANNEL_ID, found)
    await update.message.reply_text(
        f"✅ Пост #{post_id} отправлен в {TEST_CHANNEL_ID}\n"
        f"*{found.get('title', '—')}*\n"
        f"Тип: `{found.get('interactive_type', '—')}`",
        parse_mode="Markdown"
    )

async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Немедленно отправить сегодняшний пост в основной канал."""
    if update.effective_user.id != ADMIN_ID:
        return
    post = get_post_for_today()
    if not post:
        await update.message.reply_text("❌ Нет поста на сегодня в content.json")
        return
    await send_post(context.bot, CHANNEL_ID, post)
    await update.message.reply_text(
        f"✅ Пост отправлен в {CHANNEL_ID}\n"
        f"*{post.get('title', '—')}*",
        parse_mode="Markdown"
    )

async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать план постов на текущую неделю."""
    if update.effective_user.id != ADMIN_ID:
        return
    posts    = load_content()
    today    = datetime.now(tz=MSK).date()
    start    = get_campaign_start()
    delta    = (today - start).days
    week_num = (delta // 7) % 4 + 1

    week_posts = [p for p in posts if p.get("week") == week_num]
    if not week_posts:
        await update.message.reply_text(
            f"Нет постов для недели {week_num} в content.json\n"
            f"Всего постов в файле: {len(posts)}"
        )
        return

    day_labels = {
        "monday": "ПН 🔮", "tuesday": "ВТ 💔", "wednesday": "СР 💰",
        "thursday": "ЧТ 🕯", "friday": "ПТ 🗣", "saturday": "СБ ✨",
        "sunday": "ВС ☀️🌙",
    }
    itype_icon = {"reactions": "👍", "button_prediction": "🔘", "vote_123": "1️⃣"}

    lines = [f"📅 *Неделя {week_num} из 4:*\n"]
    for p in week_posts:
        day   = day_labels.get(p.get("day", ""), p.get("day", "?"))
        title = p.get("title", "—")
        icon  = itype_icon.get(p.get("interactive_type", ""), "")
        lines.append(f"{day} {icon} {title}")

    lines.append(f"\n⏰ Авто-постинг: 10:00 МСК\nКанал: {CHANNEL_ID}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ─── Callback: кнопки предсказаний на постах канала ──────────────────────────
async def prediction_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query    = update.callback_query
    user     = query.from_user
    btn_type = query.data[5:]  # убираем "pred_"

    if is_btn_on_cooldown(user.id, btn_type):
        await query.answer("Уже отправила тебе сегодня — загляни в личку 🌙")
        return

    pool = load_texts().get(btn_type, [])
    if not pool:
        await query.answer("Скоро пополню 🔮")
        return

    name = f"@{user.username}" if user.username else user.first_name
    text = random.choice(pool).replace("{name}", name)

    try:
        await context.bot.send_message(chat_id=user.id, text=text)
        cooldowns_btn[(user.id, btn_type)] = datetime.now()
        await query.answer("Отправила в личку ✨")
    except Exception:
        await query.answer(
            "Напиши мне /start — и получишь ответ в личку! 🔮",
            show_alert=True
        )

# ─── Клавиатуры меню ─────────────────────────────────────────────────────────
def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Триггеры в комментах (карта/кофе/руна)", callback_data="menu_triggers")],
        [InlineKeyboardButton("💫 Кнопки на постах (любовь/деньги/карты)", callback_data="menu_btns")],
    ])

def triggers_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎴 «карта» — ответы на слово карта", callback_data="menu_karta")],
        [InlineKeyboardButton("☕ «кофе» — ответы на слово кофе",  callback_data="menu_kofe")],
        [InlineKeyboardButton("🌿 «руна» — ответы на слово руна",  callback_data="menu_runa")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
    ])

def btn_texts_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❤️ Что он чувствует", callback_data="menu_button_love")],
        [InlineKeyboardButton("💰 Денежный совет",   callback_data="menu_button_money")],
        [InlineKeyboardButton("🔮 Карты отвечают",   callback_data="menu_button_cards")],
        [InlineKeyboardButton("◀️ Назад",            callback_data="back_main")],
    ])

def key_menu_keyboard(key: str) -> InlineKeyboardMarkup:
    back_cb = "menu_btns" if key.startswith("button_") else "menu_triggers"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Показать все тексты",   callback_data=f"list_{key}")],
        [InlineKeyboardButton("➕ Добавить предсказание", callback_data=f"add_{key}")],
        [InlineKeyboardButton("🗑 Удалить предсказание",  callback_data=f"del_{key}")],
        [InlineKeyboardButton("◀️ Назад",                 callback_data=back_cb)],
    ])

def back_keyboard(key: str) -> InlineKeyboardMarkup:
    if key.startswith("button_"):
        back_cb = "menu_btns"
    else:
        back_cb = "menu_triggers"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад",        callback_data=back_cb)],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")],
    ])

# ─── Главное меню ─────────────────────────────────────────────────────────────
async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет доступа.")
        return
    await update.message.reply_text(
        "🎛 *Панель управления*\n\n"
        "💬 *Триггеры* — тексты, которые бот пишет в ответ на слова в комментах (карта / кофе / руна)\n"
        "💫 *Кнопки* — тексты, которые бот шлёт в личку когда подписчик жмёт кнопку в посте\n\n"
        "Выбери раздел:",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

# ─── Обработчик кнопок меню (ConversationHandler) ────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        await query.edit_message_text("❌ Нет доступа.")
        return ConversationHandler.END

    data = query.data

    if data == "back_main":
        await query.edit_message_text(
            "🎛 *Панель управления*\n\nВыбери раздел:",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )
        return ConversationHandler.END

    if data == "menu_triggers":
        await query.edit_message_text(
            "💬 *Триггеры в комментах*\n\n"
            "Когда подписчик пишет слово в комментах — бот отвечает случайным текстом из пула.\n\n"
            "Выбери слово чтобы управлять пулом текстов:",
            parse_mode="Markdown",
            reply_markup=triggers_keyboard()
        )
        return ConversationHandler.END

    if data == "menu_btns":
        await query.edit_message_text(
            "💫 *Кнопки на постах*\n\n"
            "Когда подписчик нажимает кнопку в посте канала — бот шлёт ему предсказание в личку.\n\n"
            "Выбери тип кнопки чтобы управлять пулом текстов:",
            parse_mode="Markdown",
            reply_markup=btn_texts_keyboard()
        )
        return ConversationHandler.END

    if data.startswith("menu_"):
        key   = data[5:]
        count = len(load_texts().get(key, []))
        await query.edit_message_text(
            f"{KEY_LABELS.get(key, key)}\n\nВ пуле: *{count}* предсказаний\n\nЧто хочешь сделать?",
            parse_mode="Markdown",
            reply_markup=key_menu_keyboard(key)
        )
        return ConversationHandler.END

    if data.startswith("list_"):
        key   = data[5:]
        texts = load_texts().get(key, [])
        if not texts:
            await query.edit_message_text(f"Пул {KEY_LABELS.get(key, key)} пуст.", reply_markup=back_keyboard(key))
            return ConversationHandler.END
        lines = [f"📋 {KEY_LABELS.get(key, key)} — {len(texts)} шт:\n"]
        for i, t in enumerate(texts, 1):
            preview = t[:70].replace("\n", " ")
            lines.append(f"{i}. {preview}…")
        await query.edit_message_text("\n".join(lines), reply_markup=back_keyboard(key))
        return ConversationHandler.END

    if data.startswith("add_"):
        key = data[4:]
        context.user_data["add_key"] = key
        await query.edit_message_text(
            f"➕ *Добавление в {KEY_LABELS.get(key, key)}*\n\n"
            f"Напиши текст нового предсказания.\n"
            f"💡 `{{name}}` → имя пользователя автоматически.\n\n"
            f"Отправь /cancel для отмены.",
            parse_mode="Markdown"
        )
        return WAITING_TEXT

    if data.startswith("del_"):
        key   = data[4:]
        texts = load_texts().get(key, [])
        if not texts:
            await query.edit_message_text(f"Пул {KEY_LABELS.get(key, key)} пуст.", reply_markup=back_keyboard(key))
            return ConversationHandler.END
        context.user_data["del_key"] = key
        lines = [f"🗑 *Удаление из {KEY_LABELS.get(key, key)}*\n\nКакой номер?\n"]
        for i, t in enumerate(texts, 1):
            preview = t[:60].replace("\n", " ")
            lines.append(f"{i}. {preview}…")
        lines.append("\nНапиши номер или /cancel.")
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown")
        return WAITING_DELETE_NUM

    return ConversationHandler.END

# ─── Ввод нового предсказания ─────────────────────────────────────────────────
async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    key      = context.user_data.get("add_key")
    new_text = update.message.text.strip()
    data     = load_texts()
    if key not in data:
        data[key] = []
    data[key].append(new_text)
    save_texts(data)
    await update.message.reply_text(
        f"✅ Добавлено в {KEY_LABELS.get(key, key)}!\nВ пуле: *{len(data[key])}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Назад", callback_data=f"menu_{key}"),
            InlineKeyboardButton("🏠 Меню",  callback_data="back_main"),
        ]])
    )
    return ConversationHandler.END

# ─── Ввод номера для удаления ─────────────────────────────────────────────────
async def receive_delete_num(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    key = context.user_data.get("del_key")
    try:
        idx = int(update.message.text.strip()) - 1
    except ValueError:
        await update.message.reply_text("Напиши просто число, например: 3")
        return WAITING_DELETE_NUM
    data = load_texts()
    pool = data.get(key, [])
    if idx < 0 or idx >= len(pool):
        await update.message.reply_text(f"Нет номера {idx+1}. В пуле: {len(pool)}.")
        return WAITING_DELETE_NUM
    removed = pool.pop(idx)
    save_texts(data)
    preview = removed[:60].replace("\n", " ")
    await update.message.reply_text(
        f"🗑 Удалено:\n«{preview}…»\n\nОсталось: *{len(pool)}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Назад", callback_data=f"menu_{key}"),
            InlineKeyboardButton("🏠 Меню",  callback_data="back_main"),
        ]])
    )
    return ConversationHandler.END

# ─── Отмена ───────────────────────────────────────────────────────────────────
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

# ─── Ответ в комментариях ─────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not msg.text:
        return
    if update.effective_chat.type == "private":
        return
    text = msg.text.strip()
    user = msg.from_user
    name = f"@{user.username}" if user.username else user.first_name

    if   KARTA.match(text): key = "karta"
    elif KOFE.match(text):  key = "kofe"
    elif RUNA.match(text):  key = "runa"
    else: return

    if is_on_cooldown(user.id):
        return

    pool = load_texts().get(key, [])
    if not pool:
        return

    cooldowns[user.id] = datetime.now()
    response = random.choice(pool).replace("{name}", name)
    await msg.reply_text(response)
    logger.info(f"Ответил {name} | {key}")

# ─── Запуск ───────────────────────────────────────────────────────────────────
def main() -> None:
    if BOT_TOKEN == "ВСТАВЬ_ТОКЕН_ЗДЕСЬ":
        logger.error("Токен не задан! Задай BOT_TOKEN в переменных окружения.")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # APScheduler — авто-постинг в 10:00 МСК ежедневно
    scheduler = AsyncIOScheduler(timezone=MSK)
    scheduler.add_job(
        scheduled_morning_post,
        CronTrigger(hour=10, minute=0, timezone=MSK),
        args=[app],
        id="morning_post",
    )
    # Воскресный вечерний пост (ответы на карты)
    scheduler.add_job(
        scheduled_evening_post,
        CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=MSK),
        args=[app],
        id="evening_post",
    )
    scheduler.start()
    logger.info("APScheduler запущен ✅ (10:00 МСК ежедневно)")

    # ConversationHandler — управление пулами через кнопки (только для admin)
    # Паттерн ^(?!pred_) — не захватывает кнопки предсказаний с постов
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern=r"^(?!pred_)")],
        states={
            WAITING_TEXT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text)],
            WAITING_DELETE_NUM: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_delete_num)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    app.add_handler(CommandHandler("menu",     cmd_menu))
    app.add_handler(CommandHandler("start",    cmd_menu))
    app.add_handler(CommandHandler("test",     cmd_test))      # тест-пост сегодня → тест-канал
    app.add_handler(CommandHandler("testpost", cmd_testpost))  # тест конкретного поста по id
    app.add_handler(CommandHandler("post",     cmd_post))      # немедленный пост в основной канал
    app.add_handler(CommandHandler("schedule", cmd_schedule))  # план на неделю
    # Кнопки предсказаний — перехватываем ДО conv
    app.add_handler(CallbackQueryHandler(prediction_callback, pattern=r"^pred_"))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен ✅")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
