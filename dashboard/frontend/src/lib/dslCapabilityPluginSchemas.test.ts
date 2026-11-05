import { describe, expect, it } from 'vitest'

import { resolveCapabilityPluginFieldSchema } from './dslCapabilityPluginSchemas'

describe('capability plugin field schemas', () => {
  it('exposes the complete context compression contract', () => {
    const fields = resolveCapabilityPluginFieldSchema('context_compression')
    expect(fields?.map((field) => field.key)).toEqual([
      'enabled',
      'mode',
      'budget',
      'targets',
      'scoring',
      'recovery',
      'request_controls',
      'failure_mode',
    ])
    expect(fields?.find((field) => field.key === 'targets')?.type).toBe('object')
  })

  it('exposes only the unified shadow dispatch contract', () => {
    const fields = resolveCapabilityPluginFieldSchema('shadow_dispatch')
    expect(fields?.map((field) => field.key)).toEqual([
      'enabled',
      'models',
      'sample_rate',
      'execution',
      'budget',
      'capture',
    ])
    expect(fields?.find((field) => field.key === 'models')?.type).toBe('string[]')
    expect(fields?.find((field) => field.key === 'sample_rate')).toMatchObject({
      type: 'number',
      min: 0,
      max: 1,
      placeholder: '1',
    })
    const execution = fields?.find((field) => field.key === 'execution')
    expect(execution?.type).toBe('object')
    expect(execution?.fields?.map((field) => [field.key, field.type, field.placeholder])).toEqual([
      ['max_concurrency', 'number', '2'],
      ['max_queue_size', 'number', '8'],
      ['timeout', 'string', '30s'],
    ])
    const budget = fields?.find((field) => field.key === 'budget')
    expect(budget?.type).toBe('object')
    expect(budget?.fields?.map((field) => field.key)).toEqual(['max_calls', 'max_response_bytes'])
    const capture = fields?.find((field) => field.key === 'capture')
    expect(capture?.type).toBe('object')
    expect(capture?.fields?.map((field) => [field.key, field.type])).toEqual([
      ['enabled', 'boolean'],
      ['max_bytes', 'number'],
    ])
    for (const section of [execution, budget, capture]) {
      for (const field of section?.fields ?? []) {
        if (field.type === 'number') expect(field.min).toBe(1)
      }
    }
  })

  it('exposes canonical response cache fields', () => {
    const fields = resolveCapabilityPluginFieldSchema('response_cache')
    expect(fields?.map((field) => field.key)).toEqual([
      'enabled',
      'mode',
      'scope',
      'semantic',
      'ttl_seconds',
      'request_controls',
      'personalized',
      'revision',
    ])
  })

  it('exposes the prompt cache marker contract', () => {
    const fields = resolveCapabilityPluginFieldSchema('prompt_cache')
    expect(fields?.map((field) => field.key)).toEqual([
      'enabled',
      'ttl',
      'targets',
      'on_unsupported',
    ])
  })

  it.each(['constructor', 'toString', '__proto__'])('rejects prototype key %s', (pluginType) => {
    expect(resolveCapabilityPluginFieldSchema(pluginType)).toBeNull()
  })
})
