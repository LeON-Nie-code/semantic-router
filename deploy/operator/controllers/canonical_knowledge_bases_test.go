package controllers

import (
	"context"
	"encoding/json"
	"testing"

	vllmv1alpha1 "github.com/vllm-project/semantic-router/operator/api/v1alpha1"
	routerconfig "github.com/vllm-project/semantic-router/src/semantic-router/pkg/config"
)

func TestOperatorKnowledgeBasesSurviveTheRouterLoadPath(t *testing.T) {
	for _, tc := range []struct {
		name, config string
		want         int
	}{
		{"omitted", `{}`, len(routerconfig.DefaultCanonicalGlobal().ModelCatalog.KBs)},
		{"empty", `{"knowledge_bases":[]}`, 0},
		{"custom", `{"knowledge_bases":[{"name":"project-docs","source":{"path":"knowledge_bases/project"},"threshold":0.75}]}`, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var spec vllmv1alpha1.ConfigSpec
			if err := json.Unmarshal([]byte(tc.config), &spec); err != nil {
				t.Fatal(err)
			}
			// Kubernetes clients serialize the typed API before the controller
			// sees it. An empty override must survive that boundary as well.
			encoded, err := json.Marshal(spec)
			if err != nil {
				t.Fatal(err)
			}
			if err = json.Unmarshal(encoded, &spec); err != nil {
				t.Fatal(err)
			}
			r := &SemanticRouterReconciler{}
			document, err := r.generateConfigYAML(context.Background(), &vllmv1alpha1.SemanticRouter{
				Spec: vllmv1alpha1.SemanticRouterSpec{Config: spec},
			})
			if err != nil {
				t.Fatal(err)
			}
			cfg, err := routerconfig.ParseYAMLBytes([]byte(document))
			if err != nil {
				t.Fatal(err)
			}
			if len(cfg.KnowledgeBases) != tc.want {
				t.Fatalf("loaded %d KBs, want %d", len(cfg.KnowledgeBases), tc.want)
			}
			if tc.name == "custom" && (cfg.KnowledgeBases[0].Name != "project-docs" || cfg.KnowledgeBases[0].Threshold != 0.75) {
				t.Fatalf("custom KB was not preserved: %+v", cfg.KnowledgeBases[0])
			}
		})
	}
}

func TestOperatorRejectsUnknownKnowledgeBaseFields(t *testing.T) {
	var spec vllmv1alpha1.ConfigSpec
	if err := json.Unmarshal([]byte(`{"knowledge_bases":[{"name":"docs","unknown_field":{"path":"docs"}}]}`), &spec); err != nil {
		t.Fatal(err)
	}
	r := &SemanticRouterReconciler{}
	_, err := r.generateConfigYAML(context.Background(), &vllmv1alpha1.SemanticRouter{
		Spec: vllmv1alpha1.SemanticRouterSpec{Config: spec},
	})
	if err == nil {
		t.Fatal("unknown knowledge-base entry field was silently discarded")
	}
}

func TestOperatorOptionalFeatureDefaultsMatchTheCRD(t *testing.T) {
	for _, tc := range []struct {
		name, document string
		want           bool
	}{
		{"omitted flags", `{"response_cache":{},"tools":{}}`, true},
		{"disabled", `{"response_cache":{"enabled":false},"tools":{"enabled":false}}`, false},
		{"enabled", `{"response_cache":{"enabled":true},"tools":{"enabled":true}}`, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var spec vllmv1alpha1.ConfigSpec
			if err := json.Unmarshal([]byte(tc.document), &spec); err != nil {
				t.Fatal(err)
			}
			r := &SemanticRouterReconciler{}
			config, err := r.buildCanonicalConfig(context.Background(), &vllmv1alpha1.SemanticRouter{
				Spec: vllmv1alpha1.SemanticRouterSpec{Config: spec},
			})
			if err != nil {
				t.Fatal(err)
			}
			if config.Global.Stores.ResponseCache.Enabled != tc.want || config.Global.Integrations.Tools.Enabled != tc.want {
				t.Fatalf("cache=%v tools=%v, want %v", config.Global.Stores.ResponseCache.Enabled, config.Global.Integrations.Tools.Enabled, tc.want)
			}
		})
	}
}
