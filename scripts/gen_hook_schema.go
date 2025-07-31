// +build ignore

package main

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/invopop/jsonschema"
	"github.com/tus/tusd/v2/pkg/hooks"
)


func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: go run gen_hook_schema.go [request|response]")
		os.Exit(1)
	}

	var schema *jsonschema.Schema
	switch os.Args[1] {
	case "request":
		schema = jsonschema.Reflect(&hooks.HookRequest{})
	case "response":
		schema = jsonschema.Reflect(&hooks.HookResponse{})
	default:
		fmt.Fprintf(os.Stderr, "unknown argument %q, want 'request' or 'response'\n", os.Args[1])
		os.Exit(1)
	}

	data, err := json.MarshalIndent(schema, "", "  ")
	if err != nil {
		fmt.Fprintln(os.Stderr, "error marshaling schema:", err)
		os.Exit(1)
	}

	filename := fmt.Sprintf("hook-%s.schema.json", os.Args[1])
	if err := os.WriteFile(filename, data, 0644); err != nil {
		fmt.Fprintln(os.Stderr, "error writing file:", err)
		os.Exit(1)
	}
}