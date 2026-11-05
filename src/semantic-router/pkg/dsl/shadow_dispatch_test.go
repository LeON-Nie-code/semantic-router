package dsl

import (
	"strings"
	"testing"
)

func TestUnifiedShadowDSL(t *testing.T) {
	for _, tc := range []struct {
		name, fields string
		valid        bool
	}{
		{"models", `models: ["a", "b"] execution: {max_concurrency: 1 timeout: "30s"} budget: {max_calls: 2}`, true},
		{"legacy", `model: "a"`, false},
		{"unknown nested", `models: ["a"] budget: {extra: 1}`, false},
		{"fractional", `models: ["a"] budget: {max_calls: 0.5}`, false},
		{"zero", `models: ["a"] execution: {max_concurrency: 0}`, false},
		{"wrong type", `models: ["a"] budget: "bad"`, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			input := `ROUTE test { PRIORITY 1 WHEN domain("math") MODEL "primary" PLUGIN shadow_dispatch { enabled: true ` + tc.fields + ` } }`
			cfg, errs := Compile(input)
			if !tc.valid {
				if len(errs) == 0 {
					t.Fatal("invalid configuration compiled")
				}
				return
			}
			if len(errs) > 0 {
				t.Fatalf("compile: %v", errs)
			}
			output, err := DecompileRouting(cfg)
			if err != nil {
				t.Fatal(err)
			}
			if !strings.Contains(output, `"a"`) || !strings.Contains(output, "max_concurrency") {
				t.Fatalf("fields lost: %s", output)
			}
			if _, errs = Compile(output); len(errs) > 0 {
				t.Fatalf("roundtrip: %v", errs)
			}
		})
	}
}
