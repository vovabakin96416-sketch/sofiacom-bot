"""
generate_session.py — запусти ОДИН РАЗ локально чтобы получить SESSION_STRING.

После получения строки добавь её в Railway как переменную TELETHON_SESSION.
Этот файл НЕ нужен на сервере.

Запуск:
    python generate_session.py
"""

import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID   = input("Введи TELETHON_API_ID (с my.telegram.org): ").strip()
API_HASH = input("Введи TELETHON_API_HASH: ").strip()


async def main():
    async with TelegramClient(StringSession(), int(API_ID), API_HASH) as client:
        session_string = client.session.save()
        print("\n" + "="*60)
        print("✅ SESSION_STRING получена!\n")
        print(session_string)
        print("="*60)
        print("\nДобавь эту строку в Railway как переменную TELETHON_SESSION")


asyncio.run(main())
