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
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.error import BadRequest, NetworkError
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
SETTINGS_FILE = Path("settings.json")

# ─── Состояния диалога ────────────────────────────────────────────────────────
WAITING_TEXT, WAITING_DELETE_NUM, WAITING_EDIT, WAITING_PHOTO = range(4)

# ─── Кулдауны ─────────────────────────────────────────────────────────────────
COOLDOWN_HOURS = 24
# Кулдаун триггеров — ОТДЕЛЬНО по каждому слову (карта/кофе/руна), чтобы один
# человек мог получить и карту, и руну в течение суток (а не одно на всё).
cooldowns:     dict[tuple[int, str], datetime] = {}
cooldowns_btn: dict[tuple[int, str], datetime] = {}

# ─── Очередь постов на одобрение ──────────────────────────────────────────────
pending_posts: dict[str, dict] = {}  # slot ("morning"/"evening") → post dict

def is_on_cooldown(user_id: int, key: str) -> bool:
    last = cooldowns.get((user_id, key))
    return last is not None and datetime.now() - last < timedelta(hours=COOLDOWN_HOURS)

def is_btn_on_cooldown(user_id: int, btn_type: str) -> bool:
    last = cooldowns_btn.get((user_id, btn_type))
    return last is not None and datetime.now() - last < timedelta(hours=COOLDOWN_HOURS)

def is_admin(update: Update) -> bool:
    """Безопасная проверка прав админа. `effective_user` может быть None
    (анонимный админ, системное сообщение) — тогда False, без падения."""
    user = update.effective_user
    return user is not None and user.id == ADMIN_ID

# ─── Паттерны триггеров ───────────────────────────────────────────────────────
KARTA = re.compile(r"^\s*к+а+р+т[аы]?[!?.,…]?\s*$", re.IGNORECASE)
KOFE  = re.compile(r"^\s*к+о+ф+[её][!?.,…]?\s*$",   re.IGNORECASE)
RUNA  = re.compile(r"^\s*р+у+н[аы]?[!?.,…]?\s*$",   re.IGNORECASE)
ZNAK   = re.compile(r"^\s*з+н+а+к+[!?.,…]?\s*$",        re.IGNORECASE)
LYUBOV = re.compile(r"^\s*л+ю+б+о+в+ь?[!?.,…]?\s*$",    re.IGNORECASE)
DENGI  = re.compile(r"^\s*д+е+н+ь?г+и+[!?.,…]?\s*$",     re.IGNORECASE)
SVET   = re.compile(r"^\s*с+в+е+т+[!?.,…]?\s*$",        re.IGNORECASE)
ORACLE = re.compile(r"^\s*(д+а+|н+е+т+)[!?.,…]?\s*$",   re.IGNORECASE)

# Названия карт Таро и рун → попадают в пул «карта»
CARD_NAME = re.compile(
    r"^\s*("
    # Старшие арканы
    r"жрица|маг|дурак|шут|императрица|император|иерофант|влюблённые?|влюбленные?"
    r"|колесница|сила|отшельник|колесо|колесо фортуны|справедливость"
    r"|повешенный|смерть|умеренность|дьявол|башня|звезда|луна|солнце|суд|мир"
    # Таро в целом
    r"|таро|тарот"
    # Руны (популярные)
    r"|феху|уруз|турисаз|ансуз|райдо|кано|гебо|вуньо|хагалаз|наутиз|иса|йера"
    r"|эйваз|перт|альгиз|соулу|тейваз|беркана|эваз|манназ|лагуз|ингуз|дагаз|отала"
    r")[!?.,…]?\s*$",
    re.IGNORECASE
)

KEY_LABELS = {
    "karta":         "🎴 Карта",
    "kofe":          "☕ Кофе",
    "runa":          "🌿 Руна",
    "znak":          "🔮 Знак",
    "lyubov":        "❤️ Любовь",
    "dengi":         "💰 Деньги",
    "svet":          "✨ Свет",
    "oracle":        "🎲 Да/Нет (оракул)",
    "button_love":   "❤️ Что он чувствует",
    "button_money":  "💰 Денежный совет",
    "button_cards":  "🔮 Карты отвечают",
}

# ─── Тексты по умолчанию ──────────────────────────────────────────────────────
DEFAULT_TEXTS = {
    "karta": [
        "🎴 {name}, тяну твою карту...\n\n👑 Сегодня колода про изобилие. У тебя уже больше, чем ты позволяешь себе видеть. Заметь это — и поток усилится.\n\nРезонирует? ✨",
        "🎴 {name}, карта дня для тебя...\n\n🦁 Твоя сила сегодня в мягкости. Не дави на ситуацию — отпусти хватку, и она развернётся сама.\n\nКак ощущение? 💗",
        "🎴 {name}, вот что говорит колода...\n\n🎡 Колесо поворачивается. То, что казалось застрявшим, вот-вот сдвинется. Не торопи — но будь готова сказать «да».\n\nСовпало? 👇",
        "🎴 {name}, твоя карта сегодня...\n\n🕊️ День отпускания. Что-то отжившее просит уйти. Это не потеря — это место для нового.\n\nЧто отзывается? 🌿",
        "🎴 {name}, смотрю в колоду...\n\n🌍 Какой-то круг закрывается. Оглянись с благодарностью — ты прошла больше, чем кажется.\n\nЧувствуешь это? ✨",
        "🎴 {name}, карта вытянута...\n\n🌅 После трудного — рассвет. Если было тяжело, колода шепчет: свет уже близко. Доживи до утра.\n\nКак тебе? 🌙",
        "🎴 {name}, вот твоё послание...\n\n🌸 Время позаботиться о себе без вины. Ты много отдаёшь. Сегодня верни немного себе.\n\nСделаешь что-то для себя? 💗",
        "🎴 {name}, твоя карта дня...\n\n⭐ Маленький шаг сегодня важнее большого плана. Сделай одно дело, которое откладывала — и день сложится.\n\nЧто это за дело? ✨",
        "🎴 {name}, колода открылась...\n\n🤲 Сегодня можно просить помощи. Рядом есть тот, кто поддержит — не тяни всё одна.\n\nК кому потянулась мысль? 🌿",
        "🎴 {name}, тяну твою карту...\n\n🔑 Сегодня день решений. То, что ты откладывала «на потом», просит ответа именно сейчас. Не идеального — просто честного.\n\nЧто это за вопрос? ✨",
        "🎴 {name}, карта дня для тебя...\n\n🌙 Замедлись. Сегодня важнее почувствовать, чем понять. Ответ придёт в тишине, а не в спешке.\n\nГде сейчас твоя спешка? 💗",
        "🎴 {name}, колода открылась...\n\n💞 День про близость. Скажи тёплое слово тому, кого давно не баловала вниманием — и тебе вернётся вдвойне.\n\nКому напишешь? 🌸"
    ],
    "kofe": [
        "☕ {name}, смотрю в твою чашку...\n\nНа дне — дорога. Скоро путь, пусть и внутренний. И рядом человек, который думает о тебе чаще, чем говорит.\n\nЧто-то совпало? ☕",
        "☕ {name}, читаю кофейную гущу...\n\nВижу круг — цикл завершается, готовься к новому витку. Тревога вокруг денег уходит, но не сразу. Не форсируй.\n\nРезонирует? 🌙",
        "☕ {name}, заглядываю в чашку...\n\nНа дне — птица. Это свобода от того, что давно держало. Решение, которое ты откладывала, уже созрело.\n\nУгадала? 💫",
        "☕ {name}, гуща раскрывается...\n\nВижу сердце ближе к краю — отношения в фокусе этих дней. Либо потеплеет, либо прояснится. Говори прямо — поймут.\n\nКак с этим сейчас? 💗",
        "☕ {name}, смотрю внимательно...\n\nВетка с листьями — рост и хорошие новости. Что-то, во что ты вложилась, начинает плодоносить. Не сдавайся на финише.\n\nЧто прорастает? 🌿",
        "☕ {name}, твоя чашка говорит...\n\nНа дне — ключ. Открывается дверь, которая казалась закрытой. Следи за неожиданным предложением на этой неделе.\n\nЖдёшь чего-то? ✨",
        "☕ {name}, смотрю в твою чашку...\n\nНа дне — дом. Уют и тыл выходят на первый план. Хороший момент навести порядок — в углу комнаты и в мыслях.\n\nЧто просит порядка? 🏡",
        "☕ {name}, читаю гущу...\n\nВижу две дороги, что сходятся. Скоро выбор — не пугайся, оба пути хорошие. Слушай, где теплее.\n\nЧувствуешь развилку? ✨",
        "☕ {name}, чашка говорит...\n\nНа дне — солнце. Светлая полоса близко. То, что тревожило, начнёт растворяться уже на этой неделе.\n\nВо что хочется верить? ☀️"
    ],
    "runa": [
        "🌿 {name}, тяну рунное послание...\n\n🌾 Выпала Феху — руна изобилия. Спроси себя: что я выращиваю, а что просто терплю? Питай первое.\n\nРезонирует? 🌿",
        "🌿 {name}, руна брошена...\n\n🛤️ Выпала Райдо — руна пути. Свериться важнее, чем ускориться. Туда ли ты идёшь — или по привычной колее?\n\nКак тебе? ✨",
        "🌿 {name}, руна для тебя...\n\n🌞 Выпала Вуньо — руна радости. Счастье не впереди, «когда наладится». Оно в мелочах сегодня. Заметь хотя бы одну.\n\nЧто порадовало? 💫",
        "🌿 {name}, послание рун...\n\n🌅 Выпала Дагаз — руна прорыва. Рассвет после долгой ночи. Если было темно — свет уже близко. Доживи до утра.\n\nЧувствуешь поворот? 🌿",
        "🌿 {name}, руны говорят...\n\n🛡️ Выпала Альгиз — руна защиты. Твоё поле сегодня сильное. Двигайся вперёд — ты под охраной.\n\nЧувствуешь опору? ✨",
        "🌿 {name}, тяну руну...\n\n⚡ Выпала Тейваз — руна воина. Сила в ясности намерения. Реши, чего хочешь, — и иди прямо.\n\nКуда ведёт компас? 💫",
        "🌿 {name}, руна выпала...\n\n🌱 Беркана — руна роста. Что-то новое хочет прорасти. Дай ему пространство и тишину, пока не окрепнет.\n\nЧто начинается у тебя? 🌿",
        "🌿 {name}, послание для тебя...\n\n🌊 Лагуз — руна потока. Сегодня не день логики. Доверься интуиции — она точнее расчётов.\n\nЧто подсказывает чутьё? ✨",
        "🌿 {name}, тяну руну...\n\n🏡 Выпала Отала — руна дома и корней. Сила сейчас в опоре: семья, традиции, своё место. Обопрись на родное.\n\nГде твоя опора? 🌿",
        "🌿 {name}, руна для тебя...\n\n🔥 Выпала Кано — руна света и ясности. То, что было скрыто, проясняется. Творческая искра возвращается. Действуй, пока горит.\n\nЧто проясняется? ✨"
    ],
    "button_love": [
        "🔮 {name}, смотрю на твою ситуацию...\n\nОн чувствует больше, чем показывает — его держит не равнодушие, а страх ошибиться. Не форсируй, дай ему проявиться. Один тёплый знак с твоей стороны откроет дверь.\n\n💗 Напиши «карта» в канале — получишь послание на день",
        "🔮 {name}, карты об отношениях...\n\nЭтот человек думает о тебе чаще, чем ты думаешь. Сейчас главное — быть собой, а не угадывать, как понравиться. Твоя подлинность и есть притяжение.\n\n💗 Напиши «карта» в канале за посланием",
        "🔮 {name}, вижу твоё сердце...\n\nКарты говорят: прежде чем гадать «любит/не любит», спроси — тебе с ним хорошо? Любовь даёт силы, а не отнимает. Вот главный ответ.\n\n❤️ Напиши «карта» в канале — там твоя карта дня",
        "🔮 {name}, смотрю на вашу связь...\n\nСейчас не время решать сгоряча. Дай ситуации неделю тишины. Что важно — останется, наносное — отпадёт само. Не торопи развязку.\n\n💫 Напиши «карта» в канале за посланием",
        "🔮 {name}, смотрю на вашу связь...\n\nНе выбирай недоступных в надежде «заслужить» любовь. Ты достойна того, кто выбирает тебя сам, без борьбы. Карты видят: такой человек ближе, чем ты думаешь.\n\n💗 Напиши «карта» в канале за посланием",
        "🔮 {name}, карты об отношениях...\n\nЛучшее, что ты можешь сделать для любви прямо сейчас — наполнить себя. Счастливую женщину хотят беречь. Начни с себя, остальное подтянется.\n\n❤️ Напиши «карта» в канале — там твоя карта дня"
    ],
    "button_money": [
        "💰 {name}, карты о деньгах...\n\nСейчас энергия денег копится — не время крупных трат. Отложи одно необязательное расходование сегодня. И присмотрись к идее, которую давно откладываешь: в ней ключ.\n\n✨ Напиши «карта» в канале за посланием дня",
        "💰 {name}, денежный совет от карт...\n\nТвой потолок — сумма, которую стыдно назвать за свою работу. Подними цену хотя бы мысленно. Сопротивление внутри — вот граница, что пора двигать.\n\n💫 Напиши «карта» в канале — там твоя карта дня",
        "💰 {name}, смотрю в финансовое поле...\n\nВижу поворот: что-то закрытое скоро откроется снова. Следи за неожиданным предложением. И кто-то из окружения может стать нужным знакомством.\n\n🌿 Напиши «карта» в канале за посланием",
        "💰 {name}, карты о потоке...\n\nДеньги идут туда, где их ценят. Поблагодари за то, что уже есть — даже за малое. Благодарность открывает поток быстрее, чем тревога его закрывает.\n\n✨ Напиши «карта» в канале — там послание дня",
        "💰 {name}, карты о деньгах...\n\nНе жди «большой суммы, чтобы начать». Привычка важнее размера: отложи сегодня малое — деньга к деньге. Поток любит регулярность, а не рывки.\n\n✨ Напиши «карта» в канале за посланием дня",
        "💰 {name}, денежный совет...\n\nСпроси честно: за что ты НЕ разрешаешь себе зарабатывать? Часто блок звучит «мне столько не положено». Разреши — и потолок поднимется.\n\n💫 Напиши «карта» в канале — там твоя карта дня"
    ],
    "button_cards": [
        "🔮 {name}, карты отвечают...\n\nДа — но не сразу. Будет шаг, который покажется остановкой. Это не тупик, а поворот. Доверяй процессу и занимайся тем, что в твоих руках сейчас.\n\n✨ Напиши «карта» в канале — там твоя карта дня",
        "🔮 {name}, спрашиваю карты...\n\nОтвет — «подожди». Не из-за отказа, а потому что момент ещё созревает. Торопить — значит упустить. Дай времени сделать свою часть.\n\n💫 Напиши «карта» в канале за посланием",
        "🔮 {name}, карты дают ответ...\n\nОбрати внимание на тело, когда думаешь об этом. Лёгкость внутри — иди. Зажим — подожди. Тело знает ответ раньше ума.\n\n🌙 Напиши «карта» в канале — там твоё послание",
        "🔮 {name}, колода отвечает...\n\nКарты говорят: ответ ты уже знаешь, просто боишься его признать. Сядь в тишине на минуту и спроси прямо. Первое, что всплывёт, — и есть правда.\n\n✨ Напиши «карта» в канале за посланием",
        "🔮 {name}, карты отвечают...\n\nДа, путь открыт — но не через силу, а через лёгкость. Где идёт туго, там не твоя дверь. Иди туда, где открывается само.\n\n✨ Напиши «карта» в канале за посланием",
        "🔮 {name}, спрашиваю карты...\n\nКарты видят: тебе важно перестать всё контролировать. Отпусти хватку хотя бы в одном — и решение придёт оттуда, откуда не ждёшь.\n\n🌙 Напиши «карта» в канале — там твоё послание",
        "🔮 {name}, колода отвечает...\n\nЗнак будет. Следи за повторяющимся образом или фразой в ближайшие дни — это не случайность, а подсказка. Жизнь говорит с тобой мелочами.\n\n💫 Напиши «карта» в канале за посланием"
    ],
    "znak": [
        "🔮 {name}, твой знак на сегодня...\n\nОбрати внимание на то, что повторится дважды за день — слово, число, образ. Это не случайность, а подсказка. Жизнь говорит с тобой мелочами.\n\nЧто уже мелькало? ✨",
        "🔮 {name}, читаю твой знак...\n\nПеро, монетка или встреча «не вовремя» сегодня — добрая весть. Вселенная подмигивает: ты на верном пути.\n\nЗаметишь — улыбнись в ответ 🌙",
        "🔮 {name}, знак для тебя...\n\nЕсли сегодня вспомнится человек из прошлого — это к завершению старой истории. Отпусти мысленно, и станет легче.\n\nКто всплыл в памяти? 🤍",
        "🔮 {name}, твоё послание-знак...\n\nПтица у окна, бабочка, неожиданная песня — сегодня это про надежду. То, чего ждёшь, ближе, чем кажется.\n\nВо что хочется верить? 🕊️",
        "🔮 {name}, знак дня...\n\nСегодня твоё «нет» — тоже знак. Если внутри сопротивление — не ломай себя. Тело знает раньше ума.\n\nГде сейчас сопротивление? 🌿",
        "🔮 {name}, ловлю для тебя знак...\n\nЗелёный свет. То, что задумала, можно начинать — день поддерживает. Не жди идеального момента, он уже здесь.\n\nЧто начнёшь? ☀️"
    ],
    "lyubov": [
        "💗 {name}, послание про любовь...\n\nОн чувствует больше, чем говорит. Молчание — не холод, а неумение выразить. Дай немного пространства, и он потянется.\n\nРезонирует? ❤️",
        "💗 {name}, карты о твоём сердце...\n\nЛюбовь начинается с того, как ты относишься к себе. Наполни себя — и к тебе потянутся за этим теплом.\n\nЧто сделаешь для себя сегодня? 🌸",
        "💗 {name}, про отношения для тебя...\n\nНе выбирай тех, кого нужно «заслуживать». Твой человек выбирает тебя сам, без борьбы и доказательств.\n\nОткликается? ✨",
        "💗 {name}, послание любви...\n\nСейчас не время решать сгоряча. Дай чувствам неделю тишины. Что настоящее — останется, наносное — отпадёт.\n\nЧто на сердце? 🤍",
        "💗 {name}, карты про близость...\n\nСкажи тёплое слово первой. Иногда один честный шаг растапливает месяцы недосказанности.\n\nКому хочется написать? 💌",
        "💗 {name}, твоё любовное послание...\n\nТы достойна спокойной любви — без качелей и тревоги. Если рядом тревожно, это не страсть, а зависимость. Выбирай покой.\n\nЧувствуешь разницу? 🌙"
    ],
    "dengi": [
        "💰 {name}, денежная подсказка...\n\nОтложи сегодня одну необязательную трату — энергия денег копится. А идея, которую откладываешь, и есть твой следующий шаг к доходу.\n\nЧто за идея? ✨",
        "💰 {name}, карты о деньгах...\n\nТвой потолок — сумма, которую стыдно назвать за свою работу. Подними цену хотя бы мысленно. Сопротивление внутри — вот граница, что пора двигать.\n\nГде твой потолок? 💫",
        "💰 {name}, подсказка по деньгам...\n\nДеньги идут туда, где их ценят. Поблагодари за то, что уже есть — даже за малое. Благодарность открывает поток.\n\nЗа что благодаришь? 🌿",
        "💰 {name}, денежное послание...\n\nНе жди «большой суммы, чтобы начать». Отложи сегодня малое — привычка важнее размера. Деньга к деньге.\n\nНачнёшь? 🪙",
        "💰 {name}, карты о потоке...\n\nВижу поворот: что-то закрытое скоро откроется снова. Следи за неожиданным предложением на этой неделе — не отказывай сходу.\n\nЖдёшь чего-то? 🔑",
        "💰 {name}, про твои финансы...\n\nСпроси честно: за что ты НЕ разрешаешь себе зарабатывать? Часто блок звучит «мне столько не положено». Разреши — и потолок поднимется.\n\nЧто отзывается? 💎"
    ],
    "svet": [
        "✨ {name}, твой луч света на сегодня...\n\nТам, где сейчас темно, скоро забрезжит. Сделай один шаг к свету — даже в темноте дорога видна на шаг вперёд.\n\nКакой шаг? 🌅",
        "✨ {name}, свет для тебя...\n\nТвой источник силы сегодня — простое. Прогулка, музыка, тёплый разговор. Не ищи спасения в большом, когда лечит малое.\n\nЧто наполняет тебя? 🌿",
        "✨ {name}, послание света...\n\nТы светишь ярче, чем думаешь. Перестань приглушать себя ради чужого комфорта. Твой свет кому-то очень нужен.\n\nГде ты себя гасишь? 🕯️",
        "✨ {name}, луч на сегодня...\n\nОтпусти контроль хотя бы в одном. Где перестаёшь держать — там и приходит свет и решение. Доверься.\n\nЧто отпустишь? 🌙",
        "✨ {name}, твой свет говорит...\n\nБлагодарность — самый короткий путь к свету. Назови три вещи, за которые тепло прямо сейчас. Настроение развернётся само.\n\nС чего начнёшь? 💛"
    ],
    "oracle": [
        "🎲 {name}, держа вопрос в голове, ты позвала оракула...\n\n✅ ДА. Знаки сложились в твою пользу. Действуй — момент твой.\n\nЧувствуешь подтверждение? ✨",
        "🎲 {name}, оракул услышал твой вопрос...\n\n🚫 НЕТ. Сейчас не время — и это забота, а не отказ. Позже откроется лучший путь.\n\nДоверишься? 🌙",
        "🎲 {name}, карты отвечают на твоё «да или нет»...\n\n⏳ ПОДОЖДИ. Ответ зреет. Дай ситуации несколько дней — и всё прояснится само.\n\nХватит терпения? 🌿",
        "🎲 {name}, оракул говорит...\n\n✅ ДА — но мягко. Иди вперёд без напора. Где идёт легко, там твоя дверь.\n\nГде сейчас легко? 💫",
        "🎲 {name}, твой ответ от оракула...\n\n🚫 НЕТ в эту сторону. Загляни внутрь: возможно, ты хочешь этого из страха, а не из желания.\n\nЧто на самом деле зовёт? 🤍",
        "🎲 {name}, оракул бросил жребий...\n\n✅ ДА. Сердце уже знает ответ — оракул лишь подтверждает. Слушай его.\n\nЧто говорит сердце? ❤️",
        "🎲 {name}, спрашиваешь судьбу...\n\n⏳ ПОКА ТУМАН. Не лучший день для решений. Вернись к вопросу, когда уляжется тревога.\n\nЧто тревожит? 🌫️",
        "🎲 {name}, оракул отвечает...\n\n✅ ДА, и смелее! Ты слишком долго сомневаешься в том, что давно решено. Делай шаг.\n\nГотова? 🔥"
    ]
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

# ─── Настройки бота (settings.json) ───────────────────────────────────────────
# ⚠️ На эфемерном диске хостинга файл стирается при redeploy (как и texts.json).
#    Постоянное хранилище — отдельный этап (см. план, п.3.2).
DEFAULT_SETTINGS = {
    "approval_enabled": True,   # ВКЛ = бот спрашивает одобрение перед публикацией
    "comments_enabled": True,   # ВКЛ = бот отвечает на карта/кофе/руна в комментах
    "post_time": "10:00",
}

def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        for k, v in DEFAULT_SETTINGS.items():
            data.setdefault(k, v)
        return data
    save_settings(dict(DEFAULT_SETTINGS))
    return dict(DEFAULT_SETTINGS)

def save_settings(data: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def get_setting(key: str):
    return load_settings().get(key, DEFAULT_SETTINGS.get(key))

def toggle_setting(key: str) -> bool:
    data = load_settings()
    data[key] = not data.get(key, DEFAULT_SETTINGS.get(key, False))
    save_settings(data)
    return data[key]

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

# ─── Pexels: получить URL фото по запросу ────────────────────────────────────
def fetch_pexels_photo(query: str) -> str | None:
    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        return None
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": api_key},
            params={"query": query, "per_page": 15, "orientation": "portrait"},
            timeout=8,
        )
        photos = resp.json().get("photos", [])
        if photos:
            return random.choice(photos)["src"]["large"]
    except Exception as e:
        logger.warning(f"Pexels error: {e}")
    return None

# ─── Надёжная отправка: Markdown с откатом на простой текст ───────────────────
async def safe_send(bot, chat_id, text, keyboard=None, photo=None) -> None:
    """Отправляет с Markdown; если разметка ломается (напр. `_` в @канале) —
    повторяет без parse_mode, чтобы сообщение всё равно дошло."""
    try:
        if photo is not None:
            await bot.send_photo(chat_id=chat_id, photo=photo, caption=text,
                                 parse_mode="Markdown", reply_markup=keyboard)
        else:
            await bot.send_message(chat_id=chat_id, text=text,
                                   parse_mode="Markdown", reply_markup=keyboard)
    except BadRequest as e:
        if "parse" in str(e).lower() or "entit" in str(e).lower():
            logger.warning(f"Markdown сломан → отправляю без разметки: {e}")
            if photo is not None:
                await bot.send_photo(chat_id=chat_id, photo=photo, caption=text,
                                     reply_markup=keyboard)
            else:
                await bot.send_message(chat_id=chat_id, text=text,
                                       reply_markup=keyboard)
        else:
            raise

def _resolve_photo(post: dict):
    """Возвращает фото для поста: bytes из локального файла, URL или None."""
    photo_path = post.get("photo_path")
    if photo_path and Path(photo_path).exists():
        return Path(photo_path).read_bytes()
    return post.get("photo_url") or (
        fetch_pexels_photo(post["pexels_query"]) if post.get("pexels_query") else None
    )

# ─── Отправка поста в канал ───────────────────────────────────────────────────
async def send_post(bot, channel_id: str, post: dict) -> None:
    title = post.get("title", "")
    text  = post.get("text", "")
    cta   = post.get("cta", "")
    full  = f"*{title}*\n\n{text}\n\n{cta}".strip()

    keyboard = None
    itype = post.get("interactive_type", "")
    if itype == "button_prediction" and post.get("button"):
        btn = post["button"]
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(btn["label"], callback_data=f"pred_{btn['type']}")
        ]])
    elif itype == "button_choice" and post.get("choices"):
        rows = [
            [InlineKeyboardButton(c["label"], callback_data=f"choice_{post.get('id', 0)}_{i}")]
            for i, c in enumerate(post["choices"])
        ]
        keyboard = InlineKeyboardMarkup(rows)

    await safe_send(bot, channel_id, full, keyboard, _resolve_photo(post))

# ─── Одобрение поста: превью → администратору ────────────────────────────────
def approval_keyboard(slot: str) -> InlineKeyboardMarkup:
    """6 кнопок экрана проверки поста (план, раздел 9.6)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Опубликовать",  callback_data=f"approve_{slot}"),
         InlineKeyboardButton("🔄 Другое фото",   callback_data=f"aphoto_{slot}")],
        [InlineKeyboardButton("✍️ Изменить текст", callback_data=f"aedit_{slot}"),
         InlineKeyboardButton("🖼 Своё фото",      callback_data=f"aown_{slot}")],
        [InlineKeyboardButton("⏭ Не сегодня",     callback_data=f"askip_{slot}"),
         InlineKeyboardButton("❌ Отменить",       callback_data=f"cancel_{slot}")],
    ])

def _approval_caption(post: dict) -> str:
    title = post.get("title", "")
    text  = post.get("text", "")
    cta   = post.get("cta", "")
    itype = post.get("interactive_type", "")
    full  = f"*{title}*\n\n{text}\n\n{cta}".strip()
    return (
        f"📋 *Одобри публикацию*\n\n"
        f"{full}\n\n"
        f"Тип: `{itype}` · Канал: {CHANNEL_ID}"
    )[:1020]  # Telegram caption limit 1024

async def send_approval_preview(bot, post: dict, slot: str) -> None:
    """Отправить администратору превью поста с 6 кнопками. Кладёт черновик в pending_posts."""
    pending_posts[slot] = post
    await safe_send(bot, ADMIN_ID, _approval_caption(post), approval_keyboard(slot), _resolve_photo(post))

async def request_approval(app: Application, post: dict, slot: str) -> None:
    """Подготовить пост (подтянуть фото) и показать администратору превью на одобрение."""
    if not ADMIN_ID:
        await send_post(app.bot, CHANNEL_ID, post)
        return

    post = dict(post)  # работаем с копией

    # Получаем фото заранее, чтобы показать в превью и не делать 2 запроса к Pexels
    if not post.get("photo_url") and post.get("pexels_query"):
        photo_url = fetch_pexels_photo(post["pexels_query"])
        if photo_url:
            post["photo_url"] = photo_url

    await send_approval_preview(app.bot, post, slot)
    logger.info(f"📋 Запрос одобрения [{slot}] отправлен администратору {ADMIN_ID}")


# ─── Плановая отправка (APScheduler вызывает эти функции) ────────────────────
async def scheduled_morning_post(app: Application) -> None:
    holiday = get_holiday_post()
    post    = holiday or get_post_for_today("morning")
    if not post:
        today    = datetime.now(tz=MSK).date()
        start    = get_campaign_start()
        delta    = (today - start).days
        week_num = (delta // 7) % 4 + 1
        day_name = DAYS_EN[today.weekday()]
        msg = (
            f"⚠️ Нет поста на сегодня (утро)\n"
            f"Ищу: week={week_num}, day={day_name}\n"
            f"CAMPAIGN_START={CAMPAIGN_START or 'не задан (=сегодня)'}\n\n"
            f"Проверь content.json и CAMPAIGN_START в Railway"
        )
        logger.warning(msg)
        if ADMIN_ID:
            try:
                await app.bot.send_message(chat_id=ADMIN_ID, text=msg)
            except Exception as e:
                logger.error(f"Не смог уведомить админа: {e}")
        return
    if get_setting("approval_enabled"):
        await request_approval(app, post, "morning")
        logger.info("📋 Утренний пост ожидает одобрения")
    else:
        await send_post(app.bot, CHANNEL_ID, post)
        logger.info("Проверка выкл — утренний пост опубликован сразу")

async def scheduled_evening_post(app: Application) -> None:
    post = get_post_for_today("evening")
    if not post:
        return
    if get_setting("approval_enabled"):
        await request_approval(app, post, "evening")
        logger.info("📋 Вечерний пост ожидает одобрения")
    else:
        await send_post(app.bot, CHANNEL_ID, post)
        logger.info("Проверка выкл — вечерний пост опубликован сразу")

# ─── Команды администратора ───────────────────────────────────────────────────
async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправить сегодняшний пост в ТЕСТОВЫЙ канал для проверки."""
    if not is_admin(update):
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
    if not is_admin(update):
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
    if not is_admin(update):
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

async def cmd_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запустить процесс одобрения для сегодняшнего поста (как автопостинг в 10:00)."""
    if not is_admin(update):
        return
    post = get_post_for_today("morning")
    if not post:
        await update.message.reply_text(
            "❌ Нет поста на сегодня в content.json.\nПроверь /status"
        )
        return
    await request_approval(context.application, post, "morning")
    await update.message.reply_text("📋 Превью с кнопками одобрения отправлено выше ☝️")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Диагностика: показать текущий расчёт недели и пост на сегодня."""
    if not is_admin(update):
        return
    today    = datetime.now(tz=MSK)
    start    = get_campaign_start()
    delta    = (today.date() - start).days
    week_num = (delta // 7) % 4 + 1
    day_name = DAYS_EN[today.weekday()]
    post_m   = get_post_for_today("morning")
    post_e   = get_post_for_today("evening")
    now_msk  = today.strftime("%Y-%m-%d %H:%M МСК")
    msg = (
        f"📊 *Статус бота*\n\n"
        f"Время сейчас: `{now_msk}`\n"
        f"CAMPAIGN_START: `{CAMPAIGN_START or 'не задан (=сегодня)'}`\n"
        f"Дата кампании: `{start}`\n"
        f"Дней от старта: `{delta}`\n"
        f"Текущая неделя: `{week_num}` из 4\n"
        f"День недели: `{day_name}`\n\n"
        f"Утренний пост: {'✅ ' + post_m.get('title','?') if post_m else '❌ не найден'}\n"
        f"Вечерний пост: {'✅ ' + post_e.get('title','?') if post_e else '➖ нет'}\n\n"
        f"Каналы:\n  Основной: {CHANNEL_ID}\n  Тест: {TEST_CHANNEL_ID}\n"
        f"Admin ID: {ADMIN_ID}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать план постов на текущую неделю."""
    if not is_admin(update):
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

# ─── Callback: button_choice — выбор карты/варианта → попап с ответом ────────
async def choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    # callback_data формат: choice_<post_id>_<choice_index>
    parts = query.data.split("_")
    try:
        post_id = int(parts[1])
        idx     = int(parts[2])
    except (IndexError, ValueError):
        await query.answer("🔮", show_alert=False)
        return

    posts = load_content()
    post  = next((p for p in posts if p.get("id") == post_id), None)
    if not post:
        await query.answer("🔮 Скоро раскрою...", show_alert=True)
        return

    choices = post.get("choices", [])
    if idx < 0 or idx >= len(choices):
        await query.answer("🔮", show_alert=True)
        return

    answer = choices[idx].get("answer", "🔮")
    await query.answer(answer[:200], show_alert=True)

# ─── Callback: одобрение/отмена публикации поста ────────────────────────────
async def approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not is_admin(update):
        return

    action, slot = query.data.split("_", 1)  # approve_morning → ("approve", "morning")

    async def edit_msg(text: str) -> None:
        try:
            await query.edit_message_caption(text, parse_mode="Markdown")
        except Exception:
            try:
                await query.edit_message_text(text, parse_mode="Markdown")
            except Exception:
                pass

    if action == "approve":
        post = pending_posts.pop(slot, None)
        if not post:
            await edit_msg("❌ Пост уже обработан или не найден")
            return
        await send_post(context.bot, CHANNEL_ID, post)
        await edit_msg(f"✅ *Опубликовано!*\n\n*{post.get('title', '—')}*\nКанал: {CHANNEL_ID}")
        logger.info(f"✅ Пост [{slot}] одобрен → {CHANNEL_ID}")
        return

    if action == "aphoto":  # 🔄 Другое фото — перевыбрать и показать превью заново
        post = pending_posts.get(slot)
        if not post:
            await edit_msg("❌ Пост уже обработан или не найден")
            return
        if post.get("pexels_query"):
            new_photo = fetch_pexels_photo(post["pexels_query"])
            if new_photo:
                post["photo_url"]  = new_photo
                post["photo_path"] = None
        # старое сообщение убираем и шлём новое превью (надёжнее, чем редактировать медиа)
        try:
            await query.delete_message()
        except Exception:
            pass
        await send_approval_preview(context.bot, post, slot)
        return

    if action == "askip":  # ⏭ Не сегодня
        pending_posts.pop(slot, None)
        await edit_msg("⏭ *Пропущено* — сегодня не публикуем.")
        logger.info(f"⏭ Пост [{slot}] пропущен")
        return

    # cancel — ❌ Отменить
    post = pending_posts.pop(slot, None)
    await edit_msg(f"❌ *Отменено*\n\n*{post.get('title', '—') if post else '—'}*")
    logger.info(f"❌ Пост [{slot}] отменён администратором")


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
        [InlineKeyboardButton("📝 Тексты предсказаний", callback_data="menu_texts")],
        [InlineKeyboardButton("🗓 Посты",               callback_data="menu_posts")],
        [InlineKeyboardButton("⚙️ Настройки",           callback_data="menu_settings")],
        [InlineKeyboardButton("📊 Статистика",          callback_data="menu_stats")],
    ])

def texts_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Триггеры (карта/кофе/руна)",        callback_data="menu_triggers")],
        [InlineKeyboardButton("💫 Кнопки постов (любовь/деньги/карты)", callback_data="menu_btns")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
    ])

def triggers_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎴 «карта» — ответы на слово карта", callback_data="menu_karta")],
        [InlineKeyboardButton("☕ «кофе» — ответы на слово кофе",  callback_data="menu_kofe")],
        [InlineKeyboardButton("🌿 «руна» — ответы на слово руна",  callback_data="menu_runa")],
        [InlineKeyboardButton("🔮 «знак» — ответы на слово знак",       callback_data="menu_znak")],
        [InlineKeyboardButton("❤️ «любовь» — ответы на слово любовь",    callback_data="menu_lyubov")],
        [InlineKeyboardButton("💰 «деньги» — ответы на слово деньги",    callback_data="menu_dengi")],
        [InlineKeyboardButton("✨ «свет» — ответы на слово свет",         callback_data="menu_svet")],
        [InlineKeyboardButton("🎲 «да/нет» — оракул",                    callback_data="menu_oracle")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu_texts")],
    ])

def btn_texts_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❤️ Что он чувствует", callback_data="menu_button_love")],
        [InlineKeyboardButton("💰 Денежный совет",   callback_data="menu_button_money")],
        [InlineKeyboardButton("🔮 Карты отвечают",   callback_data="menu_button_cards")],
        [InlineKeyboardButton("◀️ Назад",            callback_data="menu_texts")],
    ])

def posts_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Запланированные посты",   callback_data="posts_scheduled")],
        [InlineKeyboardButton("👀 Предпросмотр следующего", callback_data="posts_preview")],
        [InlineKeyboardButton("🕯 Праздничные посты",       callback_data="posts_holidays")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
    ])

def settings_menu_keyboard() -> InlineKeyboardMarkup:
    s = load_settings()
    approval = "ВКЛ ✅" if s.get("approval_enabled") else "ВЫКЛ ⏸"
    comments = "ВКЛ ✅" if s.get("comments_enabled") else "ВЫКЛ 🔇"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Проверка постов: {approval}", callback_data="toggle_approval")],
        [InlineKeyboardButton(f"💬 Комментарии: {comments}",     callback_data="menu_comments")],
        [InlineKeyboardButton(f"⏰ Время постинга: {s.get('post_time', '10:00')}", callback_data="settings_time")],
        [InlineKeyboardButton("📢 Каналы", callback_data="settings_channels")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
    ])

def comments_menu_keyboard() -> InlineKeyboardMarkup:
    s = load_settings()
    comments = "ВКЛ ✅" if s.get("comments_enabled") else "ВЫКЛ 🔇"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"💬 Отвечать в комментах: {comments}", callback_data="toggle_comments")],
        [InlineKeyboardButton("🎴 Слова: карта, кофе, руна", callback_data="comments_words")],
        [InlineKeyboardButton(f"⏱ Кулдаун: {COOLDOWN_HOURS} ч", callback_data="comments_cooldown")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu_settings")],
    ])

def section_back_keyboard(back_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("◀️ Назад", callback_data=back_cb),
        InlineKeyboardButton("🏠 Меню",  callback_data="back_main"),
    ]])

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

# ─── Тексты экранов разделов ─────────────────────────────────────────────────
def scheduled_posts_text() -> str:
    posts    = load_content()
    today    = datetime.now(tz=MSK).date()
    start    = get_campaign_start()
    week_num = ((today - start).days // 7) % 4 + 1
    week_posts = [p for p in posts if p.get("week") == week_num]
    if not week_posts:
        return f"📋 Нет постов для недели {week_num}.\nВсего в файле: {len(posts)}."
    day_labels = {
        "monday": "ПН", "tuesday": "ВТ", "wednesday": "СР", "thursday": "ЧТ",
        "friday": "ПТ", "saturday": "СБ", "sunday": "ВС",
    }
    lines = [f"📋 *Запланировано на неделю {week_num} из 4:*\n"]
    for p in week_posts:
        d    = day_labels.get(p.get("day", ""), p.get("day", "?"))
        slot = "🌙" if p.get("slot") == "evening" else ""
        lines.append(f"{d} {slot} {p.get('title', '—')}")
    return "\n".join(lines)

def holidays_text() -> str:
    if not HOLIDAYS_FILE.exists():
        return "🕯 Праздничных постов нет (файл holidays.json отсутствует)."
    try:
        hs = json.loads(HOLIDAYS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return "🕯 Не удалось прочитать holidays.json."
    if not hs:
        return "🕯 Список праздников пуст."
    lines = ["🕯 *Праздничные посты:*\n"]
    for h in hs:
        lines.append(f"{h.get('date', '?')} · {h.get('name', '—')}")
    return "\n".join(lines)

def info_screen_text(data: str) -> str:
    s = load_settings()
    if data == "settings_time":
        return (f"⏰ *Время постинга:* {s.get('post_time', '10:00')} МСК\n\n"
                "Изменение времени — следующий этап.")
    if data == "settings_channels":
        return (f"📢 *Каналы:*\nОсновной: {CHANNEL_ID}\nТест: {TEST_CHANNEL_ID}")
    if data == "comments_words":
        return ("🎴 *Слова-триггеры:* карта, кофе, руна\n(+ названия карт и рун).\n\n"
                "Изменение списка — следующий этап.")
    if data == "comments_cooldown":
        return f"⏱ *Кулдаун:* {COOLDOWN_HOURS} ч на человека на каждое слово отдельно."
    return "—"

async def stats_screen(query) -> None:
    today    = datetime.now(tz=MSK)
    start    = get_campaign_start()
    week_num = ((today.date() - start).days // 7) % 4 + 1
    post_m   = get_post_for_today("morning")
    txt = (
        "📊 *Статистика*\n\n"
        f"Неделя: {week_num} из 4\n"
        f"Пост на сегодня: {'✅ ' + post_m.get('title', '?') if post_m else '❌ нет'}\n"
        f"Канал: {CHANNEL_ID}\n\n"
        "Полный отчёт (просмотры/реакции) собирает analytics.py по понедельникам."
    )
    await query.edit_message_text(
        txt, parse_mode="Markdown",
        reply_markup=section_back_keyboard("back_main")
    )

# ─── Главное меню ─────────────────────────────────────────────────────────────
MAIN_MENU_TEXT = (
    "🎛 *Панель управления*\n\n"
    "📝 *Тексты* — что бот отвечает на слова и кнопки\n"
    "🗓 *Посты* — расписание и предпросмотр\n"
    "⚙️ *Настройки* — проверка постов, комментарии\n"
    "📊 *Статистика* — что сейчас\n\n"
    "Выбери раздел:"
)

# Тёплое приветствие для НЕ-админа (новичок из рекламы пишет боту /start)
WELCOME_DM = (
    "🌙 Здравствуй. Это пространство Софии.\n"
    "Я уже почувствовала твоё «здесь» 🤍\n\n"
    "Напиши слово *КАРТА* — и я пришлю тебе послание дня.\n"
    "А если хочешь спросить меня лично — просто напиши, я отвечу 🔮"
)

async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        # Новичок из рекламы → встречаем тепло, НЕ «Нет доступа»
        if update.message:
            await update.message.reply_text(WELCOME_DM, parse_mode="Markdown")
        return
    if update.message:
        await update.message.reply_text(
            MAIN_MENU_TEXT, parse_mode="Markdown", reply_markup=main_menu_keyboard()
        )

# ─── Обработчик кнопок меню (ConversationHandler) ────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if not is_admin(update):
        await query.edit_message_text("❌ Нет доступа.")
        return ConversationHandler.END

    data = query.data

    if data == "back_main":
        await query.edit_message_text(
            MAIN_MENU_TEXT, parse_mode="Markdown", reply_markup=main_menu_keyboard()
        )
        return ConversationHandler.END

    # ── Кнопки экрана проверки поста: правка текста / своё фото (вход в диалог) ──
    if data.startswith("aedit_"):
        slot = data[6:]
        if slot not in pending_posts:
            await query.answer("Пост уже обработан", show_alert=True)
            return ConversationHandler.END
        context.user_data["edit_slot"] = slot
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="✍️ Пришли новый ТЕКСТ поста одним сообщением.\n/cancel — отмена."
        )
        return WAITING_EDIT

    if data.startswith("aown_"):
        slot = data[5:]
        if slot not in pending_posts:
            await query.answer("Пост уже обработан", show_alert=True)
            return ConversationHandler.END
        context.user_data["photo_slot"] = slot
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="🖼 Пришли фото (как фотографию).\n/cancel — отмена."
        )
        return WAITING_PHOTO

    # ── Разделы главного меню ──
    if data == "menu_texts":
        await query.edit_message_text(
            "📝 *Тексты предсказаний*\n\n"
            "💬 Триггеры — ответы на слова в комментах.\n"
            "💫 Кнопки — что бот шлёт в личку по кнопке на посте.\n\n"
            "Выбери раздел:",
            parse_mode="Markdown", reply_markup=texts_menu_keyboard()
        )
        return ConversationHandler.END

    if data == "menu_posts":
        await query.edit_message_text(
            "🗓 *Посты*\n\nУправление публикациями:",
            parse_mode="Markdown", reply_markup=posts_menu_keyboard()
        )
        return ConversationHandler.END

    if data == "menu_settings":
        await query.edit_message_text(
            "⚙️ *Настройки*\n\nНажми на пункт, чтобы переключить:",
            parse_mode="Markdown", reply_markup=settings_menu_keyboard()
        )
        return ConversationHandler.END

    if data == "menu_comments":
        await query.edit_message_text(
            "💬 *Ответы в комментариях*\n\n"
            "Когда подписчик пишет слово (карта/кофе/руна) — бот отвечает предсказанием.",
            parse_mode="Markdown", reply_markup=comments_menu_keyboard()
        )
        return ConversationHandler.END

    if data == "menu_stats":
        await stats_screen(query)
        return ConversationHandler.END

    # ── Переключатели ──
    if data == "toggle_approval":
        state = toggle_setting("approval_enabled")
        note  = ("✅ Проверка ВКЛ — одобряешь каждый пост перед публикацией."
                 if state else "⏸ Проверка ВЫКЛ — бот публикует посты сам, без одобрения.")
        await query.edit_message_text(
            f"⚙️ *Настройки*\n\n{note}",
            parse_mode="Markdown", reply_markup=settings_menu_keyboard()
        )
        return ConversationHandler.END

    if data == "toggle_comments":
        state = toggle_setting("comments_enabled")
        note  = ("✅ Бот отвечает на карта/кофе/руна в комментах."
                 if state else "🔇 Ответы в комментах выключены.")
        await query.edit_message_text(
            f"💬 *Ответы в комментариях*\n\n{note}",
            parse_mode="Markdown", reply_markup=comments_menu_keyboard()
        )
        return ConversationHandler.END

    # ── Посты: подэкраны ──
    if data == "posts_scheduled":
        await query.edit_message_text(
            scheduled_posts_text(), parse_mode="Markdown",
            reply_markup=section_back_keyboard("menu_posts")
        )
        return ConversationHandler.END

    if data == "posts_preview":
        post = get_holiday_post() or get_post_for_today("morning")
        if not post:
            await query.edit_message_text(
                "❌ Нет поста на сегодня в content.json.",
                reply_markup=section_back_keyboard("menu_posts")
            )
        else:
            await request_approval(context.application, post, "morning")
            await query.edit_message_text(
                "👀 Превью с кнопками отправлено отдельным сообщением ☝️",
                reply_markup=section_back_keyboard("menu_posts")
            )
        return ConversationHandler.END

    if data == "posts_holidays":
        await query.edit_message_text(
            holidays_text(), parse_mode="Markdown",
            reply_markup=section_back_keyboard("menu_posts")
        )
        return ConversationHandler.END

    # ── Информационные экраны (правка — следующий этап) ──
    if data in ("settings_time", "settings_channels", "comments_words", "comments_cooldown"):
        back = "menu_comments" if data.startswith("comments_") else "menu_settings"
        await query.edit_message_text(
            info_screen_text(data), parse_mode="Markdown",
            reply_markup=section_back_keyboard(back)
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
    if not is_admin(update):
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
    if not is_admin(update):
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

# ─── Правка поста на одобрении: новый текст ───────────────────────────────────
async def receive_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update):
        return ConversationHandler.END
    slot = context.user_data.get("edit_slot")
    post = pending_posts.get(slot) if slot else None
    if not post:
        if update.message:
            await update.message.reply_text("Пост не найден — открой /preview заново.")
        return ConversationHandler.END
    if not update.message or not update.message.text:
        return WAITING_EDIT
    post["text"] = update.message.text.strip()
    await update.message.reply_text("✍️ Текст обновлён. Вот новое превью:")
    await send_approval_preview(context.bot, post, slot)
    return ConversationHandler.END

# ─── Правка поста на одобрении: своё фото ─────────────────────────────────────
async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update):
        return ConversationHandler.END
    slot = context.user_data.get("photo_slot")
    post = pending_posts.get(slot) if slot else None
    if not post:
        if update.message:
            await update.message.reply_text("Пост не найден — открой /preview заново.")
        return ConversationHandler.END
    if not update.message or not update.message.photo:
        if update.message:
            await update.message.reply_text("Пришли именно фотографию.")
        return WAITING_PHOTO
    # file_id телеграма годится как photo для send_photo
    post["photo_url"]  = update.message.photo[-1].file_id
    post["photo_path"] = None
    await update.message.reply_text("🖼 Фото обновлено. Вот новое превью:")
    await send_approval_preview(context.bot, post, slot)
    return ConversationHandler.END

# ─── Отмена ───────────────────────────────────────────────────────────────────
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("Отменено.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

# ─── Ответ в комментариях ─────────────────────────────────────────────────────
# Автоответ на живой вопрос в личке (в голосе Софии)
AUTO_REPLY_DM = (
    "Спасибо, что написала 🌙 Я обязательно отвечу тебе лично, чуть позже.\n"
    "А пока напиши слово *КАРТА* — и получи послание дня 🔮"
)

async def handle_private(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Личка от обычного человека (не админа). Триггеры карта/кофе/руна →
    послание из пула (как в комментах). Любой другой живой текст → тёплый
    автоответ (раз в сутки на человека) + пинг Софии на ADMIN_ID (на каждое
    сообщение, чтобы она видела все вопросы). Закрывает риск «бот молчит»."""
    msg = update.message
    if not msg or not msg.text:
        return
    user = msg.from_user
    if user is None or is_admin(update):
        return
    text = msg.text.strip()
    name = f"@{user.username}" if user.username else (user.first_name or "Гость")

    # 1) Триггеры карта/кофе/руна работают и в личке
    if   KARTA.match(text):     key = "karta"
    elif KOFE.match(text):      key = "kofe"
    elif RUNA.match(text):      key = "runa"
    elif CARD_NAME.match(text): key = "karta"
    elif ZNAK.match(text):      key = "znak"
    elif LYUBOV.match(text):    key = "lyubov"
    elif DENGI.match(text):     key = "dengi"
    elif SVET.match(text):      key = "svet"
    elif ORACLE.match(text):    key = "oracle"
    else:                       key = None

    if key:
        if is_on_cooldown(user.id, key):
            return
        pool = load_texts().get(key, [])
        if pool:
            cooldowns[(user.id, key)] = datetime.now()
            await msg.reply_text(random.choice(pool).replace("{name}", name))
            logger.info(f"DM-триггер {name} | {key}")
        return

    # 2) Живой вопрос → автоответ (раз в сутки) + пинг Софии (всегда)
    if not is_on_cooldown(user.id, "_dm_auto"):
        cooldowns[(user.id, "_dm_auto")] = datetime.now()
        await safe_send(context.bot, user.id, AUTO_REPLY_DM)
    if ADMIN_ID:
        await safe_send(
            context.bot, ADMIN_ID,
            f"💬 *Тебе написали в личку!*\n"
            f"От: {name} (id `{user.id}`)\n\n"
            f"Текст: {text}"
        )
    logger.info(f"DM-вопрос от {name}: {text[:80]}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not msg.text:
        return
    chat = update.effective_chat
    if chat is None or chat.type == "private":
        return
    user = msg.from_user
    if user is None:
        return
    if not get_setting("comments_enabled"):
        return
    text = msg.text.strip()
    name = f"@{user.username}" if user.username else user.first_name

    if   KARTA.match(text):     key = "karta"
    elif KOFE.match(text):      key = "kofe"
    elif RUNA.match(text):      key = "runa"
    elif CARD_NAME.match(text): key = "karta"  # жрица, солнце, луна и др. → карточный пул
    elif ZNAK.match(text):      key = "znak"
    elif LYUBOV.match(text):    key = "lyubov"
    elif DENGI.match(text):     key = "dengi"
    elif SVET.match(text):      key = "svet"
    elif ORACLE.match(text):    key = "oracle"
    else: return

    if is_on_cooldown(user.id, key):
        return

    pool = load_texts().get(key, [])
    if not pool:
        return

    cooldowns[(user.id, key)] = datetime.now()
    response = random.choice(pool).replace("{name}", name)
    await msg.reply_text(response)
    logger.info(f"Ответил {name} | {key}")

# ─── Глобальный обработчик ошибок ─────────────────────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    # Временные сетевые ошибки (обрыв связи с Telegram, напр. httpx.ConnectError)
    # — пишем только в лог, БЕЗ спама администратору в личку.
    if isinstance(err, NetworkError):
        logger.warning(f"Сетевая ошибка (пропускаю уведомление админу): {err}")
        return
    logger.error("Исключение при обработке апдейта:", exc_info=err)
    if ADMIN_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"⚠️ Ошибка бота:\n{type(err).__name__}: {err}"
            )
        except Exception:
            pass

# ─── Запуск ───────────────────────────────────────────────────────────────────
async def _post_init(app: Application) -> None:
    """Регистрируем список команд → у поля ввода появляется кнопка «Меню»."""
    await app.bot.set_my_commands([
        BotCommand("menu",     "🎛 Панель управления"),
        BotCommand("status",   "📊 Что сейчас (неделя, пост)"),
        BotCommand("preview",  "👀 Пост на одобрение"),
        BotCommand("post",     "✅ Опубликовать сейчас"),
        BotCommand("schedule", "🗓 План недели"),
        BotCommand("test",     "🧪 В тест-канал"),
    ])

def main() -> None:
    if BOT_TOKEN == "ВСТАВЬ_ТОКЕН_ЗДЕСЬ":
        logger.error("Токен не задан! Задай BOT_TOKEN в переменных окружения.")
        return

    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()

    # APScheduler — авто-постинг в 10:00 МСК ежедневно
    scheduler = AsyncIOScheduler(timezone=MSK)
    scheduler.add_job(
        scheduled_morning_post,
        CronTrigger(hour=10, minute=0, timezone=MSK),
        args=[app],
        id="morning_post",
        misfire_grace_time=7200,  # досылать если перезапустился в течение 2 часов
    )
    # Воскресный вечерний пост (ответы на карты)
    scheduler.add_job(
        scheduled_evening_post,
        CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=MSK),
        args=[app],
        id="evening_post",
        misfire_grace_time=7200,
    )
    scheduler.start()
    logger.info("APScheduler запущен ✅ (10:00 МСК ежедневно)")

    # ConversationHandler — управление пулами через кнопки (только для admin)
    # Паттерн ^(?!pred_) — не захватывает кнопки предсказаний с постов
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern=r"^(?!pred_|choice_|approve_|cancel_|aphoto_|askip_)")],
        states={
            WAITING_TEXT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text)],
            WAITING_DELETE_NUM: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_delete_num)],
            WAITING_EDIT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit)],
            WAITING_PHOTO:      [MessageHandler(filters.PHOTO, receive_photo)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    app.add_handler(CommandHandler("menu",     cmd_menu))
    app.add_handler(CommandHandler("start",    cmd_menu))
    app.add_handler(CommandHandler("test",     cmd_test))      # тест-пост сегодня → тест-канал
    app.add_handler(CommandHandler("testpost", cmd_testpost))  # тест конкретного поста по id
    app.add_handler(CommandHandler("post",     cmd_post))      # немедленный пост в основной канал
    app.add_handler(CommandHandler("preview",  cmd_preview))   # пост с одобрением (как автопостинг)
    app.add_handler(CommandHandler("schedule", cmd_schedule))  # план на неделю
    app.add_handler(CommandHandler("status",   cmd_status))    # диагностика
    # Кнопки — перехватываем ДО conv
    app.add_handler(CallbackQueryHandler(approval_callback,   pattern=r"^(approve|cancel|aphoto|askip)_"))
    app.add_handler(CallbackQueryHandler(choice_callback,     pattern=r"^choice_"))
    app.add_handler(CallbackQueryHandler(prediction_callback, pattern=r"^pred_"))
    app.add_handler(conv)
    # Личка обычного человека → автоответ + пинг Софии (ДО общего обработчика)
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_private))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    logger.info("Бот запущен ✅")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
