"""Нагрузка на фейках и память Redis (S4, шаг 3): N пользователей × M апдейтов через /dev/max/*.

Каждый пользователь: текстовые фразы (напоминание, встреча, список, поиск, «перенеси»), голосовые,
нажатия кнопок из ответов и одно уведомление J4. До и после — `INFO memory` и `MEMORY USAGE`
ключей по видам; итог — таблица «байт на активного пользователя» для docs/redis-keys.md.
Лимит бота — 20 сообщений в минуту, поэтому апдейтов на пользователя не больше 20.

    uv run python deploy/redis_load.py [--users 200] [--updates 20]
"""

import argparse
import asyncio
import os
import shlex
import sys
import uuid
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.e2e.stand import (
    Chat,
    buttons,
    itemIdOf,
    notifyRaw,
    push,
    serviceToken,
)

from common.testing.redis_registry import StandRedis

ROOT: Final = Path(__file__).resolve().parents[1]
BOT_URL: Final = os.environ.get("BOT_URL", "http://127.0.0.1:8001")
COMPOSE: Final = (
    f"docker compose -f {ROOT / 'deploy' / 'docker-compose.yml'} "
    f"--env-file {ROOT / '.env'} --profile mock"
)
PHRASES: Final = (
    "Напомни завтра в 10 позвонить Ивану",
    "Что у меня завтра?",
    "Встреча с Петром завтра в 15",
    "найди звонок Ивану",
    "что у меня сегодня",
    "создай задачу купить хлеб",
    "что у меня на неделе",
)
VOICE_EVERY: Final = 7
# из updates апдейтов пользователя столько — нажатия кнопок из его ответов (вторая фаза)
PRESSES: Final = 2
CONCURRENCY: Final = 32
WAIT_S: Final = 600.0


# Примечание: фаза 1 — тексты и голосовые пользователя по очереди (без ожидания ответов: порядок
# у одного пользователя держит диспетчер бота)
async def sendPhrases(client: httpx.AsyncClient, chat: Chat, count: int) -> None:
    for n in range(count):
        phrase = PHRASES[n % len(PHRASES)]
        if n % VOICE_EVERY == VOICE_EVERY - 1:
            body = {"max_user_id": chat.userId, "max_chat_id": chat.chatId, "text": phrase}
            (await client.post("/dev/max/voice", json=body)).raise_for_status()
        else:
            await push(client, chat.text(phrase, mid=f"mid.load.{uuid.uuid4().hex}"))


# Примечание: фаза 2 — нажатия последних callback-кнопок и одно уведомление J4 о записи
async def pressAndNotify(
    client: httpx.AsyncClient, chat: Chat, mine: list[dict[str, object]]
) -> int:
    payloads = [
        button["payload"]
        for message in reversed(mine)
        for row in buttons(message)
        for button in row
        if button["type"] == "callback"
    ][:PRESSES]
    for payload in payloads:
        await push(client, chat.press(payload))
    for message in mine:
        try:
            itemId = itemIdOf(message)
        except AssertionError:
            continue
        remindAt = datetime.now(tz=UTC) + timedelta(hours=1)
        response = await notifyRaw(client, chat, itemId, "Позвонить Ивану", remindAt)
        response.raise_for_status()
        return len(payloads) + 1
    return len(payloads)


# Примечание: сообщения бота по чатам
async def sentByChat(client: httpx.AsyncClient) -> dict[int, list[dict[str, object]]]:
    sent = (await client.get("/dev/max/sent")).json()["messages"]
    byChat: dict[int, list[dict[str, object]]] = {}
    for message in sent:
        byChat.setdefault(int(message["chat_id"]), []).append(message)
    return byChat


# Примечание: ждём, пока у каждого чата не меньше нужного числа сообщений бота
async def waitAll(client: httpx.AsyncClient, expected: dict[int, int]) -> None:
    async with asyncio.timeout(WAIT_S):
        while True:
            byChat = await sentByChat(client)
            if all(len(byChat.get(chat, [])) >= n for chat, n in expected.items()):
                return
            await asyncio.sleep(1)


# Примечание: прогон и таблица; разница — только ключи этого прогона (по INFO и по видам)
async def main(users: int, updates: int) -> None:
    redis = StandRedis(tuple(shlex.split(COMPOSE)))
    before, beforeKinds = redis.memory("used_memory"), redis.usageByKind()
    serviceToken()
    chats = [Chat() for _ in range(users)]
    phrases = updates - PRESSES
    limit = asyncio.Semaphore(CONCURRENCY)

    # Примечание: шаг пользователя под общим пределом одновременности
    async def guarded(step: Awaitable[int | None]) -> int | None:
        async with limit:
            return await step

    async with httpx.AsyncClient(base_url=BOT_URL, timeout=30) as client:
        await asyncio.gather(*(guarded(sendPhrases(client, c, phrases)) for c in chats))
        await waitAll(client, {c.chatId: phrases for c in chats})
        byChat = await sentByChat(client)
        extra = await asyncio.gather(
            *(guarded(pressAndNotify(client, c, byChat[c.chatId])) for c in chats)
        )
        await waitAll(
            client, {c.chatId: phrases + (n or 0) for c, n in zip(chats, extra, strict=True)}
        )
    done = [phrases + (n or 0) for n in extra]
    after, afterKinds = redis.memory("used_memory"), redis.usageByKind()
    print(f"пользователей: {users}, апдейтов и уведомлений: {sum(done)}")  # noqa: T201
    print(f"used_memory: {before} → {after} байт (+{after - before})")  # noqa: T201
    print("| Вид | Ключей за прогон | Байт за прогон | Байт на пользователя |")  # noqa: T201
    print("|---|---|---|---|")  # noqa: T201
    for kind in sorted(afterKinds):
        size = afterKinds[kind][0] - beforeKinds.get(kind, (0, 0))[0]
        count = afterKinds[kind][1] - beforeKinds.get(kind, (0, 0))[1]
        print(f"| `{kind}` | {count} | {size} | {size // users} |")  # noqa: T201
    print(f"| всего (INFO) | | {after - before} | {(after - before) // users} |")  # noqa: T201


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", type=int, default=200)
    parser.add_argument("--updates", type=int, default=20)
    args = parser.parse_args()
    asyncio.run(main(args.users, args.updates))
