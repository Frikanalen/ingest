# Ingest

This used to go by move-and-process and fkprocess.


Requires go (for integration tests with tusd)


## Testing

```shell
uv run pytest
```

## Code generation

We use code generation to export the hook request body schema from tusd via a JSON Schema file to Pydantic types.

To update the schema, run 
```shell
scripts/generate-hook-types.sh
```