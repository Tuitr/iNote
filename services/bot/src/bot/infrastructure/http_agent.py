"""AgentGateway по HTTP: POST /v1/messages и /v1/actions агента, 45 с, без повторов (стандарт)."""

from pydantic import ValidationError

from common.errors import AppError, ErrorCode
from common.http import ServiceHttpClient
from contracts.reply import ActionRequest, MessageRequest, Reply

MESSAGES_PATH = "/v1/messages"
ACTIONS_PATH = "/v1/actions"


# Примечание: клиент агента; X-Trace-Id и сервисный токен ставит ServiceHttpClient
class HttpAgentGateway:
    # Примечание: http создан в lifespan (правило 9): база — BOT_AGENT_URL, таймаут 45 с, NO_RETRY
    def __init__(self, http: ServiceHttpClient) -> None:
        self._http = http

    # Примечание: текст пользователя → ход агента; POST без ключа не повторяется
    async def sendMessage(self, request: MessageRequest) -> Reply:
        return await self._call(MESSAGES_PATH, request.toWire())

    # Примечание: кнопка или команда → действие без LLM
    async def sendAction(self, request: ActionRequest) -> Reply:
        return await self._call(ACTIONS_PATH, request.toWire())

    # Примечание: ответ не по контракту Reply — internal, бот покажет ошибку с кодом
    async def _call(self, path: str, body: dict[str, object]) -> Reply:
        response = await self._http.post(path, json=body)
        try:
            return Reply.fromJson(response.content)
        except ValidationError as exc:
            raise AppError(ErrorCode.INTERNAL, "агент ответил не по контракту Reply") from exc
