package testcases

import (
	"context"
	"fmt"

	"k8s.io/client-go/kubernetes"

	pkgtestcases "github.com/vllm-project/semantic-router/e2e/pkg/testcases"
)

func init() {
	pkgtestcases.Register("shadow-dispatch-multi-model-completes", pkgtestcases.TestCase{
		Description: "Both candidates complete with shared concurrency one and leave the primary response unchanged",
		Tags:        []string{"router-replay", "shadow-dispatch", "functional"},
		Fn:          testShadowDispatchMultiModel,
	})
}

func testShadowDispatchMultiModel(ctx context.Context, client *kubernetes.Clientset, opts pkgtestcases.TestCaseOptions) error {
	run, err := openShadowDispatchRun(ctx, client, opts)
	if err != nil {
		return err
	}
	defer run.Close()
	session, err := run.primary(ctx, "vllm-sr/shadow-multi", shadowPrimaryCeiling)
	if err != nil {
		return err
	}
	record, err := run.replay(ctx, session, 2)
	if err != nil {
		return err
	}
	expected := map[string]bool{"openai/shadow-candidate": false, "openai/shadow-second": false}
	for _, outcome := range record.Outcomes {
		seen, ok := expected[outcome.TargetRef]
		if !ok || seen || outcome.Verdict != "completed" || outcome.Metadata["response_sha256"] == "" {
			return fmt.Errorf("unexpected candidate outcome: %+v", outcome)
		}
		expected[outcome.TargetRef] = true
	}
	for model, seen := range expected {
		if !seen {
			return fmt.Errorf("missing completed outcome for %s", model)
		}
	}
	return nil
}
