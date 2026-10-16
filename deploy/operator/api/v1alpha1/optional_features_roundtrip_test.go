package v1alpha1

import (
	"context"
	"encoding/json"
	"testing"
)

func TestExplicitlyDisabledFeaturesSurviveAdmissionAndSerialization(t *testing.T) {
	var sr SemanticRouter
	if err := json.Unmarshal([]byte(`{"spec":{"config":{"response_cache":{"enabled":false},"tools":{"enabled":false}}}}`), &sr); err != nil {
		t.Fatal(err)
	}
	if _, err := sr.ValidateCreate(context.Background(), &sr); err != nil {
		t.Fatal(err)
	}
	clone := sr.DeepCopy()
	if _, err := sr.ValidateUpdate(context.Background(), &sr, clone); err != nil {
		t.Fatal(err)
	}
	data, err := json.Marshal(clone)
	if err != nil {
		t.Fatal(err)
	}
	var decoded struct {
		Spec struct {
			Config map[string]map[string]interface{} `json:"config"`
		} `json:"spec"`
	}
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"response_cache", "tools"} {
		if value, found := decoded.Spec.Config[name]["enabled"]; !found || value != false {
			t.Errorf("%s lost explicit false; Kubernetes can reapply enabled=true", name)
		}
	}
}
