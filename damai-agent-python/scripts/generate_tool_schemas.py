"""Generate Python Tool definitions from the canonical Agent OpenAPI contract."""

from __future__ import annotations

import argparse
import copy
import pprint
import sys
from pathlib import Path
from typing import Any, Dict, FrozenSet

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_PATH = REPOSITORY_ROOT / "contracts" / "agent-tools-v1.openapi.yaml"
OUTPUT_PATH = PROJECT_ROOT / "damai_agent" / "generated" / "tool_schemas.py"
REQUIRED_EXTENSIONS = (
    "x-agent-tool-name",
    "x-agent-description",
    "x-agent-risk",
    "x-agent-scope",
    "x-agent-timeout-ms",
)
ALLOWED_RISKS = {"READ_ONLY", "REVERSIBLE_WRITE", "ORDER_WRITE", "PROHIBITED"}


class ContractError(ValueError):
    """Raised when the OpenAPI contract cannot produce safe Tool definitions."""


def _resolve_pointer(document: Dict[str, Any], reference: str) -> Any:
    if not reference.startswith("#/"):
        raise ContractError(f"只支持本地 OpenAPI 引用: {reference}")
    value: Any = document
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            raise ContractError(f"无法解析 OpenAPI 引用: {reference}")
        value = value[part]
    return copy.deepcopy(value)


def _dereference(
    document: Dict[str, Any],
    value: Any,
    seen: FrozenSet[str] = frozenset(),
) -> Any:
    if isinstance(value, list):
        return [_dereference(document, item, seen) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        reference = value["$ref"]
        if not isinstance(reference, str) or reference in seen:
            raise ContractError(f"OpenAPI 引用无效或形成循环: {reference}")
        resolved = _resolve_pointer(document, reference)
        siblings = {key: item for key, item in value.items() if key != "$ref"}
        if siblings:
            if not isinstance(resolved, dict):
                raise ContractError(f"带相邻字段的引用必须指向对象: {reference}")
            resolved.update(siblings)
        return _dereference(document, resolved, seen | {reference})
    return {key: _dereference(document, item, seen) for key, item in value.items()}


def _request_schema(document: Dict[str, Any], operation: Dict[str, Any]) -> Dict[str, Any]:
    request_body = _dereference(document, operation.get("requestBody", {}))
    try:
        schema = request_body["content"]["application/json"]["schema"]
    except (KeyError, TypeError) as error:
        raise ContractError("每个 Agent Tool 都必须声明 application/json requestBody") from error
    resolved = _dereference(document, schema)
    if not isinstance(resolved, dict) or resolved.get("type") != "object":
        raise ContractError("Agent Tool 参数 Schema 必须是 object")
    if resolved.get("additionalProperties") is not False:
        raise ContractError("Agent Tool 参数 Schema 必须设置 additionalProperties=false")
    return resolved


def build_definitions(document: Dict[str, Any]) -> list[Dict[str, Any]]:
    version = str(document.get("info", {}).get("version", "")).strip()
    if not version.startswith("1."):
        raise ContractError("v1 契约的 info.version 必须使用 1.x SemVer")
    if not document.get("security"):
        raise ContractError("Agent Tool 契约必须声明全局 security")

    definitions = []
    names = set()
    paths = document.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise ContractError("OpenAPI paths 不能为空")

    for path in sorted(paths):
        operation = paths[path].get("post") if isinstance(paths[path], dict) else None
        if not isinstance(operation, dict):
            continue
        if not path.startswith("/internal/agent/v1/tools/"):
            raise ContractError(f"Agent Tool 路径必须位于 v1 命名空间: {path}")
        missing = [name for name in REQUIRED_EXTENSIONS if name not in operation]
        if missing:
            raise ContractError(f"{path} 缺少生成元数据: {', '.join(missing)}")

        name = str(operation["x-agent-tool-name"])
        if name in names:
            raise ContractError(f"Agent Tool 名称重复: {name}")
        names.add(name)
        risk = str(operation["x-agent-risk"])
        if risk not in ALLOWED_RISKS:
            raise ContractError(f"{name} 使用未知风险等级: {risk}")
        timeout_ms = operation["x-agent-timeout-ms"]
        if not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise ContractError(f"{name} 的 timeout 必须是正整数毫秒")

        definitions.append(
            {
                "name": name,
                "version": version,
                "description": str(operation["x-agent-description"]),
                "path": path,
                "parameters": _request_schema(document, operation),
                "risk": risk,
                "required_scope": str(operation["x-agent-scope"]),
                "timeout_ms": timeout_ms,
            }
        )
    if not definitions:
        raise ContractError("契约中没有可生成的 POST Agent Tool")
    return definitions


def render(definitions: list[Dict[str, Any]]) -> str:
    rendered = pprint.pformat(definitions, width=100, sort_dicts=False)
    return (
        '"""Generated from contracts/agent-tools-v1.openapi.yaml; do not edit."""\n\n'
        "from __future__ import annotations\n\n"
        "from typing import Any, Dict, List\n\n"
        f"TOOL_DEFINITIONS: List[Dict[str, Any]] = {rendered}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if generated output is stale")
    args = parser.parse_args()

    try:
        document = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ContractError("OpenAPI 根节点必须是对象")
        definitions = build_definitions(document)
        output = render(definitions)
    except (OSError, yaml.YAMLError, ContractError) as error:
        print(f"contract generation failed: {error}", file=sys.stderr)
        return 1

    if args.check:
        if not OUTPUT_PATH.is_file() or OUTPUT_PATH.read_text(encoding="utf-8") != output:
            print(
                "generated Tool Schema is stale; run scripts/generate_tool_schemas.py",
                file=sys.stderr,
            )
            return 1
        print("generated Tool Schema is up to date")
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = OUTPUT_PATH.with_suffix(".py.tmp")
    temporary_path.write_text(output, encoding="utf-8")
    temporary_path.replace(OUTPUT_PATH)
    print(f"generated {len(definitions)} Tool definitions at {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
