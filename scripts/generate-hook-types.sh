#!/bin/bash
set -euo pipefail

cd scripts/ || echo

SCHEMA_JSON="hook.schema.json"
PYTHON_OUT="../libraries/hookschema.py"

# 1. Compile and run Go program to generate JSON Schema
go run gen_hook_schema.go > "$SCHEMA_JSON"

# 2. Convert to Pydantic using datamodel-code-generator
uv run datamodel-codegen \
    --formatters isort ruff-check ruff-format \
    --input "$SCHEMA_JSON" \
    --snake-case-field \
    --input-file-type jsonschema \
    --output "$PYTHON_OUT" \
    --output-model-type pydantic_v2.BaseModel

echo "✅ Python types generated in $PYTHON_OUT"
