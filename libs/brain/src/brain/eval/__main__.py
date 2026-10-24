"""CLI eval: python -m brain.eval run --provider fake|gigachat --cases tests/eval/cases.yaml.

fake — оракул (бесплатно, проверяет раннер и кейсы); gigachat — настоящая модель по LLM_* из
окружения, платно: запускать только по прямой просьбе владельца B (make eval грузит .env).
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from brain.eval.cases import EvalCase, loadCases
from brain.eval.runner import RunRecord, buildReport, oracleBrain, runCases, writeTraces
from brain.factory import openLlmBrain
from brain.settings import LlmSettings
from common.logging import configureLogging
from contracts.agent_side import Brain


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m brain.eval")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="прогнать кейсы и построить отчёт")
    run.add_argument("--provider", choices=["fake", "gigachat"], default="fake")
    run.add_argument("--model", help="модель GigaChat вместо LLM_MODEL")
    run.add_argument("--cases", type=Path, default=Path("tests/eval/cases.yaml"))
    run.add_argument("--repeat", type=int, default=3)
    run.add_argument("--out", type=Path, default=Path(".eval"))
    args = parser.parse_args()
    configureLogging("brain-eval", "WARNING")
    cases = loadCases(args.cases)
    records, modelName = asyncio.run(_collect(cases, args.provider, args.model, args.repeat))
    report = buildReport(records, modelName)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"report-{stamp}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    writeTraces(records, args.out / f"traces-{stamp}.jsonl")
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


# Примечание: прогоны кейсов: оракул или GigaChat (один Brain и HTTP-клиент на весь прогон)
async def _collect(
    cases: Sequence[EvalCase], provider: str, model: str | None, repeat: int
) -> tuple[list[RunRecord], str]:
    if provider == "fake":

        async def makeOracle(case: EvalCase) -> Brain:
            return oracleBrain(case)

        return await runCases(cases, makeOracle, repeat), "oracle"
    settings = LlmSettings()
    if settings.provider != "gigachat":
        # иначе прогон тихо пойдёт на фейке без ответов и покажет 0 % вместо ошибки настройки
        msg = "eval на GigaChat: нужен LLM_PROVIDER=gigachat и LLM_GIGACHAT_CREDENTIALS"
        raise SystemExit(msg)
    if model is not None:
        settings = settings.model_copy(update={"model": model})
    async with openLlmBrain(settings) as brain:

        async def sameBrain(case: EvalCase) -> Brain:
            return brain

        return await runCases(cases, sameBrain, repeat), settings.model


if __name__ == "__main__":
    main()
