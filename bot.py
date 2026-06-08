"""
Бот Софии — отвечает на КАРТА / КОФЕ / РУНА в комментариях.
Управление через кнопки: /menu в личке боту.
"""

import os, re, json, random, logging
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN  = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_ЗДЕСЬ")
ADMIN_ID   = int(os.getenv("ADMIN_ID", "0"))
TEXTS_FILE = Path("texts.json")

# ─── Состояния диалога ────────────────────────────────────────────────────────
WAITING_TEXT, WAITING_DELETE_NUM = range(2)

# ─── Кулдаун ──────────────────────────────────────────────────────────────────
COOLDOWN_HOURS = 24
cooldowns: dict[int, datetime] = {}

def is_on_cooldown(user_id: int) -> bool:
    last = cooldowns.get(user_id)
    return last is not None and datetime.now() - last < timedelta(hours=COOLDOWN_HOURS)

# ─── Паттерны ────────────────────────────────────────────────────────────────
KARTA = re.compile(r"^\s*к+а+р+т[аы]?[!?.,…]?\s*$", re.IGNORECASE)
KOFE  = re.compile(r"^\s*к+о+ф+[её][!?.,…]?\s*$",   re.IGNORECASE)
RUNA  = re.compile(r"^\s*р+у+н[аы]?[!?.,…]?\s*$",   re.IGNORECASE)

KEY_LABELS = {"karta": "🎴 Карта", "kofe": "☕ Кофе", "runa": "🌿 Руна"}

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
}

# ─── Работа с texts.json ──────────────────────────────────────────────────────
def load_texts() -> dict:
    if TEXTS_FILE.exists():
        return json.loads(TEXTS_FILE.read_text(encoding="utf-8"))
    save_texts(DEFAULT_TEXTS)
    return DEFAULT_TEXTS

def save_texts(data: dict) -> None:
    TEXTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ─── Клавиатуры ──────────────────────────────────────────────────────────────
def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎴 Карта",  callback_data="menu_karta"),
            InlineKeyboardButton("☕ Кофе",   callback_data="menu_kofe"),
            InlineKeyboardButton("🌿 Руна",   callback_data="menu_runa"),
        ],
    ])

def key_menu_keyboard(key: str) -> InlineKeyboardMarkup:
    label = KEY_LABELS[key]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📋 Показать все тексты",    callback_data=f"list_{key}")],
        [InlineKeyboardButton(f"➕ Добавить предсказание",  callback_data=f"add_{key}")],
        [InlineKeyboardButton(f"🗑 Удалить предсказание",   callback_data=f"del_{key}")],
        [InlineKeyboardButton("◀️ Назад",                   callback_data="back_main")],
    ])

def back_keyboard(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад",  callback_data=f"menu_{key}")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")],
    ])

# ─── Главное меню ─────────────────────────────────────────────────────────────
async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет доступа.")
        return
    await update.message.reply_text(
        "🎛 *Панель управления*\n\nВыбери тип предсказания:",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

# ─── Обработчик кнопок ────────────────────────────────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        await query.edit_message_text("❌ Нет доступа.")
        return ConversationHandler.END

    data = query.data

    # Главное меню
    if data == "back_main":
        await query.edit_message_text(
            "🎛 *Панель управления*\n\nВыбери тип предсказания:",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )
        return ConversationHandler.END

    # Меню конкретного типа
    if data.startswith("menu_"):
        key = data[5:]
        count = len(load_texts().get(key, []))
        await query.edit_message_text(
            f"{KEY_LABELS[key]}\n\nСейчас в пуле: *{count}* предсказаний\n\nЧто хочешь сделать?",
            parse_mode="Markdown",
            reply_markup=key_menu_keyboard(key)
        )
        return ConversationHandler.END

    # Показать список
    if data.startswith("list_"):
        key = data[5:]
        texts = load_texts().get(key, [])
        if not texts:
            await query.edit_message_text(
                f"Пул {KEY_LABELS[key]} пуст.",
                reply_markup=back_keyboard(key)
            )
            return ConversationHandler.END

        lines = [f"📋 {KEY_LABELS[key]} — {len(texts)} предсказаний:\n"]
        for i, t in enumerate(texts, 1):
            preview = t[:70].replace("\n", " ")
            lines.append(f"{i}. {preview}…")
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=back_keyboard(key)
        )
        return ConversationHandler.END

    # Добавить текст — запрашиваем ввод
    if data.startswith("add_"):
        key = data[4:]
        context.user_data["add_key"] = key
        await query.edit_message_text(
            f"➕ *Добавление в {KEY_LABELS[key]}*\n\n"
            f"Напиши текст нового предсказания.\n\n"
            f"💡 `{{name}}` заменится на имя пользователя автоматически.\n\n"
            f"Отправь /cancel чтобы отменить.",
            parse_mode="Markdown"
        )
        return WAITING_TEXT

    # Удалить — запрашиваем номер
    if data.startswith("del_"):
        key = data[4:]
        texts = load_texts().get(key, [])
        if not texts:
            await query.edit_message_text(
                f"Пул {KEY_LABELS[key]} пуст — удалять нечего.",
                reply_markup=back_keyboard(key)
            )
            return ConversationHandler.END

        context.user_data["del_key"] = key
        lines = [f"🗑 *Удаление из {KEY_LABELS[key]}*\n\nКакой номер удалить?\n"]
        for i, t in enumerate(texts, 1):
            preview = t[:60].replace("\n", " ")
            lines.append(f"{i}. {preview}…")
        lines.append("\nНапиши номер или /cancel для отмены.")
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown")
        return WAITING_DELETE_NUM

    return ConversationHandler.END

# ─── Получить текст нового предсказания ───────────────────────────────────────
async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    key = context.user_data.get("add_key")
    new_text = update.message.text.strip()

    data = load_texts()
    data[key].append(new_text)
    save_texts(data)

    await update.message.reply_text(
        f"✅ Добавлено в {KEY_LABELS[key]}!\nТеперь в пуле: *{len(data[key])}* предсказаний.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"◀️ К {KEY_LABELS[key]}", callback_data=f"menu_{key}"),
            InlineKeyboardButton("🏠 Меню", callback_data="back_main"),
        ]])
    )
    return ConversationHandler.END

# ─── Получить номер для удаления ──────────────────────────────────────────────
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
        await update.message.reply_text(f"Нет номера {idx+1}. Всего в пуле: {len(pool)}. Попробуй ещё.")
        return WAITING_DELETE_NUM

    removed = pool.pop(idx)
    save_texts(data)
    preview = removed[:60].replace("\n", " ")

    await update.message.reply_text(
        f"🗑 Удалено:\n«{preview}…»\n\nОсталось в пуле: *{len(pool)}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"◀️ К {KEY_LABELS[key]}", callback_data=f"menu_{key}"),
            InlineKeyboardButton("🏠 Меню", callback_data="back_main"),
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

    # В личке для админа — только меню, не отвечаем на триггеры
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
        logger.error("Токен не задан!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # ConversationHandler для управления текстами через кнопки
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler)],
        states={
            WAITING_TEXT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text)],
            WAITING_DELETE_NUM: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_delete_num)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("start", cmd_menu))   # /start тоже открывает меню
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен ✅")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
