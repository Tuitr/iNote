import importlib


# Примечание: пакет установлен в workspace и импортируется
def test_packageImports() -> None:
    module = importlib.import_module("contracts")
    assert module.__name__ == "contracts"
