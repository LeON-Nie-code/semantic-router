package config

import (
	"fmt"
	"strings"

	"gopkg.in/yaml.v3"
)

// routerReplayForms are the forms of shadow traffic before router_replay
// owned it: the shadow_dispatch plugin, now the shadow block of the
// decision's router_replay plugin, which records its outcome.
var routerReplayForms = []ConfigForm{
	{
		Code:    CodeRemovedPluginType,
		Paths:   RoutingScopes("decisions[].plugins[]"),
		Match:   hasType("shadow_dispatch"),
		What:    "plugin shadow_dispatch is removed",
		Becomes: RemovedPluginTypes["shadow_dispatch"],
		Migrate: migrateShadowDispatchPlugin,
	},
	{
		Code:    CodeRemovedField,
		Paths:   RoutingScopes("data_policy"),
		What:    "a recipe's data_policy is removed",
		Becomes: "the recipe's router_replay plugin: enabled: false for replay: false, capture_personal_data: false for replay_personal_data: false",
		Migrate: migrateDataPolicy,
	},
}

// dataPolicyFields are the data_policy fields that forbade something when
// false, and the router_replay field that forbids it now.
var dataPolicyFields = []struct{ policy, replay, forbade string }{
	{"replay", "enabled", "replay capture"},
	{"replay_personal_data", "capture_personal_data", "capturing personal data"},
}

// migrateDataPolicy keeps what a recipe's data_policy did. A field set to
// false forbade replay capture, or the capture of personal data, for every
// request of the recipe, whatever a decision's router_replay plugin said: the
// recipe's router_replay plugin forbids it now, and so does each decision's
// that allowed it. Unset or true, a field never allowed anything by itself,
// and nothing replaces it.
func migrateDataPolicy(m *Migration, at FormMatch) error {
	recipe, policy := at.Parent, resolveAlias(at.Node)
	m.Delete(recipe, "data_policy")
	prefix := strings.TrimSuffix(at.Path, ".data_policy")
	forbidden := false
	for _, field := range dataPolicyFields {
		if value := mappingValue(policy, field.policy); value == nil || value.ShortTag() != "!!bool" || !strings.EqualFold(value.Value, "false") {
			continue
		}
		forbidden = true
		plugins := ensureSequence(recipe, "plugins")
		plugin := pluginOfType(plugins, DecisionPluginRouterReplay)
		if plugin == nil {
			plugin = mappingNode("type", DecisionPluginRouterReplay)
			plugins.Content = append(plugins.Content, plugin)
		}
		setNode(ensureMapping(plugin, "configuration"), field.replay, boolNode(false))
		for _, decision := range sequenceAt(recipe, "decisions") {
			decision = resolveAlias(decision)
			replay := pluginOfType(resolveAlias(mappingValue(decision, "plugins")), DecisionPluginRouterReplay)
			if configuration := mappingValue(replay, "configuration"); yamlTrue(mappingValue(configuration, field.replay)) {
				setNode(configuration, field.replay, boolNode(false))
				m.Changed(fmt.Sprintf("%s.decisions[%s]", prefix, scalarAt(decision, "name")),
					fmt.Sprintf("its router_replay %s is false: the recipe's data_policy forbade %s", field.replay, field.forbade))
			}
		}
	}
	if !forbidden {
		m.Changed(at.Path, "data_policy is dropped: it forbade nothing, so it changed nothing")
	}
	return nil
}

// migrateShadowDispatchPlugin moves an enabled shadow_dispatch plugin's
// settings into the shadow block of the decision's router_replay plugin, or
// makes it a router_replay plugin that records the decision. A disabled one
// sent nothing.
func migrateShadowDispatchPlugin(m *Migration, at FormMatch) error {
	plugins := at.Parent
	dispatch := resolveAlias(mappingValue(at.Node, "configuration"))
	for i := 0; dispatch != nil && i+1 < len(dispatch.Content); i += 2 {
		if dispatch.Content[i].ShortTag() != "!!merge" {
			continue
		}
		// Decode the effective mapping before reading enabled or migrating
		// fields: YAML merge keys and aliases also supplied legacy settings.
		var settings map[string]interface{}
		if err := dispatch.Decode(&settings); err != nil {
			return err
		}
		dispatch = &yaml.Node{}
		if err := dispatch.Encode(settings); err != nil {
			return err
		}
		break
	}
	if !yamlTrue(mappingValue(dispatch, "enabled")) {
		removeItem(plugins, at.Node)
		return nil
	}
	shadow, err := migrateShadowSettings(m, at, dispatch)
	if err != nil {
		return err
	}
	for _, item := range plugins.Content {
		if scalarAt(item, "type") != DecisionPluginRouterReplay {
			continue
		}
		replay := mappingValue(item, "configuration")
		if replay == nil {
			replay = &yaml.Node{Kind: yaml.MappingNode, Tag: "!!map"}
			m.Set(item, "configuration", replay)
		}
		if mappingValue(replay, "shadow") != nil {
			return fmt.Errorf("the decision's router_replay plugin already has a shadow")
		}
		if enabled := mappingValue(replay, "enabled"); enabled != nil && !yamlTrue(enabled) {
			m.Note(at.Path, "the decision's router_replay plugin is disabled, and a shadow runs only while the decision records replays; enable it to keep the shadow")
		}
		m.Set(replay, "shadow", shadow)
		removeItem(plugins, at.Node)
		return nil
	}
	replay := &yaml.Node{Kind: yaml.MappingNode, Tag: "!!map"}
	if err := m.SetValue(replay, "enabled", true); err != nil {
		return err
	}
	m.Set(replay, "shadow", shadow)
	m.Set(at.Node, "type", Scalar(DecisionPluginRouterReplay))
	m.Set(at.Node, "configuration", replay)
	m.Changed(at.Path, "router_replay now records this decision, so that the replay record keeps the shadow's outcome")
	return nil
}

// migrateShadowSettings rewrites the released single-model configuration.
func migrateShadowSettings(m *Migration, at FormMatch, dispatch *yaml.Node) (*yaml.Node, error) {
	shadow := &yaml.Node{Kind: yaml.MappingNode, Tag: "!!map"}
	for i := 0; i+1 < len(dispatch.Content); i += 2 {
		key, value := dispatch.Content[i].Value, dispatch.Content[i+1]
		switch key {
		case "enabled":
		case "model":
			m.Set(shadow, "models", &yaml.Node{Kind: yaml.SequenceNode, Tag: "!!seq", Content: []*yaml.Node{value}})
		case "sample_rate":
			m.Set(shadow, key, value)
		case "capture_response_body":
			m.Set(ensureMapping(shadow, "capture"), "enabled", value)
		case "max_concurrency", "max_queue_depth", "timeout_seconds", "max_response_bytes", "max_capture_bytes":
			var raw interface{}
			if err := value.Decode(&raw); err != nil {
				return nil, err
			}
			// Like an omitted or zero limit, null selected the old default.
			if raw == nil {
				continue
			}
			payload, err := NewStructuredPayload(raw)
			var bound int
			if err != nil || payload == nil || payload.DecodeInto(&bound) != nil || bound < 0 {
				return nil, fmt.Errorf("%s: %s must be a non-negative integer", at.Path, key)
			}
			// A zero in the old configuration selected the default.
			if bound == 0 {
				continue
			}
			if key == "timeout_seconds" && bound > int(maxShadowTimeout.Seconds()) {
				bound = int(maxShadowTimeout.Seconds())
				m.Note(at.Path, "timeout_seconds is limited to 300s by the shadow execution deadline")
			}
			if key == "max_response_bytes" && bound < 2 {
				bound = 2
				m.Note(at.Path, "max_response_bytes is raised to 2 to allow an overflow probe")
			}
			value = &yaml.Node{}
			if err := value.Encode(bound); err != nil {
				return nil, err
			}
			switch key {
			case "max_concurrency":
				m.Set(ensureMapping(shadow, "execution"), "max_concurrency", value)
			case "max_queue_depth":
				m.Set(ensureMapping(shadow, "execution"), "max_queue_size", value)
			case "timeout_seconds":
				m.Set(ensureMapping(shadow, "execution"), "timeout", Scalar(fmt.Sprintf("%ds", bound)))
			case "max_response_bytes":
				m.Set(ensureMapping(shadow, "budget"), "max_response_bytes", value)
			case "max_capture_bytes":
				m.Set(ensureMapping(shadow, "capture"), "max_bytes", value)
			}
		case "forward_headers":
			m.Set(shadow, key, value)
		case "max_retries", "tls_skip_verify":
			m.Note(at.Path, key+" is removed: shadow candidates use one attempt, verified TLS and their own provider credentials and headers")
		default:
			return nil, fmt.Errorf("%s: unknown shadow field %q", at.Path, key)
		}
	}
	return shadow, nil
}
