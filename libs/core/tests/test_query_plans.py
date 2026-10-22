"""Основные запросы на 50 000 записей идут по индексам, а не перебором таблицы (шаг 31 S4).

Планы и время — в docs/d-explain.md. Тест держит то, что там записано: появится новый запрос
без индекса или пропадёт индекс — тест покраснеет.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.core.tests.query_plans import collect_plans, generate_data

pytestmark = pytest.mark.anyio

# Запросы одного пользователя: перебирать ради них все записи всех пользователей нельзя.
USER_SCOPED = {
    "list_items / GET /v1/items за неделю",
    "search: полнотекст",
    "search: триграммы",
    "запись по короткому id (8 символов), изменение",
    "запись по короткому id, удалённые тоже (delete_item)",
    "карточка по полному id (GET /v1/items/{id})",
    "повтор вызова: idempotency_key",
    "конфликты встреч",
    "уведомления записи: отмена при изменении",
}
EXPECTED_INDEX = {
    "история диалога с курсором": "ix_dialog_log_user_id_id",
    "Scheduler: claim_due": "ix_notifications_fire_at",
    "Scheduler: cancel_stale": "ix_notifications_fire_at",
    "Scheduler: reclaim_expired": "ix_notifications_lease_until",
    "Scheduler: schedule_next (прошедшие серии)": "ix_items_open_series",
}


async def test_main_queries_use_indexes(session_factory: async_sessionmaker[AsyncSession]) -> None:
    await generate_data(session_factory)

    plans = {plan.name: plan.plan for plan in await collect_plans(session_factory)}

    assert USER_SCOPED | EXPECTED_INDEX.keys() <= plans.keys()
    full_scans = [name for name in USER_SCOPED if "Seq Scan on items" in plans[name]]
    missing = [name for name, index in EXPECTED_INDEX.items() if index not in plans[name]]
    assert full_scans == []
    assert missing == []
