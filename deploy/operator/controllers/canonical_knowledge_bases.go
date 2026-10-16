package controllers

import (
	"fmt"

	"gopkg.in/yaml.v3"

	vllmv1alpha1 "github.com/vllm-project/semantic-router/operator/api/v1alpha1"
	routerconfig "github.com/vllm-project/semantic-router/src/semantic-router/pkg/config"
)

func applyOperatorKnowledgeBases(canonical *routerconfig.CanonicalConfig, spec vllmv1alpha1.ConfigSpec) error {
	if spec.KnowledgeBases == nil {
		return nil
	}
	kbs := make([]routerconfig.KnowledgeBaseConfig, 0, len(*spec.KnowledgeBases))
	for index, raw := range *spec.KnowledgeBases {
		kb, err := decodeCanonicalModelObject[routerconfig.KnowledgeBaseConfig](&raw)
		if err != nil {
			return fmt.Errorf("config.knowledge_bases[%d]: %w", index, err)
		}
		kbs = append(kbs, kb)
	}
	canonical.Global.ModelCatalog.KBs = kbs
	return nil
}

func marshalOperatorConfig(config *routerconfig.CanonicalConfig, spec vllmv1alpha1.ConfigSpec) ([]byte, error) {
	data, err := yaml.Marshal(config)
	if err != nil || spec.KnowledgeBases == nil || len(*spec.KnowledgeBases) != 0 {
		return data, err
	}
	// CanonicalModelCatalog omits empty KBs. Keep this explicitly configured
	// override in the document, or the Router loader restores its defaults.
	var document map[string]interface{}
	if err := yaml.Unmarshal(data, &document); err != nil {
		return nil, err
	}
	global, ok := document["global"].(map[string]interface{})
	if !ok {
		return nil, fmt.Errorf("operator config is missing global")
	}
	catalog, ok := global["model_catalog"].(map[string]interface{})
	if !ok {
		return nil, fmt.Errorf("operator config is missing global.model_catalog")
	}
	catalog["kbs"] = []interface{}{}
	return yaml.Marshal(document)
}
