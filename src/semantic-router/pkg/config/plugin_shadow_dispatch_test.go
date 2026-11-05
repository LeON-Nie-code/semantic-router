package config

import (
	"strings"
	"testing"
)

func TestShadowDispatchPluginConfigDefaults(t *testing.T) {
	c := ShadowDispatchPluginConfig{Enabled: true, Models: []string{" candidate ", "other"}}.WithDefaults()
	if c.Models[0] != "candidate" || *c.Execution.MaxConcurrency != 2 || *c.Execution.MaxQueueSize != 8 || c.Execution.Timeout != "30s" || *c.Budget.MaxCalls != 2 || *c.Budget.MaxResponseBytes != 2<<20 || *c.Capture.MaxBytes != 8192 || c.EffectiveSampleRate() != 1 {
		t.Fatalf("unexpected defaults: %+v", c)
	}
	original := ShadowDispatchPluginConfig{Models: []string{" candidate "}}
	_ = original.WithDefaults()
	if original.Models[0] != " candidate " {
		t.Fatal("defaults mutated source models")
	}
}

func TestShadowDispatchModelMustHaveBackend(t *testing.T) {
	cfg := &RouterConfig{
		BackendModels: BackendModels{
			ModelConfig: map[string]ModelParams{
				"primary":   {PreferredEndpoints: []string{"ep"}},
				"candidate": {PreferredEndpoints: []string{"ep"}},
			},
			VLLMEndpoints: []VLLMEndpoint{{Name: "ep", Address: "127.0.0.1", Port: 8000}},
		},
	}
	decision := &Decision{
		Name:      "route",
		ModelRefs: []ModelRef{{Model: "primary"}},
		Plugins: []DecisionPlugin{{
			Type: DecisionPluginShadowDispatch,
			Configuration: MustStructuredPayload(map[string]interface{}{
				"enabled": true,
				"models":  []string{"candidate"},
			}),
		}},
	}
	if err := validateDecisionShadowDispatchPlugin(cfg, decision); err != nil {
		t.Fatalf("configured shadow model rejected: %v", err)
	}
	if got := decision.GetShadowDispatchConfig(); got == nil || len(got.Models) != 1 || got.Models[0] != "candidate" {
		t.Fatalf("GetShadowDispatchConfig = %+v", got)
	}

	decision.Plugins[0].Configuration = MustStructuredPayload(map[string]interface{}{
		"enabled": true,
		"models":  []string{"candidate", "unknown"},
	})
	err := validateDecisionShadowDispatchPlugin(cfg, decision)
	if err == nil || !strings.Contains(err.Error(), "no configured backend") {
		t.Fatalf("error = %v, want unknown backend rejection", err)
	}

	if (&Decision{Name: "plain"}).GetShadowDispatchConfig() != nil {
		t.Fatal("decision without plugin must return nil config")
	}

	decision.Plugins[0].Configuration = MustStructuredPayload(map[string]interface{}{
		"enabled": true,
		"models":  []string{"candidate"},
	})
	decision.Algorithm = &AlgorithmConfig{Type: DecisionAlgorithmRatings}
	err = validateDecisionShadowDispatchPlugin(cfg, decision)
	if err == nil || !strings.Contains(err.Error(), "looper-executed") {
		t.Fatalf("error = %v, want looper rejection", err)
	}
}

func TestShadowDispatchValidateSharedWithConverter(t *testing.T) {
	zero := 0
	c := ShadowDispatchPluginConfig{Enabled: true, Models: []string{"m"}, Budget: ShadowBudgetConfig{MaxCalls: &zero}}
	if err := c.Validate(); err == nil {
		t.Fatal("explicit zero must fail")
	}
	var absent *ShadowDispatchPluginConfig
	if err := absent.Validate(); err != nil {
		t.Fatal(err)
	}
}

func TestShadowCredentialHeaders(t *testing.T) {
	for _, name := range []string{"Authorization", "x-api-key", "Cookie", "x-user-openai-key"} {
		if !IsShadowCredentialHeader(name) {
			t.Errorf("not recognized: %s", name)
		}
	}
	if IsShadowCredentialHeader("traceparent") {
		t.Fatal("traceparent is not a credential")
	}
}
