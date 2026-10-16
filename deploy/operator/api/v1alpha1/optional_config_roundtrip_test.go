package v1alpha1

import (
	"context"
	"encoding/json"
	"testing"
)

func TestExplicitlyDisabledFeatureFlagsSurviveTypedClients(t *testing.T) {
	for _, field := range []string{"response_cache", "semantic_cache", "tools"} {
		t.Run(field, func(t *testing.T) {
			var config ConfigSpec
			if err := json.Unmarshal([]byte(`{"`+field+`":{"enabled":false}}`), &config); err != nil {
				t.Fatal(err)
			}
			data, err := json.Marshal(config)
			if err != nil {
				t.Fatal(err)
			}
			var result map[string]map[string]interface{}
			if err := json.Unmarshal(data, &result); err != nil {
				t.Fatal(err)
			}
			if flag, present := result[field]["enabled"]; !present || flag != false {
				t.Fatalf("typed client lost explicit false: %s", data)
			}
		})
	}
}

func TestKnowledgeBaseOverridesPassAdmissionAndDeepCopy(t *testing.T) {
	for _, raw := range []string{`{}`, `{"knowledge_bases":[]}`, `{"knowledge_bases":[{"name":"docs","source":{"path":"knowledge_bases/docs"},"threshold":0.75}]}`} {
		t.Run(raw, func(t *testing.T) {
			var config ConfigSpec
			if err := json.Unmarshal([]byte(raw), &config); err != nil {
				t.Fatal(err)
			}
			sr := &SemanticRouter{Spec: SemanticRouterSpec{Config: config}}
			if _, err := sr.ValidateCreate(context.Background(), sr); err != nil {
				t.Fatal(err)
			}
			copy := sr.DeepCopy()
			if _, err := copy.ValidateUpdate(context.Background(), sr, copy); err != nil {
				t.Fatal(err)
			}
			before, err := json.Marshal(sr.Spec.Config)
			if err != nil {
				t.Fatal(err)
			}
			after, err := json.Marshal(copy.Spec.Config)
			if err != nil || string(before) != string(after) {
				t.Fatalf("deep copy changed the override: %s -> %s, %v", before, after, err)
			}
			if copy.Spec.Config.KnowledgeBases != nil && len(*copy.Spec.Config.KnowledgeBases) > 0 {
				(*copy.Spec.Config.KnowledgeBases)[0].Raw[0] = ' '
				if (*sr.Spec.Config.KnowledgeBases)[0].Raw[0] != '{' {
					t.Fatal("deep copy aliases the original KB JSON")
				}
			}
		})
	}
}
