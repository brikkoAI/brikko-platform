// LT-1 — Baseline load test for Voltari gateway.
// Goal: 200 RPM sustained for 10 minutes; p50 <200ms, p95 <500ms, errors <0.1%.
// Target: staging gateway with mock-provider (no real upstream cost).
//
// Usage:
//   k6 run -e BASE_URL=https://staging-api.brikko.ru \
//          -e API_KEY=$STAGING_LOAD_KEY \
//          baseline.js
//
// Tip: запускать с отдельного VPS, чтобы k6 не съел CPU тестируемой системы.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('voltari_errors');
const routerDecisionPresent = new Rate('router_decision_present');
const gatewayOverhead = new Trend('gateway_overhead_ms', true);

export const options = {
  scenarios: {
    baseline: {
      executor: 'ramping-arrival-rate',
      startRate: 0,
      timeUnit: '1m',          // RPM units
      preAllocatedVUs: 50,
      maxVUs: 200,
      stages: [
        { duration: '1m', target: 200 },   // ramp 0 → 200 RPM
        { duration: '8m', target: 200 },   // sustain
        { duration: '1m', target: 0 },     // ramp down
      ],
    },
  },
  thresholds: {
    http_req_duration: ['p(50)<200', 'p(95)<500', 'p(99)<1500'],
    http_req_failed: ['rate<0.001'],          // < 0.1%
    voltari_errors: ['rate<0.001'],
    router_decision_present: ['rate>0.99'],   // header в 99%+ ответов
    gateway_overhead_ms: ['p(95)<500'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'https://staging-api.brikko.ru';
const API_KEY = __ENV.API_KEY;

if (!API_KEY) {
  throw new Error('API_KEY env var required');
}

const PROMPTS = [
  'say ok',
  'what is 2+2?',
  'translate "hello" to russian',
  'one word answer: capital of france',
  'reply with just the word "yes"',
];

export default function () {
  const prompt = PROMPTS[Math.floor(Math.random() * PROMPTS.length)];
  const payload = JSON.stringify({
    model: 'auto:cheap',
    messages: [{ role: 'user', content: prompt }],
    max_tokens: 10,
  });

  const params = {
    headers: {
      'Authorization': `Bearer ${API_KEY}`,
      'Content-Type': 'application/json',
    },
    timeout: '30s',
    tags: { endpoint: '/v1/chat/completions' },
  };

  const t0 = Date.now();
  const r = http.post(`${BASE_URL}/v1/chat/completions`, payload, params);
  const dt = Date.now() - t0;

  // gateway overhead = total - upstream_latency_ms (заголовок выставляется gateway'ем).
  // Если заголовок отсутствует — считаем total как overhead (хуже для нас, ок для gating).
  const upstreamMs = parseInt(r.headers['X-Upstream-Latency-Ms'] || '0', 10);
  const overhead = Math.max(0, dt - upstreamMs);
  gatewayOverhead.add(overhead);

  const ok = check(r, {
    'status 200': (resp) => resp.status === 200,
    'has choices': (resp) => {
      try { return JSON.parse(resp.body).choices?.[0]?.message?.content?.length > 0; }
      catch { return false; }
    },
    'has X-Request-Id': (resp) => !!resp.headers['X-Request-Id'],
  });

  errorRate.add(!ok);
  routerDecisionPresent.add(!!r.headers['X-Router-Decision']);

  // Без sleep — k6 arrival-rate сам контролирует темп.
}

export function handleSummary(data) {
  return {
    'stdout': textSummary(data),
    'baseline-summary.json': JSON.stringify(data, null, 2),
  };
}

function textSummary(data) {
  const m = data.metrics;
  const p50 = m.http_req_duration?.values?.['p(50)']?.toFixed(0) || '?';
  const p95 = m.http_req_duration?.values?.['p(95)']?.toFixed(0) || '?';
  const p99 = m.http_req_duration?.values?.['p(99)']?.toFixed(0) || '?';
  const failed = ((m.http_req_failed?.values?.rate || 0) * 100).toFixed(3);
  const overheadP95 = m.gateway_overhead_ms?.values?.['p(95)']?.toFixed(0) || '?';
  return `
=== LT-1 Baseline Summary ===
HTTP duration: p50=${p50}ms p95=${p95}ms p99=${p99}ms
Gateway overhead p95: ${overheadP95}ms
Errors: ${failed}%
Total requests: ${m.http_reqs?.values?.count || 0}
=============================
`;
}
