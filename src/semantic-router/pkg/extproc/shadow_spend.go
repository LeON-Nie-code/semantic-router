package extproc

import (
	"math"

	"github.com/vllm-project/semantic-router/src/semantic-router/pkg/config"
	"github.com/vllm-project/semantic-router/src/semantic-router/pkg/llmprotocol"
	"github.com/vllm-project/semantic-router/src/semantic-router/pkg/spendbudget"
)

type shadowSpend struct {
	prepared   *preparedShadowCall
	pricing    config.ModelPricing
	currency   string
	usage      responseUsageMetrics
	estimate   float64
	charged    float64
	settlement string
	dispatched bool
}

func (d *shadowDispatcher) reserveShadowGroup(jobs []*shadowJob, limit float64) *spendbudget.Hold {
	if jobs[0].context.Err() != nil {
		for _, job := range jobs {
			d.recordUnstarted(job)
		}
		return nil
	}
	total := 0.0
	for _, job := range jobs {
		spend, reason, err := d.estimateShadowSpend(job)
		if reason != "" {
			d.recordShadowBudgetRefusal(jobs, reason, err)
			return nil
		}
		job.spend = spend
		total += spend.estimate
	}
	if math.IsInf(spendbudget.Round(total), 0) {
		d.recordShadowBudgetRefusal(jobs, shadowReasonBudgetExhausted, nil)
		return nil
	}
	// A ledger for this group keeps background spend out of the primary
	// request’s user and session accounts.
	hold, refusal := spendbudget.NewLedger().Reserve([]spendbudget.Limit{{Key: "shadow", Scope: "shadow", Amount: limit}}, spendbudget.Round(total))
	if refusal != nil {
		d.recordShadowBudgetRefusal(jobs, shadowReasonBudgetExhausted, nil)
		return nil
	}
	return hold
}

func (d *shadowDispatcher) estimateShadowSpend(job *shadowJob) (*shadowSpend, string, error) {
	currency := job.routerConfig.Budgets.EffectiveCurrency()
	pricing, ok := job.routerConfig.GetFullModelPricing(job.model)
	if !ok || normalizeReplayCurrency(pricing.Currency) != currency {
		return nil, shadowReasonBudgetUnpriced, nil
	}
	prepared, reason, err := d.prepareShadowCall(job)
	if err != nil {
		return nil, reason, err
	}
	// Use the bound on the encoded request. A default spend estimate alone
	// cannot limit what the backend actually generates.
	request, _, _, err := job.engine.DecodeRequest(prepared.target.format, prepared.body)
	if err != nil {
		return nil, shadowReasonBudgetUnbounded, err
	}
	output := request.Sampling.MaxOutputTokens
	if output == nil || *output <= 0 || (request.CandidateCount != nil && *request.CandidateCount > 1) {
		return nil, shadowReasonBudgetUnbounded, nil
	}
	inputRate := max(pricing.PromptPer1M, effectiveCacheWriteRate(pricing), effectiveCacheWrite1hRate(pricing))
	estimate := float64(llmprotocol.EstimateInput(&request).Tokens)*(inputRate/1e6) + float64(*output)*(pricing.CompletionPer1M/1e6)
	if estimate < 0 || math.IsNaN(estimate) || math.IsInf(estimate, 0) {
		return nil, shadowReasonBudgetUnpriced, nil
	}
	return &shadowSpend{prepared: prepared, pricing: pricing, currency: currency, estimate: estimate, settlement: "released"}, "", nil
}

func (d *shadowDispatcher) recordShadowBudgetRefusal(jobs []*shadowJob, reason string, err error) {
	for _, job := range jobs {
		job.spend = nil
		d.record(job, d.failShadow(shadowResult{startedAt: d.now()}, reason, err))
	}
}

func (s *shadowSpend) settle(result shadowResult) {
	if result.inputKnown && result.outputKnown {
		s.charged = costForResponseUsage(s.usage, s.pricing)
		s.settlement = "usage"
	} else if s.dispatched {
		s.charged = s.estimate
		s.settlement = "estimate"
	}
}

func settleShadowGroup(hold *spendbudget.Hold, jobs []*shadowJob) {
	charged := 0.0
	for _, job := range jobs {
		charged += job.spend.charged
	}
	hold.Settle(charged)
}
