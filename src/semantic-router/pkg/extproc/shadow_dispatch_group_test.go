package extproc

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/vllm-project/semantic-router/src/semantic-router/pkg/config"
	"github.com/vllm-project/semantic-router/src/semantic-router/pkg/routerreplay"
)

func TestShadowGroupQueuesAllModels(t *testing.T) {
	backend := newShadowTestBackend(t)
	router, primary := newShadowTestRouter(t, backend)
	router.Config.ModelConfig["second"] = config.ModelParams{PreferredEndpoints: []string{"shadow-backend"}, APIFormat: config.APIFormatOpenAI}
	cfg := shadowTestPluginConfig()
	cfg.Models = append(cfg.Models, "second")
	cfg.Execution.MaxConcurrency = shadowTestInt(1)
	baseline := runShadowRequest(t, router, primary, nil, nil)
	run := runShadowRequest(t, router, primary, cfg, nil)
	waitForShadow(t, router)
	if !bytes.Equal(baseline.body, run.body) {
		t.Fatal("shadow altered primary request")
	}
	if backend.requestCount() != 2 || len(shadowOutcomes(t, run)) != 2 {
		t.Fatalf("calls=%d outcomes=%d, want both candidates", backend.requestCount(), len(shadowOutcomes(t, run)))
	}
	var bodies [2]map[string]any
	for i := range bodies {
		if err := json.Unmarshal(backend.bodies[i], &bodies[i]); err != nil {
			t.Fatal(err)
		}
		delete(bodies[i], "model")
	}
	a, _ := json.Marshal(bodies[0])
	b, _ := json.Marshal(bodies[1])
	if !bytes.Equal(a, b) {
		t.Fatal("candidate inputs differ")
	}
}

func TestShadowGroupDisconnectCancelsAcceptedWork(t *testing.T) {
	backend := newShadowTestBackend(t)
	started, release := make(chan struct{}), make(chan struct{})
	backend.setHandler(func(w http.ResponseWriter, _ []byte) {
		close(started)
		<-release
		writeShadowChatCompletion(w, shadowTestReply)
	})
	defer close(release)
	router, primary := newShadowTestRouter(t, backend)
	run := runShadowRequest(t, router, primary, shadowTestPluginConfig(), nil)
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("candidate did not start")
	}
	router.finalizeRouterReplay(run.ctx, routerreplay.LifecycleAborted, "client_canceled")
	if !router.ShadowDispatcher.waitIdle(time.Second) {
		t.Fatal("abandoned candidate kept running")
	}
	if outcomes := shadowOutcomes(t, run); len(outcomes) != 1 || outcomes[0].Verdict != shadowVerdictFailed || outcomes[0].Reason != "canceled" {
		t.Fatalf("outcomes=%v", outcomes)
	}
}

func TestShadowGroupNormalCompletionSurvivesEOF(t *testing.T) {
	for _, headersOnly := range []bool{false, true} {
		t.Run(map[bool]string{false: "body", true: "headers"}[headersOnly], func(t *testing.T) {
			backend := newShadowTestBackend(t)
			started, release := make(chan struct{}), make(chan struct{})
			defer close(release)
			backend.setHandler(func(w http.ResponseWriter, _ []byte) {
				close(started)
				<-release
				writeShadowChatCompletion(w, shadowTestReply)
			})
			router, primary := newShadowTestRouter(t, backend)
			run := runShadowRequest(t, router, primary, shadowTestPluginConfig(), nil)
			select {
			case <-started:
			case <-time.After(time.Second):
				t.Fatal("candidate did not start")
			}
			// Completion must work even when Replay is disabled for the request.
			run.ctx.RouterReplayID = ""
			if headersOnly {
				responseHeadersReplySent(run.ctx, nil, true)
			} else {
				responseBodyReplySent(run.ctx, nil, true)
			}
			if err := router.handleProcessReceiveError(run.ctx, io.EOF); err != nil {
				t.Fatal(err)
			}
			if router.ShadowDispatcher.waitIdle(30 * time.Millisecond) {
				t.Fatal("normal EOF canceled an unfinished candidate")
			}
		})
	}
}

func TestShadowGroupSnapshotOwnsQueuedInput(t *testing.T) {
	backend := newShadowTestBackend(t)
	started, release := make(chan struct{}, 1), make(chan struct{})
	backend.setHandler(func(w http.ResponseWriter, _ []byte) {
		select {
		case started <- struct{}{}:
		default:
		}
		<-release
		writeShadowChatCompletion(w, shadowTestReply)
	})
	router, primary := newShadowTestRouter(t, backend)
	cfg := shadowTestPluginConfig()
	cfg.Execution.MaxConcurrency = shadowTestInt(1)
	runShadowRequest(t, router, primary, cfg, nil)
	select {
	case <-started:
	case <-time.After(time.Second):
		close(release)
		t.Fatal("candidate did not start")
	}
	queued := runShadowRequest(t, router, primary, cfg, nil)
	queued.ctx.SemanticRequest.Messages[0].Role = "system"
	close(release)
	waitForShadow(t, router)
	if backend.requestCount() != 2 {
		t.Fatal("queued candidate did not run")
	}
	var wire struct {
		Messages []struct {
			Role string `json:"role"`
		} `json:"messages"`
	}
	if err := json.Unmarshal(backend.bodies[1], &wire); err != nil {
		t.Fatal(err)
	}
	if len(wire.Messages) == 0 || wire.Messages[0].Role != "user" {
		t.Fatalf("snapshot changed: %s", backend.bodies[1])
	}
}

func TestShadowGroupMissingUsageRemainsUnknown(t *testing.T) {
	backend := newShadowTestBackend(t)
	backend.setHandler(func(w http.ResponseWriter, _ []byte) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id":"no-usage","object":"chat.completion","model":"shadow-model","choices":[{"index":0,"message":{"role":"assistant","content":"answer"},"finish_reason":"stop"}]}`))
	})
	router, primary := newShadowTestRouter(t, backend)
	run := runShadowRequest(t, router, primary, shadowTestPluginConfig(), nil)
	waitForShadow(t, router)
	out := singleShadowOutcome(t, run)
	if _, ok := out.Metadata["input_tokens"]; ok {
		t.Fatal("missing usage was recorded as measured zero")
	}
	if _, ok := out.Metadata["output_tokens"]; ok {
		t.Fatal("missing output usage was recorded as measured zero")
	}
}

func TestShadowGroupSiblingFailureIsIsolated(t *testing.T) {
	backend := newShadowTestBackend(t)
	var calls atomic.Int32
	backend.setHandler(func(w http.ResponseWriter, _ []byte) {
		if calls.Add(1) == 1 {
			http.Error(w, "failed", http.StatusServiceUnavailable)
			return
		}
		writeShadowChatCompletion(w, shadowTestReply)
	})
	router, primary := newShadowTestRouter(t, backend)
	router.Config.ModelConfig["second"] = config.ModelParams{PreferredEndpoints: []string{"shadow-backend"}, APIFormat: config.APIFormatOpenAI}
	cfg := shadowTestPluginConfig()
	cfg.Models = append(cfg.Models, "second")
	cfg.Execution.MaxConcurrency = shadowTestInt(1)
	run := runShadowRequest(t, router, primary, cfg, nil)
	waitForShadow(t, router)
	outcomes := shadowOutcomes(t, run)
	if len(outcomes) != 2 {
		t.Fatalf("outcomes=%d, want 2", len(outcomes))
	}
	counts := map[string]int{}
	for _, outcome := range outcomes {
		counts[outcome.Verdict]++
	}
	if counts[shadowVerdictFailed] != 1 || counts[shadowVerdictCompleted] != 1 {
		t.Fatalf("outcomes=%v", counts)
	}
}
