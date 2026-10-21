"""Contract-тесты: JSON-схемы моделей стыков и примеры из contracts/examples под снапшотами.

Снапшот меняется только при намеренной смене контракта:
`uv run pytest -m contract --snapshot-update` — вместе с contracts/examples
и по правилу изменения контракта (CLAUDE.md).
"""

import pytest
from pydantic import BaseModel
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.json import JSONSnapshotExtension

from contracts.agent_side import TurnInput, TurnResult
from contracts.notify import NotifyDelivered, NotifyRequest
from contracts.reply import ActionRequest, MessageRequest, Reply
from contracts.users import ResolveUserRequest, ResolveUserResponse
from libs.contracts.tests.examples import EXAMPLES_DIR, loadExample

pytestmark = pytest.mark.contract

MODELS: list[type[BaseModel]] = [
    Reply,
    MessageRequest,
    ActionRequest,
    NotifyRequest,
    NotifyDelivered,
    TurnInput,
    TurnResult,
    ResolveUserRequest,
    ResolveUserResponse,
]


# Примечание: снапшоты в JSON — читаемый дифф в PR
@pytest.fixture
def jsonSnapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    return snapshot.use_extension(JSONSnapshotExtension)


# Примечание: схема модели «на проводе» (by_alias — snake_case) не меняется незаметно
@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_schema(model: type[BaseModel], jsonSnapshot: SnapshotAssertion) -> None:
    assert model.model_json_schema(by_alias=True, mode="serialization") == jsonSnapshot


# Примечание: каталог примеров: какой файл есть — тот и под снапшотом, потеря примера видна
def test_examplesCatalog(jsonSnapshot: SnapshotAssertion) -> None:
    files = sorted(str(p.relative_to(EXAMPLES_DIR)) for p in EXAMPLES_DIR.rglob("*.json"))
    assert {name: loadExample(name) for name in files} == jsonSnapshot
