# Апдейты MAX для тестов

**Синтетика.** Доступа к API MAX пока нет, поэтому апдейты собраны по официальной OpenAPI-схеме
([max-messenger/api-schema](https://github.com/max-messenger/api-schema), `schema.yaml` 0.0.33, коммит `1a4a502`).
В каждом файле есть `"_synthetic": true` — **заменить живыми апдейтами, как только будет токен.**

| Файл | `update_type` | Что проверяем |
|---|---|---|
| `text.json` | `message_created` | текст в личном диалоге |
| `command.json` | `message_created` | текст с `/` — команда |
| `voice.json` | `message_created` + вложение `audio` | голосовое (см. TODO) |
| `callback.json` | `message_callback` | нажатие inline-кнопки |
| `bot_started.json` | `bot_started` | «Начать», `payload` из deep link |

Чего нет в схеме и что нужно подтвердить на живых апдейтах:

- **`update_id`.** У апдейта MAX его нет, только `timestamp`. Бот строит id сам: `mid` сообщения, `callback_id` плюс время нажатия, чат плюс время старта (`bot.infrastructure.max_mapper.deriveUpdateId`).
- **Голосовое.** TODO: подтвердить, что оно приходит как вложение `type: "audio"`. В `payload` есть только `url` и `token`, **длительности и mime нет**.
- **`timestamp`.** В миллисекундах: так сказано в схеме для `last_activity_time`, для самого апдейта единицы не указаны.
