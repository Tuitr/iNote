"""Подписанный initData для тестов: так его подписывает MAX."""

import hashlib
import hmac
import json
from datetime import datetime
from urllib.parse import urlencode

BOT_TOKEN = "123456:test-bot-token"
JWT_SECRET = "test-jwt-secret-for-api-tests-only-0123456789"


def signed_init_data(
    max_user_id: int,
    signed_at: datetime,
    start_param: str | None = "today",
    bot_token: str = BOT_TOKEN,
    first_name: str | None = "Степан",
) -> str:
    user: dict[str, object] = {"id": max_user_id}
    if first_name is not None:
        user |= {"first_name": first_name, "last_name": "Иванов"}
    fields = {
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(user, ensure_ascii=False),
        "auth_date": str(int(signed_at.timestamp())),
    }
    if start_param is not None:
        fields["start_param"] = start_param
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)
