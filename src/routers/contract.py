"""Export the /v1 slice of the OpenAPI document: `uv run python -m src.routers.contract` rewrites docs/openapi-v1.json."""

import json
from pathlib import Path
from typing import Any

from src.routers.api import Services, create_app

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "docs" / "openapi-v1.json"


def _refs(node: Any) -> set[str]:
    """Names of every component schema referenced anywhere under `node`."""
    if isinstance(node, dict):
        found = {node["$ref"].rsplit("/", 1)[-1]} if "$ref" in node else set()
        return found.union(*(_refs(v) for v in node.values())) if node else found
    return set().union(*(_refs(v) for v in node)) if isinstance(node, list) else set()


def v1_openapi() -> dict[str, Any]:
    """The app's OpenAPI document reduced to /v1 paths and the component schemas they use."""
    full = create_app(Services(embedder=None, llm=None, triad=None)).openapi()
    paths = {p: item for p, item in full["paths"].items() if p.startswith("/v1/")}
    schemas, todo = {}, _refs(paths)
    while todo:
        name = todo.pop()
        if name not in schemas:
            schemas[name] = full["components"]["schemas"][name]
            todo |= _refs(schemas[name])
    components = {"schemas": dict(sorted(schemas.items()))}
    if "securitySchemes" in full.get("components", {}):
        components["securitySchemes"] = full["components"]["securitySchemes"]
    return {"openapi": full["openapi"], "info": full["info"], "paths": paths, "components": components}


def render() -> str:
    """The exact text committed to docs/openapi-v1.json."""
    return json.dumps(v1_openapi(), indent=2) + "\n"


if __name__ == "__main__":
    CONTRACT_PATH.write_text(render(), encoding="utf-8", newline="\n")
