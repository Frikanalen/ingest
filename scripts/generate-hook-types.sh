#!/bin/bash
set -euo pipefail

cd scripts/ 2> /dev/null || echo>>/dev/null

TUSD_SCHEMA_JSON=$PWD"/hook.schema.json"
FFPROBE_SCHEMA_JSON=$PWD"/ffprobe.json"


TUSD_SCHEMA_OUTPUT="../app/tus_hook/hook_schema.py"
FFPROBE_SCHEMA_OUTPUT="../app/ffprobe_schema.py"

echo " - Compiling and running Go program to generate JSON Schema..."
go run gen_hook_schema.go > "$TUSD_SCHEMA_JSON"
echo " ✅ JSON Schema stored to ${TUSD_SCHEMA_JSON}."


echo " - Converting to Pydantic using datamodel-code-generator..."
uv run datamodel-codegen \
    --formatters isort ruff-check ruff-format \
    --input "$TUSD_SCHEMA_JSON" \
    --force-optional \
    --disable-timestamp \
    --snake-case-field \
    --input-file-type jsonschema \
    --target-python-version 3.11 \
    --use-standard-collections \
    --enum-field-as-literal=all \
    --output "$TUSD_SCHEMA_OUTPUT" \
    --output-model-type pydantic_v2.BaseModel
echo " ✅ Python types generated in $TUSD_SCHEMA_OUTPUT"

echo " - Generating ffprobe schema using datamodel-code-generator..."
    uv run datamodel-codegen \
    --formatters isort ruff-check ruff-format \
    --input "$FFPROBE_SCHEMA_JSON" \
    --disable-timestamp \
    --snake-case-field \
    --input-file-type jsonschema \
    --target-python-version 3.11 \
    --use-standard-collections \
    --output "$FFPROBE_SCHEMA_OUTPUT" \
    --class-name FfprobeOutput \
    --output-model-type pydantic_v2.BaseModel

echo " ✅ Python types generated in $FFPROBE_SCHEMA_OUTPUT"
