package config

import (
	"fmt"
	"math"
	"regexp"
	"strings"
	"time"

	"github.com/vllm-project/semantic-router/src/semantic-router/pkg/headers"
)

// shadowCredentialHeaders names the headers that carry a credential on the
// primary path: the auth headers the provider registry knows, the per-user
// key headers the auth backend injects, and the generic HTTP credential
// carriers. A shadow copy never carries them. Names are lower-case.
var shadowCredentialHeaders = map[string]struct{}{
	"authorization":                    {},
	"proxy-authorization":              {},
	"cookie":                           {},
	"x-api-key":                        {},
	"api-key":                          {},
	"x-goog-api-key":                   {},
	headers.UserOpenAIKey:              {},
	headers.UserAnthropicKey:           {},
	headers.UserAzureOpenAIKey:         {},
	headers.UserBedrockKey:             {},
	headers.UserGeminiKey:              {},
	headers.UserVertexAIKey:            {},
	headers.UserMiniMaxKey:             {},
	headers.UserCloudflareWorkersAIKey: {},
}

// IsShadowCredentialHeader reports whether a header name is a known
// credential carrier that must never travel with a shadow copy.
func IsShadowCredentialHeader(name string) bool {
	_, ok := shadowCredentialHeaders[strings.ToLower(strings.TrimSpace(name))]
	return ok
}

const maxShadowModels = 8

var shadowDurationPattern = regexp.MustCompile(`^\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:ms|s|m)(?:(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:ms|s|m))*$`)

// DefaultShadowDispatchPluginConfig returns bounded defaults. Models must be
// explicitly configured before enabling observations.
func DefaultShadowDispatchPluginConfig() ShadowDispatchPluginConfig {
	return (ShadowDispatchPluginConfig{}).WithDefaults()
}

func shadowBound(value *int, fallback int) *int {
	if value != nil {
		fallback = *value
	}
	return &fallback
}

// WithDefaults returns an independent configuration snapshot.
func (c ShadowDispatchPluginConfig) WithDefaults() ShadowDispatchPluginConfig {
	c.Models = append([]string(nil), c.Models...)
	for i := range c.Models {
		c.Models[i] = strings.TrimSpace(c.Models[i])
	}
	n := max(1, len(c.Models))
	c.Execution.MaxConcurrency = shadowBound(c.Execution.MaxConcurrency, 2)
	c.Execution.MaxQueueSize = shadowBound(c.Execution.MaxQueueSize, 8)
	if c.Execution.Timeout == "" {
		c.Execution.Timeout = "30s"
	}
	c.Budget.MaxCalls = shadowBound(c.Budget.MaxCalls, n)
	c.Budget.MaxResponseBytes = shadowBound(c.Budget.MaxResponseBytes, n*(1<<20))
	c.Capture.MaxBytes = shadowBound(c.Capture.MaxBytes, n*4096)
	if c.SampleRate != nil {
		rate := *c.SampleRate
		c.SampleRate = &rate
	}
	return c
}

func (c *ShadowDispatchPluginConfig) EffectiveSampleRate() float64 {
	if c == nil || c.SampleRate == nil {
		return 1
	}
	return *c.SampleRate
}

// TimeoutDuration is safe after Validate has accepted the configuration.
func (c ShadowDispatchPluginConfig) TimeoutDuration() time.Duration {
	duration, _ := time.ParseDuration(c.WithDefaults().Execution.Timeout)
	return duration
}

func (c *ShadowDispatchPluginConfig) Validate() error {
	if c == nil {
		return nil
	}
	if c.Enabled && len(c.Models) == 0 {
		return fmt.Errorf("models is required when enabled")
	}
	if len(c.Models) > maxShadowModels {
		return fmt.Errorf("models cannot exceed %d candidates", maxShadowModels)
	}
	seen := map[string]bool{}
	for _, model := range c.Models {
		name := strings.TrimSpace(model)
		if name == "" {
			return fmt.Errorf("models cannot contain empty names")
		}
		if seen[name] {
			return fmt.Errorf("models contains duplicate %q", name)
		}
		seen[name] = true
	}
	rate := c.EffectiveSampleRate()
	if math.IsNaN(rate) || math.IsInf(rate, 0) || rate < 0 || rate > 1 {
		return fmt.Errorf("sample_rate must be finite and between 0 and 1")
	}
	effective := c.WithDefaults()
	for _, bound := range []struct {
		name  string
		value *int
	}{
		{"execution.max_concurrency", effective.Execution.MaxConcurrency},
		{"execution.max_queue_size", effective.Execution.MaxQueueSize},
		{"budget.max_calls", effective.Budget.MaxCalls},
		{"budget.max_response_bytes", effective.Budget.MaxResponseBytes},
		{"capture.max_bytes", effective.Capture.MaxBytes},
	} {
		if *bound.value <= 0 {
			return fmt.Errorf("%s must be positive", bound.name)
		}
	}
	duration, err := time.ParseDuration(effective.Execution.Timeout)
	if err != nil || duration <= 0 || duration > 5*time.Minute || len(effective.Execution.Timeout) > 128 || !shadowDurationPattern.MatchString(effective.Execution.Timeout) {
		return fmt.Errorf("execution.timeout must be a positive duration at most 5m")
	}
	if *effective.Budget.MaxCalls < len(c.Models) {
		return fmt.Errorf("budget.max_calls must cover all configured models")
	}
	if *effective.Budget.MaxResponseBytes < 2*len(c.Models) {
		return fmt.Errorf("budget.max_response_bytes must allocate at least two bytes to each model")
	}
	return nil
}

func validateShadowDispatchPlugin(decisionName string, index int, pluginType string, typed *ShadowDispatchPluginConfig) error {
	if err := typed.Validate(); err != nil {
		return fmt.Errorf("decision %q plugins[%d] (%s): %w", decisionName, index, pluginType, err)
	}
	return nil
}

func validateDecisionShadowDispatchPlugin(cfg *RouterConfig, decision *Decision) error {
	if cfg == nil || decision == nil {
		return nil
	}
	shadow := decision.GetShadowDispatchConfig()
	if shadow == nil || !shadow.Enabled {
		return nil
	}
	if err := shadow.Validate(); err != nil {
		return fmt.Errorf("decision %q: %w", decision.Name, err)
	}
	if decision.Algorithm != nil && IsLooperAlgorithmType(decision.Algorithm.Type) {
		return fmt.Errorf("decision %q: shadow_dispatch is not supported on looper-executed algorithm %q", decision.Name, decision.Algorithm.Type)
	}
	for _, model := range shadow.WithDefaults().Models {
		if len(cfg.GetEndpointsForModel(model)) == 0 {
			return fmt.Errorf("decision %q: shadow_dispatch model %q has no configured backend", decision.Name, model)
		}
	}
	return nil
}
