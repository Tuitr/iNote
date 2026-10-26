"""Вход мини-приложения: проверка подписи initData из MAX и выдача JWT (J5).

Подпись: секрет = HMAC-SHA256(ключ "WebAppData", сообщение — токен
бота); подпись — HMAC-SHA256(секрет, пары key=value без hash, отсортированные по ключу и
соединённые "\\n"), в hex. Строку разбираем как есть: если фронт её пересоберёт, подпись сломается.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl

import jwt

# Старый initData мог утечь по ссылке: документация MAX советует срок около часа.
INIT_DATA_MAX_AGE = timedelta(hours=1)
TOKEN_TTL = timedelta(hours=1)
JWT_ALGORITHM = "HS256"


class AuthError(Exception):
    """initData или токен не прошли проверку: ответ 401 unauthorized."""


@dataclass(frozen=True)
class MaxLogin:
    """Кто вошёл в мини-приложение, по данным MAX."""

    max_user_id: int
    name: str
    start_param: str | None


def read_init_data(
    init_data: str, bot_token: str, now: datetime, check_signature: bool
) -> MaxLogin:
    """Разбор initData. check_signature=False — только dev-авторизация на машине разработчика."""
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError as error:
        raise AuthError("initData не разобрать") from error
    if check_signature:
        _check_signature(fields, bot_token)
        _check_age(fields, now)
    return _login(fields)


def issue_token(max_user_id: int, secret: str, now: datetime) -> tuple[str, datetime]:
    """JWT на TOKEN_TTL и момент, когда он истечёт.

    В sub — max_user_id, а не внутренний user_id: содержимое JWT фронт может прочитать,
    а внутренний user_id наружу не отдаём.
    """
    # Целые секунды: в JWT время хранится в секундах, ответ должен с ним совпадать.
    expires_at = (now + TOKEN_TTL).replace(microsecond=0)
    payload = {"sub": str(max_user_id), "iat": now, "exp": expires_at}
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM), expires_at


def read_token(token: str, secret: str, now: datetime) -> int:
    """max_user_id из действующего токена; подделанный или истёкший → AuthError."""
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp"], "verify_exp": False},
        )
        max_user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError) as error:
        raise AuthError("Токен не прошёл проверку") from error
    # Срок проверяем сами, по переданному «сейчас»: так время в тестах можно заморозить.
    if datetime.fromtimestamp(payload["exp"], UTC) <= now:
        raise AuthError("Токен истёк")
    return max_user_id


def _check_signature(fields: dict[str, str], bot_token: str) -> None:
    received = fields.get("hash", "")
    pairs = sorted((key, value) for key, value in fields.items() if key != "hash")
    data_check_string = "\n".join(f"{key}={value}" for key, value in pairs)
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received.encode(), expected.encode()):
        raise AuthError("Подпись initData не совпала")


def _check_age(fields: dict[str, str], now: datetime) -> None:
    try:
        signed_at = datetime.fromtimestamp(int(fields["auth_date"]), UTC)
    except (KeyError, ValueError) as error:
        raise AuthError("В initData нет auth_date") from error
    if now - signed_at > INIT_DATA_MAX_AGE:
        raise AuthError("initData устарел")


def _login(fields: dict[str, str]) -> MaxLogin:
    try:
        user = json.loads(fields["user"])
        max_user_id = int(user["id"])
    except (KeyError, ValueError, TypeError) as error:
        raise AuthError("В initData нет пользователя") from error
    name = " ".join(part for part in (user.get("first_name"), user.get("last_name")) if part)
    return MaxLogin(
        max_user_id=max_user_id, name=name, start_param=fields.get("start_param") or None
    )
