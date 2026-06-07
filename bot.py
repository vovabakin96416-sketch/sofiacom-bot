"""
Бот Софии — отвечает в комментариях на КАРТА / КОФЕ / РУНА
Тексты хранятся в texts.json — редактируются прямо из Telegram, GitHub не нужен.

Запуск локально: python bot.py
Деплой: Koyeb (бесплатно, см. ИНСТРУКЦИЯ-запуск.md)
"""

import os
import re
import json
import random
import logging
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN  = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_ЗДЕСЬ")
ADMIN_ID   = int(os.getenv("ADMIN_ID", "0"))   # твой Telegram user_id (узнай у @userinfobot)
TEXTS_FILE = Path("texts.json")

# ─── Кулдаун ──────────────────────────────────────────────────────────────────
COOLDOWN_HOURS = 24
cooldowns: dict[int, datetime] = {}

def is_on_cooldown(user_id: int) -> bool:
    last = cooldowns.get(user_id)
    return last is not None and datetime.now() - last < timedelta(hours=COOLDOWN_HOURS)

# ─── Паттерны (ловят опечатки, регистр, повторы букв) ────────────────────────
KARTA = re.compile(r"^\s*к+а+р+т[аы]?[!?.,…]?\s*$", re.IGNORECASE)
KOFE  = re.compile(r"^\s*к+о+ф+[её][!?.,…]?\s*$",   re.IGNORECASE)
RUNA  = re.compile(r"^\s*р+у+н[аы]?[!?.,…]?\s*$",   re.IGNORECASE)

# ─── Тексты по умолчанию (при первом запуске сохранятся в texts.json) ─────────
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
        "☕ {name}, смотрю внимательно...\n\nНа дне — сердце. Отношения в фокусе сегодня: либо потеплеет, либо прояснится.\nГлавное — говори прямо, не обиняками. Тебя поймут.\n\nКак с этим сейчас? 💗",
    ],
    "runa": [
        "🌿 {name}, тяну рунное послание...\n\n⚡ Выпала Тейваз — руна воина и направления.\nТвоя сила сегодня в ясности намерения. Иди туда, куда ведёт внутренний компас — не оглядывайся.\n\nРезонирует? 🌿",
        "🌿 {name}, руна брошена...\n\n🌊 Выпала Лагуз — руна потока и интуиции.\nСегодня не день для логики. Доверься чувствам — они точнее любых расчётов.\n\nКак тебе такое послание? ✨",
        "🌿 {name}, руна для тебя...\n\n☀️ Выпала Соулу — руна солнца и победы.\nЭнергия сейчас высокая. Начинай то, что откладывал(а) — момент благоприятный.\n\nСовпало с ощущениями? 💫",
        "🌿 {name}, послание рун...\n\n🌱 Выпала Беркана — руна роста и нового начала.\nЧто-то новое хочет прорасти в твоей жизни. Дай этому пространство и время.\n\nЧто прорастает у тебя сейчас? 🌿",
        "🌿 {name}, руны говорят...\n\n🛡️ Выпала Альгиз — руна защиты.\nСегодня твоё поле сильное. Можешь спокойно двигаться вперёд — ты под защитой.\n\nЧувствуешь эту опору? ✨",
        "🌿 {name}, тяну руну...\n\n🔄 Выпала Эваз — руна движения и изменений.\nЧто-то приходит в движение. Не сопротивляйся переменам — они в твою пользу.\n\nЧто сейчас меняется в твоей жизни? 💫",
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

# ─── Ответ на триггер ─────────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not msg.text:
        return

    text = msg.text.strip()
    user = msg.from_user
    name = f"@{user.username}" if user.username else user.first_name

    if   KARTA.match(text): key = "karta"
    elif KOFE.match(text):  key = "kofe"
    elif RUNA.match(text):  key = "runa"
    else: return

    if is_on_cooldown(user.id):
        logger.info(f"Кулдаун: {name}")
        return

    pool = load_texts().get(key, [])
    if not pool:
        return

    cooldowns[user.id] = datetime.now()
    response = random.choice(pool).replace("{name}", name)
    await msg.reply_text(response)
    logger.info(f"Ответил {name} | триггер: {key}")

# ─── Админ-команды (только для ADMIN_ID) ──────────────────────────────────────
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("❌ Нет доступа.")
            return
        await func(update, context)
    return wrapper

@admin_only
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /list karta|kofe|runa — показать все тексты с номерами
    """
    args = context.args
    if not args or args[0] not in ("karta", "kofe", "runa"):
        await update.message.reply_text("Использование: /list karta  или /list kofe  или /list runa")
        return

    key = args[0]
    texts = load_texts().get(key, [])
    if not texts:
        await update.message.reply_text(f"Пул «{key}» пуст.")
        return

    lines = [f"📋 Пул «{key}» — {len(texts)} текстов:\n"]
    for i, t in enumerate(texts, 1):
        preview = t[:80].replace("\n", " ")
        lines.append(f"{i}. {preview}...")

    await update.message.reply_text("\n".join(lines))

@admin_only
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /addkarta Текст нового предсказания...
    /addkofe  Текст нового предсказания...
    /addruna  Текст нового предсказания...
    """
    cmd = update.message.text.split()[0].lstrip("/").lower()  # addkarta / addkofe / addruna
    key_map = {"addkarta": "karta", "addkofe": "kofe", "addruna": "runa"}
    key = key_map.get(cmd)

    if not key:
        await update.message.reply_text("Неизвестная команда.")
        return

    new_text = update.message.text.partition(" ")[2].strip()
    if not new_text:
        await update.message.reply_text(f"Напиши текст после /{cmd}")
        return

    data = load_texts()
    data[key].append(new_text)
    save_texts(data)
    await update.message.reply_text(f"✅ Добавлено в пул «{key}». Теперь {len(data[key])} текстов.")

@admin_only
async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /delete karta 3 — удалить текст №3 из пула «karta»
    """
    args = context.args
    if len(args) < 2 or args[0] not in ("karta", "kofe", "runa"):
        await update.message.reply_text("Использование: /delete karta 3\n(сначала /list karta чтобы узнать номер)")
        return

    key = args[0]
    try:
        idx = int(args[1]) - 1
    except ValueError:
        await update.message.reply_text("Номер должен быть числом.")
        return

    data = load_texts()
    pool = data.get(key, [])
    if idx < 0 or idx >= len(pool):
        await update.message.reply_text(f"Нет текста с номером {idx+1}. Всего в пуле: {len(pool)}.")
        return

    removed = pool.pop(idx)
    save_texts(data)
    preview = removed[:60].replace("\n", " ")
    await update.message.reply_text(f"🗑 Удалён текст №{idx+1}:\n«{preview}...»\nОсталось: {len(pool)}")

@admin_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🔧 Команды управления ботом:\n\n"
        "/list karta — показать все тексты для КАРТА\n"
        "/list kofe  — показать все тексты для КОФЕ\n"
        "/list runa  — показать все тексты для РУНА\n\n"
        "/addkarta Текст... — добавить текст в пул КАРТА\n"
        "/addkofe Текст...  — добавить текст в пул КОФЕ\n"
        "/addruna Текст...  — добавить текст в пул РУНА\n\n"
        "/delete karta 3 — удалить текст №3 из пула КАРТА\n\n"
        "💡 {name} в тексте заменяется на ник пользователя автоматически."
    )

# ─── Запуск ──────────────────────────────────────────────────────────────────
def main() -> None:
    if BOT_TOKEN == "ВСТАВЬ_ТОКЕН_ЗДЕСЬ":
        logger.error("Токен не задан! Укажи BOT_TOKEN в переменной окружения.")
        return
    if ADMIN_ID == 0:
        logger.warning("ADMIN_ID не задан — команды /add, /delete, /list работать не будут.")

    app = Application.builder().token(BOT_TOKEN).build()

    # Триггеры в комментах
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Админ-команды
    app.add_handler(CommandHandler("list",     cmd_list))
    app.add_handler(CommandHandler("addkarta", cmd_add))
    app.add_handler(CommandHandler("addkofe",  cmd_add))
    app.add_handler(CommandHandler("addruna",  cmd_add))
    app.add_handler(CommandHandler("delete",   cmd_delete))
    app.add_handler(CommandHandler("help",     cmd_help))

    logger.info("Бот запущен ✅")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

