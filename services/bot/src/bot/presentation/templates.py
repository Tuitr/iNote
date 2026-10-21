from typing import Final

# Тексты бота для пользователя — здесь, а не в use case (CLAUDE.md, «Стандарты»).
# Ответы по существу (подтверждения, списки) строит агент по своим шаблонам.
VOICE_NOT_YET: Final = "Голосовые подключу позже, пока напиши текстом."
GREETING: Final = (
    "Привет! Я помогу с задачами, напоминаниями и встречами. Напиши, например: "
    "«что у меня сегодня» или «напомни завтра в 10 позвонить Ивану»."
)
HELP: Final = (
    "Пиши обычным текстом: «что у меня завтра», «создай задачу купить хлеб», «найди договор».\n"
    "Команды: /today — сегодня, /tomorrow — завтра, /week — неделя."
)
UNKNOWN_COMMAND: Final = "Не знаю команду /{name}. Есть /today, /tomorrow, /week и /help."
TOO_OFTEN: Final = "Слишком часто. Подожди минуту и попробуй снова."
AGENT_FAILED: Final = (
    "Не получилось связаться с ассистентом. Попробуй ещё раз через минуту.\nКод: {code}"
)
BUTTON_EXPIRED: Final = "Кнопка устарела — повтори запрос."
# Ответ на callback — всплывающее уведомление, длинное MAX обрежет
CALLBACK_NOTIFICATION_LIMIT: Final = 200
COMMAND_NAME_LIMIT: Final = 32


# Примечание: русские тексты бота (реализация порта BotTexts)
class RussianTexts:
    # Примечание: голосовые до появления ASR (J1)
    def voiceNotSupportedYet(self) -> str:
        return VOICE_NOT_YET

    # Примечание: приветствие на «Начать»
    def greeting(self) -> str:
        return GREETING

    # Примечание: превышен лимит 20 сообщений в минуту
    def tooOften(self) -> str:
        return TOO_OFTEN

    # Примечание: агент не ответил или ответил ошибкой
    def agentFailed(self, code: str) -> str:
        return AGENT_FAILED.format(code=code)

    # Примечание: ключа кнопки уже нет в Redis (24 ч) — уведомление на нажатие
    def buttonExpired(self) -> str:
        return BUTTON_EXPIRED[:CALLBACK_NOTIFICATION_LIMIT]

    # Примечание: имя команды обрезаем — это текст пользователя
    def unknownCommand(self, name: str) -> str:
        return UNKNOWN_COMMAND.format(name=name[:COMMAND_NAME_LIMIT])

    # Примечание: справка на /start и /help
    def help(self) -> str:
        return HELP
