from importlib.resources import files


def asset_text(name: str) -> str:
    return files("ai_test.resources.panel").joinpath(name).read_text(encoding="utf-8")

