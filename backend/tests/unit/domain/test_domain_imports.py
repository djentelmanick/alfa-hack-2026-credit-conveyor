import ast
from pathlib import Path

import pytest

DOMAIN_ROOT = Path(__file__).resolve().parents[3] / "app" / "domain"

FORBIDDEN_PACKAGES = {
    "fastapi",
    "starlette",
    "sqlalchemy",
    "asyncpg",
    "alembic",
    "aioboto3",
    "aiobotocore",
    "botocore",
    "jwt",
    "httpx",
    "yaml",
}
FORBIDDEN_APP_LAYERS = ("app.infra", "app.api", "app.application", "app.main")


def _module_name(path: Path) -> str:
    parts = path.relative_to(DOMAIN_ROOT.parent.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _module_name(path)
    if path.name != "__init__.py":
        package = package.rpartition(".")[0]
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                names.append(f"{base}.{node.module}" if node.module else base)
            elif node.module:
                names.append(node.module)
    return names


DOMAIN_FILES = sorted(DOMAIN_ROOT.rglob("*.py"))


def test_domain_has_modules() -> None:
    assert len(DOMAIN_FILES) > 5


@pytest.mark.parametrize("path", DOMAIN_FILES, ids=lambda p: str(p.relative_to(DOMAIN_ROOT)))
def test_domain_does_not_import_frameworks_or_outer_layers(path: Path) -> None:
    for name in _imports(path):
        top = name.split(".")[0]
        assert top not in FORBIDDEN_PACKAGES, f"{path.name} импортирует {name}"
        assert not name.startswith(FORBIDDEN_APP_LAYERS), f"{path.name} импортирует {name}"
