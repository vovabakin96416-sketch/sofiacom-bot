"""
analytics.py — статистика канала через Telethon.

Что делает:
  - Каждый понедельник в 09:30 МСК: отчёт за прошлую неделю
    (просмотры, реакции, комменты по каждому посту)
  - Каждое воскресенье в 21:00 МСК — конец недели 4: предупреждение
    «Пора готовить посты на новый месяц»

Настройка:
  1. Зайди на https://my.telegram.org → Apps → создай приложение
  2. Возьми API_ID и API_HASH
  3. Запусти generate_session.py ОДИН РАЗ локально — получишь SESSION_STRING
  4. Добавь в Railway переменные:
       TELETHON_API_ID, TELETHON_API_HASH, TELETHON_SESSION,
       BOT_TOKEN (уже есть), ADMIN_ID (уже есть), CHANNEL_ID (уже есть)
"""

import os
import logging
from datetime import datetime, timedelta, date

import pytz
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.stats import GetBroadcastStatsRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import asyncio
import requests

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

MSK = pytz.timezone("Europe/Moscow")

API_ID      = int(os.getenv("TELETHON_API_ID", "0"))
API_HASH    = os.getenv("TELETHON_API_HASH", "")
SESSION     = os.getenv("TELETHON_SESSION", "")
BOT_TOKEN   = os.getenv("BOT_TOKEN", "")
ADMIN_ID    = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID  = os.getenv("CHANNEL_ID", "@sofia_gada1ka")

# Дата начала кампании (та же что в bot.py)
CAMPAIGN_START = os.getenv("CAMPAIGN_START", "")


def send_to_admin(text: str) -> None:
    """Отправить сообщение администратору через Bot API (без asyncio)."""
    if not BOT_TOKEN or not ADMIN_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        logger.error(f"send_to_admin error: {e}")


def get_current_week() -> int:
    """Номер текущей недели кампании (1-4)."""
    if not CAMPAIGN_START:
        return 1
    start = date.fromisoformat(CAMPAIGN_START)
    delta = (date.today() - start).days
    return (delta // 7) % 4 + 1


async def weekly_stats_report(client: TelegramClient) -> None:
    """Собрать статистику за прошлую неделю и отправить администратору."""
    logger.info("📊 Собираю статистику за прошлую неделю...")

    try:
        channel = await client.get_entity(CHANNEL_ID)
    except Exception as e:
        logger.error(f"Не могу получить канал: {e}")
        send_to_admin(f"❌ Аналитика: не могу получить канал {CHANNEL_ID}\n{e}")
        return

    # Берём посты за последние 7 дней
    week_ago = datetime.now(tz=MSK) - timedelta(days=7)
    posts = []
    async for msg in client.iter_messages(channel, limit=30):
        if msg.date.astimezone(MSK) < week_ago:
            break
        if msg.text or msg.photo:
            posts.append(msg)

    if not posts:
        send_to_admin("📊 *Аналитика за неделю*\n\nПостов за последние 7 дней не найдено.")
        return

    posts.reverse()  # хронологический порядок

    lines = ["📊 *Аналитика за прошлую неделю*\n"]
    total_views    = 0
    total_reactions = 0
    best_post      = None
    best_views     = 0

    for msg in posts:
        views     = msg.views or 0
        reactions = sum(r.count for r in msg.reactions.results) if msg.reactions else 0
        replies   = msg.replies.replies if msg.replies else 0

        total_views     += views
        total_reactions += reactions

        if views > best_views:
            best_views = views
            best_post  = msg

        # Короткий заголовок поста
        preview = (msg.text or "")[:50].replace("\n", " ").replace("*", "")
        date_str = msg.date.astimezone(MSK).strftime("%d.%m %H:%M")

        lines.append(
            f"📅 {date_str}\n"
            f"👁 {views} · ❤️ {reactions} · 💬 {replies}\n"
            f"_{preview}…_\n"
        )

    # Итоги
    avg_views = total_views // len(posts) if posts else 0
    lines.append(
        f"───────────────\n"
        f"📈 *Итого за неделю:*\n"
        f"Постов: {len(posts)}\n"
        f"Просмотров: {total_views} (среднее: {avg_views})\n"
        f"Реакций: {total_reactions}\n"
    )

    if best_post:
        preview = (best_post.text or "")[:60].replace("\n", " ").replace("*", "")
        lines.append(f"🏆 *Лучший пост:* _{preview}…_\n👁 {best_views} просмотров")

    send_to_admin("\n".join(lines))
    logger.info(f"✅ Отчёт отправлен. Постов: {len(posts)}, просмотров: {total_views}")


async def check_content_ending() -> None:
    """Предупредить администратора что заканчивается последняя неделя контента."""
    week = get_current_week()
    if week == 4:
        send_to_admin(
            "⚠️ *Пора готовить посты на новый месяц!*\n\n"
            "Сейчас идёт *Неделя 4* — последняя в content.json.\n\n"
            "После этой недели цикл начнётся заново с Недели 1.\n\n"
            "Если хочешь добавить новый месяц контента — обнови content.json "
            "и задеплой на Railway."
        )
        logger.info("⚠️ Отправлено предупреждение о конце контента")


async def main() -> None:
    if not all([API_ID, API_HASH, SESSION]):
        logger.error(
            "Telethon не настроен. Нужны переменные окружения:\n"
            "  TELETHON_API_ID, TELETHON_API_HASH, TELETHON_SESSION\n"
            "Запусти generate_session.py локально чтобы получить SESSION."
        )
        return

    client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
    await client.start()
    logger.info("✅ Telethon подключён")

    scheduler = AsyncIOScheduler(timezone=MSK)

    # Еженедельный отчёт — каждый понедельник в 09:30 МСК
    scheduler.add_job(
        weekly_stats_report,
        CronTrigger(day_of_week="mon", hour=9, minute=30, timezone=MSK),
        args=[client],
        id="weekly_report",
    )

    # Предупреждение о конце контента — каждое воскресенье в 21:00 МСК
    scheduler.add_job(
        check_content_ending,
        CronTrigger(day_of_week="sun", hour=21, minute=0, timezone=MSK),
        id="content_check",
    )

    scheduler.start()
    logger.info("APScheduler аналитики запущен ✅")
    logger.info("  - Отчёт: каждый ПН 09:30 МСК")
    logger.info("  - Проверка контента: каждое ВС 21:00 МСК")

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
