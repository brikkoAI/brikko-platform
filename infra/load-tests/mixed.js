// LT-5 — Mixed realistic traffic. 200 RPM × 30 мин с реалистичной пропорцией.
//
// Распределение (на основе ожиданий MVP):
//   60% non-stream auto:cheap        (curl, скрипты, простой fanout)
//   25% stream auto:smart            (chatbots)
//   10% non-stream pinned model      (прибитые модели для воспроизводимости)
//    3% non-stream auto:ru-legal     (152-FZ-чувствительные)
//    2% non-stream long-context      (RAG, summarization)

import http from 'k6/http';
import { check } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorByRoute = new Rate('errors_by_route');
const latencyByRoute = new Trend('latency_by_route', true);

export const options = {
  scenarios: {
    mixed: {
      executor: 'constant-arrival-rate',
      rate: 200,
      timeUnit: '1m',
      duration: '30m',
      preAllocatedVUs: 50,
      maxVUs: 200,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.001'],
    'errors_by_route{route:cheap}': ['rate<0.001'],
    'errors_by_route{route:smart}': ['rate<0.005'],
    'errors_by_route{route:ru-legal}': ['rate<0.005'],
    'errors_by_route{route:long-ctx}': ['rate<0.005'],
    http_req_duration: ['p(95)<5000'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'https://staging-api.brikko.ru';
const API_KEY = __ENV.API_KEY;
if (!API_KEY) throw new Error('API_KEY env var required');

const LONG_CTX_PROMPT = 'Summarize: ' + 'Lorem ipsum dolor sit amet. '.repeat(800); // ~10k tokens

function pickRoute() {
  const r = Math.random();
  if (r < 0.60) return 'cheap';
  if (r < 0.85) return 'smart';
  if (r < 0.95) return 'pinned';
  if (r < 0.98) return 'ru-legal';
  return 'long-ctx';
}

function buildPayload(route) {
  switch (route) {
    case 'cheap':
      return { model: 'auto:cheap', messages: [{ role: 'user', content: 'one word answer: yes or no' }], max_tokens: 5 };
    case 'smart':
      return { model: 'auto:smart', messages: [{ role: 'user', content: 'explain quantum computing in one sentence' }], max_tokens: 50, stream: true };
    case 'pinned': {
      const models = ['gpt-5.4-mini', 'claude-haiku-4.5', 'gemini-3-flash'];
      return { model: models[Math.floor(Math.random() * models.length)], messages: [{ role: 'user', content: 'ping' }], max_tokens: 5 };
    }
    case 'ru-legal':
      return { model: 'auto:ru-legal', messages: [{ role: 'user', content: 'Скажи привет на русском' }], max_tokens: 10 };
    case 'long-ctx':
      return { model: 'auto:smart', messages: [{ role: 'user', content: LONG_CTX_PROMPT }], max_tokens: 100 };
  }
}

export default function () {
  const route = pickRoute();
  const payload = buildPayload(route);
  const isStream = payload.stream === true;

  const t0 = Date.now();
  const r = http.post(`${BASE_URL}/v1/chat/completions`, JSON.stringify(payload), {
    headers: {
      'Authorization': `Bearer ${API_KEY}`,
      'Content-Type': 'application/json',
      'Accept': isStream ? 'text/event-stream' : 'application/json',
    },
    timeout: '30s',
    tags: { route },
  });
  const dt = Date.now() - t0;

  latencyByRoute.add(dt, { route });
  const ok = r.status === 200;
  errorByRoute.add(!ok, { route });

  check(r, {
    'has X-Router-Decision': (resp) => !!resp.headers['X-Router-Decision'],
    'status 200': (resp) => resp.status === 200,
  });
}
