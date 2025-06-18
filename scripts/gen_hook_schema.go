// +build ignore

package main

import (
	"encoding/json"
	"fmt"

	"github.com/invopop/jsonschema"
	"github.com/tus/tusd/v2/pkg/hooks"
)

func main() {
	schema := jsonschema.Reflect(&hooks.HookRequest{})
	data, _ := json.MarshalIndent(schema, "", "  ")
	fmt.Println(string(data))
}
