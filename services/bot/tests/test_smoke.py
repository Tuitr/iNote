import importlib

import pytest

LAYERS = ["domain", "application", "infrastructure", "presentation", "main"]


# Примечание: каркас слоёв на месте — каждый слой импортируется без побочных эффектов
@pytest.mark.parametrize("layer", LAYERS)
def test_layerImports(layer: str) -> None:
    module = importlib.import_module(f"bot.{layer}")
    assert module.__name__ == f"bot.{layer}"
