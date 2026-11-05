package config

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestUnifiedShadowPayload(t *testing.T) {
	cases := []struct{ name, body, want string }{
		{"multiple", `{"enabled":true,"models":["a","b"]}`, ""},
		{"single", `{"enabled":true,"models":["a"]}`, ""},
		{"disabled", `{"enabled":false}`, ""},
		{"legacy", `{"enabled":true,"model":"a"}`, "unknown field"},
		{"blank", `{"enabled":true,"models":[" "]}`, "empty"},
		{"duplicate", `{"enabled":true,"models":["a"," a "]}`, "duplicate"},
		{"zero", `{"enabled":true,"models":["a"],"execution":{"max_concurrency":0}}`, "positive"},
		{"fractional", `{"enabled":true,"models":["a"],"budget":{"max_calls":0.5}}`, "cannot unmarshal"},
		{"budget type", `{"enabled":true,"models":["a"],"budget":"bad"}`, "cannot unmarshal"},
		{"unknown bound", `{"enabled":true,"models":["a"],"budget":{"max_tokens":1}}`, "unknown field"},
		{"insufficient calls", `{"enabled":true,"models":["a","b"],"budget":{"max_calls":1}}`, "models"},
		{"invalid timeout", `{"enabled":true,"models":["a"],"execution":{"timeout":"0s"}}`, "timeout"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			payload := &StructuredPayload{}
			err := json.Unmarshal([]byte(tc.body), payload)
			if err != nil {
				t.Fatal(err)
			}
			err = validateDecisionPluginPayload("route", 0, DecisionPlugin{Type: DecisionPluginShadowDispatch, Configuration: payload})
			if tc.want == "" {
				if err != nil {
					t.Fatal(err)
				}
			} else if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("got %v, want %s", err, tc.want)
			}
		})
	}
}
