package extproc

import (
	"math"

	"github.com/vllm-project/semantic-router/src/semantic-router/pkg/config"
)

type shadowSpend struct {
	prepared   *preparedShadowCall
	pricing    config.ModelPricing
	currency   string
	usage      responseUsageMetrics
	estimate   float64
	accounted  float64
	costSource string
	dispatched bool
}

func (d *shadowDispatcher) admitShadowGroup(jobs []*shadowJob, limit float64) bool {
	if jobs[0].context.Err() != nil {
		for _, job := range jobs {
			d.recordUnstarted(job)
		}
		return false
	}
	total := 0.0
	for _, job := range jobs {
		spend, reason, err := d.estimateShadowSpend(job)
		if reason != "" {
			d.recordShadowBudgetRefusal(jobs, reason, err)
			return false
		}
		job.spend = spend
		total += spend.estimate
	}
	if total > limit {
		d.recordShadowBudgetRefusal(jobs, shadowReasonBudgetExhausted, nil)
		return false
	}
	return true
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
	estimate := estimateDispatchCost(&request, *output, pricing)
	if estimate < 0 || math.IsNaN(estimate) || math.IsInf(estimate, 0) {
		return nil, shadowReasonBudgetUnpriced, nil
	}
	return &shadowSpend{prepared: prepared, pricing: pricing, currency: currency, estimate: estimate, costSource: "not_dispatched"}, "", nil
}

func (d *shadowDispatcher) recordShadowBudgetRefusal(jobs []*shadowJob, reason string, err error) {
	for _, job := range jobs {
		job.spend = nil
		d.record(job, d.failShadow(shadowResult{startedAt: d.now()}, reason, err))
	}
}

func (s *shadowSpend) account(result shadowResult) {
	if result.inputKnown && result.outputKnown {
		s.accounted = costForResponseUsage(s.usage, s.pricing)
		s.costSource = "usage"
	} else if s.dispatched {
		s.accounted = s.estimate
		s.costSource = "estimate"
	}
}
