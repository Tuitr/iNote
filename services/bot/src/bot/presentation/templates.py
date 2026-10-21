from typing import Final

# Тексты для пользователя — здесь, а не в use case (CLAUDE.md, «Стандарты»)
ECHO: Final = "Вы написали: {text}"
COMMAND: Final = "Команда /{name} получена. Скоро она заработает."
VOICE_NOT_YET: Final = "Голосовые подключу позже, пока напиши текстом."
CALLBACK_ACK: Final = "Нажата кнопка: {payload}"
CALLBACK_ACK_EMPTY: Final = "Кнопка нажата"
GREETING: Final = (
    "Привет! Я помогу с задачами, напоминаниями и встречами. "
    "Пока я учусь — просто напиши что-нибудь, и я повторю."
)
TOO_OFTEN: Final = "Слишком часто. Подожди минуту и попробуй снова."
# Ответ на callback — всплывающее уведомление, длинное MAX обрежет
CALLBACK_NOTIFICATION_LIMIT: Final = 200


# Примечание: русские тексты бота (реализация порта BotTexts)
class RussianTexts:
    # Примечание: эхо — текст пользователя как есть, формат plain: разметка не ломает сообщение
    def echo(self, text: str) -> str:
        return ECHO.format(text=text)

    # Примечание: эхо имени команды
    def command(self, name: str) -> str:
        return COMMAND.format(name=name)

    # Примечание: голосовые до появления ASR (J1)
    def voiceNotSupportedYet(self) -> str:
        return VOICE_NOT_YET

    # Примечание: уведомление на нажатие; payload обрезаем — это служебная строка кнопки
    def callbackAck(self, payload: str | None) -> str:
        if not payload:
            return CALLBACK_ACK_EMPTY
        return CALLBACK_ACK.format(payload=payload)[:CALLBACK_NOTIFICATION_LIMIT]

    # Примечание: приветствие на «Начать»
    def greeting(self) -> str:
        return GREETING

    # Примечание: превышен лимит 20 сообщений в минуту
    def tooOften(self) -> str:
        return TOO_OFTEN
