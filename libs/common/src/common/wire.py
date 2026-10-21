from typing import Self

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_snake


# Примечание: база всех моделей «на проводе»: в коде camelCase, в JSON всегда snake_case
class WireModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_snake,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        # Правило 8 CLAUDE.md: читатель игнорирует незнакомые поля
        extra="ignore",
        frozen=True,
    )

    # Примечание: словарь для JSON-ответа — даты строками ISO, ключи snake_case
    def toWire(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True)

    # Примечание: готовая JSON-строка в snake_case
    def toJson(self) -> str:
        return self.model_dump_json(by_alias=True)

    # Примечание: разбор внешнего JSON на границе — дальше в коде только модель
    @classmethod
    def fromWire(cls, data: object) -> Self:
        return cls.model_validate(data)

    # Примечание: разбор сырой JSON-строки или байтов
    @classmethod
    def fromJson(cls, raw: str | bytes) -> Self:
        return cls.model_validate_json(raw)
