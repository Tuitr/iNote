"""Подписанный initData для тестов: так его подписывает MAX (план D, «Проверка initData»)."""

import hashlib
import hmac
import json
from datetime import datetime
from urllib.parse import urlencode

BOT_TOKEN = "123456:test-bot-token"  # noqa: S105 — токен только для тестов


def signed_init_data(
    max_user_id: int,
    signed_at: datetime,
    start_param: str | None = "today",
    bot_token: str = BOT_TOKEN,
) -> str:
    fields = {
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(
            {"id": max_user_id, "first_name": "Степан", "last_name": "Иванов"}, ensure_ascii=False
        ),
        "auth_date": str(int(signed_at.timestamp())),
    }
    if start_param is not None:
        fields["start_param"] = start_param
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)
