"""Запросы к таблице dialog_log."""

from collections.abc import Sequence

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from libs.contracts.internal import DialogTurn
from libs.core.db.models import DialogLogRow


class DialogRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_turns(
        self, user_id: int, trace_id: str, turns: Sequence[DialogTurn], latency_ms: int | None
    ) -> None:
        """Реплики хода по порядку (turn_no с 1). Уже записанные с этим trace_id пропускаются."""
        rows = [
            {
                "user_id": user_id,
                "trace_id": trace_id,
                "turn_no": turn_no,
                "role": turn.role,
                "text": turn.text,
                "input_type": turn.input_type,
                "item_ids": turn.item_ids,
                "latency_ms": latency_ms,
            }
            for turn_no, turn in enumerate(turns, start=1)
        ]
        statement = (
            insert(DialogLogRow)
            .values(rows)
            .on_conflict_do_nothing(index_elements=[DialogLogRow.trace_id, DialogLogRow.turn_no])
        )
        await self._session.execute(statement)
