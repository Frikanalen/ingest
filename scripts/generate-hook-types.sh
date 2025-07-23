#!/bin/bash
set -euo pipefail

cd scripts/ 2> /dev/null || echo>>/dev/null

SCHEMA_JSON=$PWD"/hook.schema.json"
PYTHON_OUT="../libraries/hookschema.py"

echo " - Compiling and running Go program to generate JSON Schema..."
go run gen_hook_schema.go > "$SCHEMA_JSON"
echo " ✅ JSON Schema stored to ${SCHEMA_JSON}."


echo " - Converting to Pydantic using datamodel-code-generator..."
uv run datamodel-codegen \
    --formatters isort ruff-check ruff-format \
    --input "$SCHEMA_JSON" \
    --force-optional \
    --snake-case-field \
    --input-file-type jsonschema \
    --output "$PYTHON_OUT" \
    --output-model-type pydantic_v2.BaseModel

echo " ✅ Python types generated in $PYTHON_OUT"
