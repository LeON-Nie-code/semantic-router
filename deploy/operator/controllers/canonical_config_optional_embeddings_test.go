package controllers

import (
	"context"
	"fmt"
	"testing"

	"sigs.k8s.io/yaml"

	vllmv1alpha1 "github.com/vllm-project/semantic-router/operator/api/v1alpha1"
	routerconfig "github.com/vllm-project/semantic-router/src/semantic-router/pkg/config"
	"github.com/vllm-project/semantic-router/src/semantic-router/pkg/modeldownload"
)

// Exercise the generated ConfigMap and actual model-download plan. Default KB
// declarations are not consumers unless a routing profile references them.
func TestOperatorOptionalEmbeddingConsumersSurviveConfigTranslation(t *testing.T) {
	for _, tc := range []struct {
		name          string
		cache, tools  bool
		wantDownloads int
	}{
		{"disabled", false, false, 0},
		{"unused-cache-enabled", true, false, 0},
		{"tools-enabled", false, true, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var sr vllmv1alpha1.SemanticRouter
			if err := yaml.Unmarshal([]byte(fmt.Sprintf(`spec:
  config:
    response_cache: {enabled: %t}
    tools: {enabled: %t}
    routing:
      signals:
        domains: []
      decisions:
        - name: default
          rules: {operator: AND, conditions: []}
          modelRefs: [{model: general-model}]
`, tc.cache, tc.tools)), &sr); err != nil {
				t.Fatal(err)
			}
			document, err := (&SemanticRouterReconciler{}).generateConfigYAML(context.Background(), &sr)
			if err != nil {
				t.Fatal(err)
			}
			cfg, err := routerconfig.ParseYAMLBytes([]byte(document))
			if err != nil {
				t.Fatal(err)
			}
			if cfg.SemanticCache.Enabled != tc.cache || cfg.Tools.Enabled != tc.tools {
				t.Fatalf("explicit feature switches changed: cache=%t tools=%t, want %t/%t", cfg.SemanticCache.Enabled, cfg.Tools.Enabled, tc.cache, tc.tools)
			}
			if len(cfg.KnowledgeBases) != 2 {
				t.Fatal("default knowledge-base catalog changed")
			}
			specs, err := modeldownload.BuildModelSpecs(cfg)
			if err != nil {
				t.Fatal(err)
			}
			if len(specs) != tc.wantDownloads {
				t.Fatalf("planned %d local downloads, want %d: %+v", len(specs), tc.wantDownloads, specs)
			}
		})
	}
}

func TestDisabledCacheAndToolsKeepExplicitEmbeddingConsumers(t *testing.T) {
	var sr vllmv1alpha1.SemanticRouter
	if err := yaml.Unmarshal([]byte(`spec:
  config:
    response_cache: {enabled: false}
    tools: {enabled: false}
    routing:
      decisions:
        - name: default
          rules: {operator: AND, conditions: []}
          modelRefs: [{model: general-model}]
      signals:
        embeddings:
          - name: support
            threshold: 0.8
            candidates: ["Help with my order"]
`), &sr); err != nil {
		t.Fatal(err)
	}
	document, err := (&SemanticRouterReconciler{}).generateConfigYAML(context.Background(), &sr)
	if err != nil {
		t.Fatal(err)
	}
	cfg, err := routerconfig.ParseYAMLBytes([]byte(document))
	if err != nil {
		t.Fatal(err)
	}
	if len(cfg.KnowledgeBases) != 2 || len(cfg.EmbeddingRules) != 1 {
		t.Fatalf("explicit signal or KB override lost: kbs=%d signals=%d", len(cfg.KnowledgeBases), len(cfg.EmbeddingRules))
	}
	if !routerconfig.EmbeddingModelsNeeded(cfg, "mmbert", true)["mmbert"] {
		t.Fatal("disabling cache and tools suppressed an explicit embedding signal")
	}
}
