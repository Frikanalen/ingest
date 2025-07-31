#!/bin/bash
set -euo pipefail

cd scripts/ 2> /dev/null || echo>>/dev/null

TUSD_REQUEST_SCHEMA_JSON=$PWD"/hook-request.schema.json"
TUSD_REQUEST_SCHEMA_PY="../app/api/hooks/schema/request.py"
TUSD_RESPONSE_SCHEMA_JSON=$PWD"/hook-response.schema.json"
TUSD_RESPONSE_SCHEMA_PY="../app/api/hooks/schema/response.py"
FFPROBE_SCHEMA_JSON=$PWD"/ffprobe.json"
FFPROBE_SCHEMA_OUTPUT="../app/media/ffprobe_schema.py"

echo " - Compiling and running Go program to generate JSON Schema..."
go run gen_hook_schema.go request
echo " ✅ JSON Schema for Request stored to ${TUSD_REQUEST_SCHEMA_JSON}."
go run gen_hook_schema.go response
echo " ✅ JSON Schema for Response stored to ${TUSD_RESPONSE_SCHEMA_JSON}."

echo " - Converting to Pydantic using datamodel-code-generator..."
uv run datamodel-codegen \
    --formatters isort ruff-check ruff-format \
    --input "${TUSD_REQUEST_SCHEMA_JSON}" \
    --force-optional \
    --disable-timestamp \
    --snake-case-field \
    --input-file-type jsonschema \
    --target-python-version 3.11 \
    --use-standard-collections \
    --enum-field-as-literal=all \
    --output "${TUSD_REQUEST_SCHEMA_PY}" \
    --output-model-type pydantic_v2.BaseModel
echo " ✅ Python request types generated in ${TUSD_REQUEST_SCHEMA_PY}"

uv run datamodel-codegen \
    --formatters isort ruff-check ruff-format \
    --input "${TUSD_RESPONSE_SCHEMA_JSON}" \
    --force-optional \
    --disable-timestamp \
    --snake-case-field \
    --input-file-type jsonschema \
    --target-python-version 3.11 \
    --use-standard-collections \
    --enum-field-as-literal=all \
    --output "${TUSD_RESPONSE_SCHEMA_PY}" \
    --output-model-type pydantic_v2.BaseModel
echo " ✅ Python response types generated in ${TUSD_RESPONSE_SCHEMA_PY}"

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
