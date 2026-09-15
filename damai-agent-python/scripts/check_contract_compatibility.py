"""Reject breaking changes to the frozen v1 Agent Tool OpenAPI contract."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Tuple

import yaml

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
CONTRACT_ROOT = Path(__file__).resolve().parents[2] / "contracts"
DEFAULT_BASELINE = CONTRACT_ROOT / "baselines" / "agent-tools-v1.0.0.openapi.yaml"
DEFAULT_CANDIDATE = CONTRACT_ROOT / "agent-tools-v1.openapi.yaml"

Document = Dict[str, Any]


def load_document(path: Path) -> Document:
    with path.open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict):
        raise ValueError(f"OpenAPI document must be an object: {path}")
    return document


def _resolve(document: Document, value: Any) -> Any:
    seen: set[str] = set()
    while isinstance(value, dict) and "$ref" in value:
        reference = value["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise ValueError(f"Only local OpenAPI references are supported: {reference}")
        if reference in seen:
            raise ValueError(f"Circular OpenAPI reference: {reference}")
        seen.add(reference)
        resolved: Any = document
        for token in reference[2:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            resolved = resolved[token]
        value = resolved
    return value


def _merge_schema(target: MutableMapping[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if key == "required":
            target[key] = sorted(set(target.get(key, [])) | set(value))
        elif key == "properties":
            properties = dict(target.get(key, {}))
            properties.update(value)
            target[key] = properties
        elif key != "allOf":
            target[key] = value


def _materialize_schema(document: Document, value: Any) -> Document:
    resolved = _resolve(document, value)
    if not isinstance(resolved, dict):
        return {}
    result: Document = {}
    for part in resolved.get("allOf", []):
        _merge_schema(result, _materialize_schema(document, part))
    _merge_schema(result, resolved)
    return result


def _schema_type(schema: Mapping[str, Any]) -> Any:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return tuple(sorted(schema_type))
    return schema_type


def _compare_input_schema(
    baseline_document: Document,
    candidate_document: Document,
    baseline_value: Any,
    candidate_value: Any,
    location: str,
    errors: List[str],
) -> None:
    baseline = _materialize_schema(baseline_document, baseline_value)
    candidate = _materialize_schema(candidate_document, candidate_value)

    if _schema_type(baseline) != _schema_type(candidate):
        errors.append(f"{location}: input type changed")
        return
    if baseline.get("nullable", False) and not candidate.get("nullable", False):
        errors.append(f"{location}: input no longer accepts null")

    baseline_enum = set(baseline.get("enum", []))
    candidate_enum = set(candidate.get("enum", []))
    if baseline_enum and (not candidate_enum or not baseline_enum.issubset(candidate_enum)):
        errors.append(f"{location}: accepted enum values were removed")

    lower_bounds = ("minimum", "exclusiveMinimum", "minLength", "minItems")
    upper_bounds = ("maximum", "exclusiveMaximum", "maxLength", "maxItems")
    for constraint in lower_bounds:
        old = baseline.get(constraint)
        new = candidate.get(constraint)
        if new is not None and (old is None or new > old):
            errors.append(f"{location}: {constraint} became stricter")
    for constraint in upper_bounds:
        old = baseline.get(constraint)
        new = candidate.get(constraint)
        if new is not None and (old is None or new < old):
            errors.append(f"{location}: {constraint} became stricter")

    if baseline.get("additionalProperties", True) is not False and (
        candidate.get("additionalProperties", True) is False
    ):
        errors.append(f"{location}: additional properties are no longer accepted")

    baseline_required = set(baseline.get("required", []))
    candidate_required = set(candidate.get("required", []))
    for name in sorted(candidate_required - baseline_required):
        errors.append(f"{location}.{name}: new required input property")

    baseline_properties = baseline.get("properties", {})
    candidate_properties = candidate.get("properties", {})
    for name, baseline_property in baseline_properties.items():
        if name not in candidate_properties:
            errors.append(f"{location}.{name}: accepted input property was removed")
            continue
        _compare_input_schema(
            baseline_document,
            candidate_document,
            baseline_property,
            candidate_properties[name],
            f"{location}.{name}",
            errors,
        )


def _compare_output_schema(
    baseline_document: Document,
    candidate_document: Document,
    baseline_value: Any,
    candidate_value: Any,
    location: str,
    errors: List[str],
) -> None:
    baseline = _materialize_schema(baseline_document, baseline_value)
    candidate = _materialize_schema(candidate_document, candidate_value)

    if _schema_type(baseline) != _schema_type(candidate):
        errors.append(f"{location}: output type changed")
        return
    if not baseline.get("nullable", False) and candidate.get("nullable", False):
        errors.append(f"{location}: output may now be null")

    baseline_required = set(baseline.get("required", []))
    candidate_required = set(candidate.get("required", []))
    for name in sorted(baseline_required - candidate_required):
        errors.append(f"{location}.{name}: required output guarantee was removed")

    baseline_properties = baseline.get("properties", {})
    candidate_properties = candidate.get("properties", {})
    for name, baseline_property in baseline_properties.items():
        if name not in candidate_properties:
            errors.append(f"{location}.{name}: output property was removed")
            continue
        _compare_output_schema(
            baseline_document,
            candidate_document,
            baseline_property,
            candidate_properties[name],
            f"{location}.{name}",
            errors,
        )

    baseline_items = baseline.get("items")
    candidate_items = candidate.get("items")
    if baseline_items is not None:
        if candidate_items is None:
            errors.append(f"{location}: output array item schema was removed")
        else:
            _compare_output_schema(
                baseline_document,
                candidate_document,
                baseline_items,
                candidate_items,
                f"{location}[]",
                errors,
            )


def _parameters(
    document: Document, operation: Mapping[str, Any]
) -> Dict[Tuple[str, str], Document]:
    result: Dict[Tuple[str, str], Document] = {}
    for value in operation.get("parameters", []):
        parameter = _resolve(document, value)
        if isinstance(parameter, dict):
            result[(str(parameter.get("in")), str(parameter.get("name")).lower())] = parameter
    return result


def _operations(document: Document) -> Iterable[Tuple[str, str, Document]]:
    for path, path_item in document.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method in HTTP_METHODS and isinstance(operation, dict):
                yield path, method, operation


def check_compatibility(baseline: Document, candidate: Document) -> List[str]:
    errors: List[str] = []
    if baseline.get("security") and not candidate.get("security"):
        errors.append("root security requirement was removed")

    candidate_operations = {
        (path, method): operation for path, method, operation in _operations(candidate)
    }
    for path, method, baseline_operation in _operations(baseline):
        location = f"{method.upper()} {path}"
        candidate_operation = candidate_operations.get((path, method))
        if candidate_operation is None:
            errors.append(f"{location}: operation was removed")
            continue

        baseline_security = baseline_operation.get("security", baseline.get("security"))
        candidate_security = candidate_operation.get("security", candidate.get("security"))
        if baseline_security and not candidate_security:
            errors.append(f"{location}: security requirement was removed")

        for extension in ("x-agent-tool-name", "x-agent-risk", "x-agent-scope"):
            if baseline_operation.get(extension) != candidate_operation.get(extension):
                errors.append(f"{location}: {extension} changed")

        baseline_parameters = _parameters(baseline, baseline_operation)
        candidate_parameters = _parameters(candidate, candidate_operation)
        for key, baseline_parameter in baseline_parameters.items():
            parameter_location = f"{location} parameter {key[1]}"
            candidate_parameter = candidate_parameters.get(key)
            if candidate_parameter is None:
                errors.append(f"{parameter_location}: parameter was removed")
                continue
            if not baseline_parameter.get("required", False) and candidate_parameter.get(
                "required", False
            ):
                errors.append(f"{parameter_location}: parameter became required")
            _compare_input_schema(
                baseline,
                candidate,
                baseline_parameter.get("schema", {}),
                candidate_parameter.get("schema", {}),
                parameter_location,
                errors,
            )
        for key, candidate_parameter in candidate_parameters.items():
            if key not in baseline_parameters and candidate_parameter.get("required", False):
                errors.append(f"{location} parameter {key[1]}: new required parameter")

        baseline_body = baseline_operation.get("requestBody")
        candidate_body = candidate_operation.get("requestBody")
        if baseline_body is not None:
            if candidate_body is None:
                errors.append(f"{location}: request body was removed")
            else:
                baseline_body = _resolve(baseline, baseline_body)
                candidate_body = _resolve(candidate, candidate_body)
                if not baseline_body.get("required", False) and candidate_body.get(
                    "required", False
                ):
                    errors.append(f"{location}: request body became required")
                baseline_content = baseline_body.get("content", {})
                candidate_content = candidate_body.get("content", {})
                for media_type, baseline_media in baseline_content.items():
                    if media_type not in candidate_content:
                        errors.append(f"{location}: request media type {media_type} was removed")
                        continue
                    _compare_input_schema(
                        baseline,
                        candidate,
                        baseline_media.get("schema", {}),
                        candidate_content[media_type].get("schema", {}),
                        f"{location} request",
                        errors,
                    )

        baseline_responses = baseline_operation.get("responses", {})
        candidate_responses = candidate_operation.get("responses", {})
        for status, baseline_response_value in baseline_responses.items():
            if status not in candidate_responses:
                errors.append(f"{location}: response {status} was removed")
                continue
            baseline_response = _resolve(baseline, baseline_response_value)
            candidate_response = _resolve(candidate, candidate_responses[status])
            baseline_content = baseline_response.get("content", {})
            candidate_content = candidate_response.get("content", {})
            for media_type, baseline_media in baseline_content.items():
                if media_type not in candidate_content:
                    errors.append(
                        f"{location}: response {status} media type {media_type} was removed"
                    )
                    continue
                _compare_output_schema(
                    baseline,
                    candidate,
                    baseline_media.get("schema", {}),
                    candidate_content[media_type].get("schema", {}),
                    f"{location} response {status}",
                    errors,
                )
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors = check_compatibility(load_document(args.baseline), load_document(args.candidate))
    if errors:
        print("Breaking Agent Tool contract changes detected:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Agent Tool contract is backward compatible with the frozen v1 baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
