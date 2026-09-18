"""Generate Python Tool definitions from the canonical Agent OpenAPI contract."""

from __future__ import annotations

import argparse
import copy
import keyword
import pprint
import re
import sys
from pathlib import Path
from typing import Any, Dict, FrozenSet, Tuple

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_PATH = REPOSITORY_ROOT / "contracts" / "agent-tools-v1.openapi.yaml"
OUTPUT_PATH = PROJECT_ROOT / "damai_agent" / "generated" / "tool_schemas.py"
MODELS_OUTPUT_PATH = PROJECT_ROOT / "damai_agent" / "generated" / "tool_models.py"
REQUIRED_EXTENSIONS = (
    "x-agent-tool-name",
    "x-agent-description",
    "x-agent-risk",
    "x-agent-scope",
    "x-agent-timeout-ms",
    "x-agent-max-calls-per-turn",
    "x-agent-concurrency-safe",
    "x-agent-exclusive",
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
    schema = _request_schema_node(document, operation)
    resolved = _dereference(document, schema)
    if not isinstance(resolved, dict) or resolved.get("type") != "object":
        raise ContractError("Agent Tool 参数 Schema 必须是 object")
    if resolved.get("additionalProperties") is not False:
        raise ContractError("Agent Tool 参数 Schema 必须设置 additionalProperties=false")
    return resolved


def _resolve_container(document: Dict[str, Any], value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if "$ref" not in value:
        return copy.deepcopy(value)
    reference = value["$ref"]
    if not isinstance(reference, str):
        raise ContractError(f"OpenAPI 引用无效: {reference}")
    resolved = _resolve_pointer(document, reference)
    if not isinstance(resolved, dict):
        raise ContractError(f"OpenAPI 容器引用必须指向对象: {reference}")
    return resolved


def _request_schema_node(document: Dict[str, Any], operation: Dict[str, Any]) -> Dict[str, Any]:
    request_body = _resolve_container(document, operation.get("requestBody", {}))
    try:
        schema = request_body["content"]["application/json"]["schema"]
    except (KeyError, TypeError) as error:
        raise ContractError("每个 Agent Tool 都必须声明 application/json requestBody") from error
    if not isinstance(schema, dict):
        raise ContractError("Agent Tool 请求 Schema 必须是对象")
    return copy.deepcopy(schema)


def _response_schema_node(document: Dict[str, Any], operation: Dict[str, Any]) -> Dict[str, Any]:
    responses = operation.get("responses", {})
    if not isinstance(responses, dict):
        raise ContractError("Agent Tool responses 必须是对象")
    response = _resolve_container(document, responses.get("200", responses.get(200, {})))
    try:
        schema = response["content"]["application/json"]["schema"]
    except (KeyError, TypeError) as error:
        raise ContractError("每个 Agent Tool 都必须声明 200 application/json 响应") from error
    if not isinstance(schema, dict):
        raise ContractError("Agent Tool 响应 Schema 必须是对象")
    return copy.deepcopy(schema)


def _pascal_case(value: str) -> str:
    words = [word for word in re.split(r"[^A-Za-z0-9]+", value) if word]
    if len(words) == 1:
        words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", words[0])
    rendered = "".join(word[:1].upper() + word[1:] for word in words)
    if not rendered or not rendered[0].isalpha():
        raise ContractError(f"无法生成 Python 模型名称: {value}")
    return rendered


def _schema_model_name(schema: Dict[str, Any], fallback: str) -> str:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        return _pascal_case(reference.rsplit("/", 1)[-1])
    return _pascal_case(fallback)


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
        max_calls_per_turn = operation["x-agent-max-calls-per-turn"]
        if not isinstance(max_calls_per_turn, int) or max_calls_per_turn <= 0:
            raise ContractError(f"{name} 的单轮调用上限必须是正整数")
        concurrency_safe = operation["x-agent-concurrency-safe"]
        exclusive = operation["x-agent-exclusive"]
        if not isinstance(concurrency_safe, bool) or not isinstance(exclusive, bool):
            raise ContractError(f"{name} 的并发与独占元数据必须是布尔值")
        if concurrency_safe and exclusive:
            raise ContractError(f"{name} 不能同时声明并发安全和独占执行")
        operation_id = str(operation.get("operationId") or name)
        request_model = _schema_model_name(
            _request_schema_node(document, operation),
            f"{operation_id}Request",
        )
        response_model = _schema_model_name(
            _response_schema_node(document, operation),
            f"{operation_id}Response",
        )

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
                "max_calls_per_turn": max_calls_per_turn,
                "concurrency_safe": concurrency_safe,
                "exclusive": exclusive,
                "request_model": request_model,
                "response_model": response_model,
            }
        )
    if not definitions:
        raise ContractError("契约中没有可生成的 POST Agent Tool")
    return definitions


def render_definitions(definitions: list[Dict[str, Any]]) -> str:
    rendered = pprint.pformat(definitions, width=100, sort_dicts=False)
    return (
        '"""Generated from contracts/agent-tools-v1.openapi.yaml; do not edit."""\n\n'
        "from __future__ import annotations\n\n"
        "from typing import Any, Dict, List\n\n"
        f"TOOL_DEFINITIONS: List[Dict[str, Any]] = {rendered}\n"
    )


def _merge_object_schema(
    document: Dict[str, Any],
    schema: Dict[str, Any],
    seen: FrozenSet[str] = frozenset(),
) -> Dict[str, Any]:
    if "$ref" in schema:
        reference = schema["$ref"]
        if not isinstance(reference, str) or reference in seen:
            raise ContractError(f"OpenAPI 引用无效或形成循环: {reference}")
        resolved = _resolve_pointer(document, reference)
        if not isinstance(resolved, dict):
            raise ContractError(f"模型引用必须指向对象: {reference}")
        siblings = {key: value for key, value in schema.items() if key != "$ref"}
        resolved.update(siblings)
        return _merge_object_schema(document, resolved, seen | {reference})

    merged: Dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": schema.get("additionalProperties", True),
    }
    all_of = schema.get("allOf", [])
    if isinstance(all_of, list):
        for part in all_of:
            if not isinstance(part, dict):
                continue
            flattened = _merge_object_schema(document, part, seen)
            merged["properties"].update(flattened.get("properties", {}))
            merged["required"].extend(flattened.get("required", []))
            if flattened.get("additionalProperties") is False:
                merged["additionalProperties"] = False

    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        merged["properties"].update(copy.deepcopy(properties))
    required = schema.get("required", [])
    if isinstance(required, list):
        merged["required"].extend(str(item) for item in required)
    merged["required"] = list(dict.fromkeys(merged["required"]))
    return merged


def _annotation(schema: Dict[str, Any]) -> str:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        annotation = _pascal_case(reference.rsplit("/", 1)[-1])
    elif isinstance(schema.get("enum"), list) and schema["enum"]:
        annotation = f"Literal[{', '.join(repr(item) for item in schema['enum'])}]"
    else:
        schema_type = schema.get("type")
        if schema_type == "integer":
            annotation = "int"
        elif schema_type == "number":
            annotation = "float"
        elif schema_type == "boolean":
            annotation = "bool"
        elif schema_type == "string" and schema.get("format") == "date-time":
            annotation = "datetime"
        elif schema_type == "string":
            annotation = "str"
        elif schema_type == "array":
            items = schema.get("items", {})
            item_annotation = _annotation(items) if isinstance(items, dict) else "Any"
            annotation = f"List[{item_annotation}]"
        elif schema_type == "object":
            annotation = "Dict[str, Any]"
        else:
            annotation = "Any"
    if schema.get("nullable") is True:
        return f"Optional[{annotation}]"
    return annotation


def _python_field_name(name: str) -> Tuple[str, bool]:
    rendered = re.sub(r"\W", "_", name)
    if not rendered or rendered[0].isdigit():
        rendered = f"field_{rendered}"
    if keyword.iskeyword(rendered):
        rendered = f"{rendered}_"
    return rendered, rendered != name


def _render_field(name: str, schema: Dict[str, Any], required: bool) -> str:
    python_name, needs_alias = _python_field_name(name)
    annotation = _annotation(schema)

    if required:
        default = "..."
    elif "default" in schema:
        default = (
            "cast(Any, None)"
            if schema["default"] is None and not annotation.startswith("Optional[")
            else repr(schema["default"])
        )
    else:
        default = "None" if annotation.startswith("Optional[") else "cast(Any, None)"

    constraints = []
    constraint_names = {
        "minimum": "ge",
        "maximum": "le",
        "minLength": "min_length",
        "maxLength": "max_length",
        "minItems": "min_length",
        "maxItems": "max_length",
        "pattern": "pattern",
    }
    for source_name, target_name in constraint_names.items():
        if source_name in schema:
            constraints.append(f"{target_name}={schema[source_name]!r}")
    if needs_alias:
        constraints.append(f"alias={name!r}")
    description = schema.get("description")
    if isinstance(description, str) and description:
        constraints.append(f"description={description!r}")

    use_field = required or bool(constraints) or (not required and "default" not in schema)
    rendered_default = (
        f"Field({default}, {', '.join(constraints)})"
        if constraints
        else (f"Field({default})" if use_field else default)
    )
    return f"    {python_name}: {annotation} = {rendered_default}"


def build_model_contracts(
    document: Dict[str, Any],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str], Dict[str, str]]:
    components = document.get("components", {})
    component_schemas = components.get("schemas", {}) if isinstance(components, dict) else {}
    if not isinstance(component_schemas, dict):
        raise ContractError("components.schemas 必须是对象")
    schemas: Dict[str, Dict[str, Any]] = {
        _pascal_case(str(name)): copy.deepcopy(schema)
        for name, schema in component_schemas.items()
        if isinstance(schema, dict)
    }
    request_models: Dict[str, str] = {}
    response_models: Dict[str, str] = {}
    paths = document.get("paths", {})
    if not isinstance(paths, dict):
        raise ContractError("OpenAPI paths 必须是对象")
    for path in sorted(paths):
        path_item = paths[path]
        operation = path_item.get("post") if isinstance(path_item, dict) else None
        if not isinstance(operation, dict):
            continue
        tool_name = str(operation["x-agent-tool-name"])
        operation_id = str(operation.get("operationId") or tool_name)
        request_schema = _request_schema_node(document, operation)
        response_schema = _response_schema_node(document, operation)
        request_name = _schema_model_name(request_schema, f"{operation_id}Request")
        response_name = _schema_model_name(response_schema, f"{operation_id}Response")
        if "$ref" not in request_schema:
            schemas[request_name] = request_schema
        if "$ref" not in response_schema:
            schemas[response_name] = response_schema
        request_models[tool_name] = request_name
        response_models[tool_name] = response_name
    return schemas, request_models, response_models


def render_models(
    document: Dict[str, Any],
    schemas: Dict[str, Dict[str, Any]],
    request_models: Dict[str, str],
    response_models: Dict[str, str],
) -> str:
    lines = [
        '"""Generated Pydantic models from contracts/agent-tools-v1.openapi.yaml; do not edit."""',
        "",
        "from __future__ import annotations",
        "",
        "from datetime import datetime",
        "from typing import Any, Dict, List, Literal, Optional, cast",
        "",
        "from pydantic import BaseModel, ConfigDict, Field",
        "",
    ]
    for name, schema in schemas.items():
        flattened = _merge_object_schema(document, schema)
        extra = "forbid" if flattened.get("additionalProperties") is False else "allow"
        lines.extend(
            [
                f"class {name}(BaseModel):",
                f"    model_config = ConfigDict(extra={extra!r}, populate_by_name=True)",
            ]
        )
        properties = flattened.get("properties", {})
        required = set(flattened.get("required", []))
        if isinstance(properties, dict) and properties:
            for field_name, field_schema in properties.items():
                if isinstance(field_schema, dict):
                    lines.append(
                        _render_field(str(field_name), field_schema, field_name in required)
                    )
        else:
            lines.append("    pass")
        lines.append("")

    model_names = list(schemas)
    lines.extend(
        [
            f"GENERATED_MODELS = ({', '.join(model_names)},)",
            "for _model in GENERATED_MODELS:",
            "    _model.model_rebuild()",
            "",
            "REQUEST_MODELS: Dict[str, type[BaseModel]] = {",
        ]
    )
    for tool_name, model_name in request_models.items():
        lines.append(f"    {tool_name!r}: {model_name},")
    lines.extend(["}", "", "RESPONSE_MODELS: Dict[str, type[BaseModel]] = {"])
    for tool_name, model_name in response_models.items():
        lines.append(f"    {tool_name!r}: {model_name},")
    lines.extend(["}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if generated output is stale")
    args = parser.parse_args()

    try:
        document = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ContractError("OpenAPI 根节点必须是对象")
        definitions = build_definitions(document)
        schemas, request_models, response_models = build_model_contracts(document)
        outputs = {
            OUTPUT_PATH: render_definitions(definitions),
            MODELS_OUTPUT_PATH: render_models(
                document,
                schemas,
                request_models,
                response_models,
            ),
        }
    except (OSError, yaml.YAMLError, ContractError) as error:
        print(f"contract generation failed: {error}", file=sys.stderr)
        return 1

    if args.check:
        stale = [
            path
            for path, output in outputs.items()
            if not path.is_file() or path.read_text(encoding="utf-8") != output
        ]
        if stale:
            print(
                "generated Tool contracts are stale: "
                + ", ".join(path.name for path in stale)
                + "; run scripts/generate_tool_schemas.py",
                file=sys.stderr,
            )
            return 1
        print("generated Tool schemas and models are up to date")
        return 0

    for path, output in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(".py.tmp")
        temporary_path.write_text(output, encoding="utf-8")
        temporary_path.replace(path)
    print(f"generated {len(definitions)} Tool definitions and Pydantic contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
