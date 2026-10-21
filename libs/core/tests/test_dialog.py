import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.internal import DialogTurnsRequest
from libs.core.db.models import DialogLogRow
from libs.core.errors import NotFoundError
from libs.core.services.dialog import DialogService

pytestmark = pytest.mark.anyio

ITEM_ID = "3c9e1a7b-2d4f-4b6a-8c1e-5f7a9b0d2e4c"


def _turns(user_id: int, trace_id: str = "ab12f0c4d5e6f708192a3b4c5d6e7f80") -> DialogTurnsRequest:
    return DialogTurnsRequest.model_validate(
        {
            "user_id": user_id,
            "trace_id": trace_id,
            "turns": [
                {
                    "role": "user",
                    "text": "Напомни завтра в 10 позвонить Ивану",
                    "input_type": "voice",
                },
                {"role": "assistant", "text": "Напомню завтра в 10:00.", "item_ids": [ITEM_ID]},
            ],
            "latency_ms": 2930,
        }
    )


async def _rows(session_factory: async_sessionmaker[AsyncSession]) -> list[DialogLogRow]:
    async with session_factory() as session:
        return list(await session.scalars(select(DialogLogRow).order_by(DialogLogRow.id)))


async def test_turns_are_saved_in_order(
    session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    await DialogService(session_factory).record_turns(_turns(user_id))

    rows = await _rows(session_factory)
    assert [(row.turn_no, row.role, row.input_type, row.latency_ms) for row in rows] == [
        (1, "user", "voice", 2930),
        (2, "assistant", None, 2930),
    ]
    assert [str(item_id) for item_id in rows[1].item_ids] == [ITEM_ID]


async def test_repeat_with_same_trace_id_adds_nothing(
    session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    service = DialogService(session_factory)

    await service.record_turns(_turns(user_id))
    await service.record_turns(_turns(user_id))
    await service.record_turns(_turns(user_id, trace_id="cd34" * 8))

    assert len(await _rows(session_factory)) == 4


async def test_unknown_user_is_not_found(
    session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    with pytest.raises(NotFoundError):
        await DialogService(session_factory).record_turns(_turns(user_id + 1))
