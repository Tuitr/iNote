"""Поиск записей по смыслу запроса: полнотекст и триграммы, слияние по месту (развилка 4.5-А).

Каждый источник даёт свой топ, места сводятся формулой RRF: score = Σ 1 / (RRF_K + место).
Так не нужно сравнивать несравнимые оценки разных источников. Третий источник — близость
по смыслу (вектор), если эмбеддер есть и ответил; иначе ищем по двум.
"""

from collections.abc import Hashable, Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.tools import SearchHit, SearchItemsArgs, SearchItemsResult
from libs.core.embedding import Embedder, embed_or_none
from libs.core.repositories.items import ItemRepo, SearchFilter
from libs.core.services.items import require_user, to_item
from libs.core.services.time import local_midnight_utc

# Сколько кандидатов берём из каждого источника до слияния.
CANDIDATES_PER_SOURCE = 20
# Стандартная константа RRF: сглаживает разницу между соседними местами.
RRF_K = 60
# Ниже этого сходства по буквам совпадение случайное («Иван» и «диван»).
MIN_TRIGRAM_SIMILARITY = 0.3
# Уверенное совпадение: лучший результат найден полнотекстом или очень похож по буквам…
STRONG_TRIGRAM_SIMILARITY = 0.5
# …или очень близок по смыслу…
STRONG_VECTOR_SIMILARITY = 0.8
# …и второй заметно хуже первого: его score не больше этой доли от score первого.
CONFIDENT_RUNNER_UP_SHARE = 0.6
# Ниже этого косинусного сходства запись по смыслу не связана с запросом. Пороги вектора
# зависят от модели: подбираем заново, когда будет эмбеддер роли A.
MIN_VECTOR_SIMILARITY = 0.5


class SearchService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], embedder: Embedder | None
    ) -> None:
        self._session_factory = session_factory
        self._embedder = embedder

    async def search(self, args: SearchItemsArgs) -> SearchItemsResult:
        caller = args.caller
        search = SearchFilter(
            kinds=[kind.value for kind in args.kind] if args.kind else None,
            statuses=["open", "done"] if args.include_done else ["open"],
            # Даты пользователя, как в list_items: from включительно, to не включительно.
            start=None if args.from_ is None else local_midnight_utc(args.from_, caller.tz),
            end=None if args.to is None else local_midnight_utc(args.to, caller.tz),
        )
        # Вектор запроса считаем до транзакции, чтобы не держать соединение, пока ждём эмбеддер.
        query_vector = await embed_or_none(self._embedder, args.query, "query")
        async with self._session_factory() as session, session.begin():
            await require_user(session, caller.user_id)
            items = ItemRepo(session)
            fulltext = await items.search_fulltext(
                caller.user_id, args.query, search, CANDIDATES_PER_SOURCE
            )
            trigram = await items.search_trigram(
                caller.user_id, args.query, search, MIN_TRIGRAM_SIMILARITY, CANDIDATES_PER_SOURCE
            )
            vector = (
                []
                if query_vector is None
                else await items.search_vector(
                    caller.user_id,
                    query_vector,
                    search,
                    MIN_VECTOR_SIMILARITY,
                    CANDIDATES_PER_SOURCE,
                )
            )
        rows = (
            {row.id: row for row in fulltext}
            | {row.id: row for row, _ in trigram}
            | {row.id: row for row, _ in vector}
        )
        rankings = [[row.id for row in fulltext], [row.id for row, _ in trigram]]
        if query_vector is not None:
            rankings.append([row.id for row, _ in vector])
        scores = rrf_scores(rankings)
        ranked = sorted(scores, key=lambda item_id: (-scores[item_id], str(item_id)))
        strong = (
            {row.id for row in fulltext}
            | {row.id for row, similarity in trigram if similarity >= STRONG_TRIGRAM_SIMILARITY}
            | {row.id for row, similarity in vector if similarity >= STRONG_VECTOR_SIMILARITY}
        )
        return SearchItemsResult(
            results=[
                SearchHit(item=to_item(rows[item_id], caller.tz), score=round(scores[item_id], 2))
                for item_id in ranked[: args.limit]
            ],
            best_match_confident=_is_confident(ranked, scores, strong),
        )


def rrf_scores[Key: Hashable](rankings: Sequence[Sequence[Key]]) -> dict[Key, float]:
    """Слияние списков по месту (RRF), нормированное в 0–1: 1 — первое место во всех списках."""
    best_possible = len(rankings) / (RRF_K + 1)
    scores: dict[Key, float] = {}
    for ranking in rankings:
        for place, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + 1 / (RRF_K + place) / best_possible
    return scores


def _is_confident(ranked: list[UUID], scores: dict[UUID, float], strong: set[UUID]) -> bool:
    """Агент может сразу брать первый результат, не переспрашивая пользователя."""
    if not ranked or ranked[0] not in strong:
        return False
    if len(ranked) == 1:
        return True
    return scores[ranked[1]] <= CONFIDENT_RUNNER_UP_SHARE * scores[ranked[0]]
