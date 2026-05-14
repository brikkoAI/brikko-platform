// LT-2 — Spike test. 0 → 1000 RPM за 30 сек, sustain 5 мин, drop.
// Goal: проверить что внезапный спайк не убивает gateway; нет 5xx после 1 мин sustain.

import http from 'k6/http';
import { check } from 'k6';
import { Rate } from 'k6/metrics';

const fiveXX = new Rate('http_5xx');

export const options = {
  scenarios: {
    spike: {
      executor: 'ramping-arrival-rate',
      startRate: 0,
      timeUnit: '1m',
      preAllocatedVUs: 100,
      maxVUs: 500,
      stages: [
        { duration: '30s', target: 1000 },  // sharp ramp
        { duration: '5m',  target: 1000 },  // sustain
        { duration: '30s', target: 0 },     // drop
      ],
    },
  },
  thresholds: {
    // На спайке допускаем большую деградацию, но не отказ.
    http_req_failed: ['rate<0.005'],            // < 0.5%
    http_req_duration: ['p(95)<2000'],          // 2 сек на спайке — ок
    'http_5xx{phase:sustain}': ['rate<0.001'],  // 5xx после первой минуты — недопустимо
  },
};

const BASE_URL = __ENV.BASE_URL || 'https://staging-api.brikko.ru';
const API_KEY = __ENV.API_KEY;
if (!API_KEY) throw new Error('API_KEY env var required');

let testStartTs;
export function setup() {
  return { startTs: Date.now() };
}

export default function (data) {
  const elapsedSec = (Date.now() - data.startTs) / 1000;
  // sustain phase = после 30 сек ramp + 60 сек прогрева
  const phase = elapsedSec >= 90 ? 'sustain' : 'rampup';

  const payload = JSON.stringify({
    model: 'auto:cheap',
    messages: [{ role: 'user', content: 'ok' }],
    max_tokens: 5,
  });

  const r = http.post(`${BASE_URL}/v1/chat/completions`, payload, {
    headers: {
      'Authorization': `Bearer ${API_KEY}`,
      'Content-Type': 'application/json',
    },
    timeout: '30s',
    tags: { phase },
  });

  fiveXX.add(r.status >= 500 && r.status < 600, { phase });

  check(r, {
    'not 5xx': (resp) => resp.status < 500 || resp.status >= 600,
  });
}
