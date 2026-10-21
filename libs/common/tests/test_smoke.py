import importlib


# Примечание: пакет установлен в workspace и импортируется
def test_packageImports() -> None:
    module = importlib.import_module("common")
    assert module.__name__ == "common"
