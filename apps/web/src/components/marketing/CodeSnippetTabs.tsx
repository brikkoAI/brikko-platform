'use client';

import { useState } from 'react';
import { Check, Copy } from 'lucide-react';

/**
 * CodeSnippetTabs — Sprint 10 P0, ICE 9.3, 0.5 дня.
 *
 * Цель: снять «страх первого подключения» прямо на лендинге.
 * AITunnel и Helicone делают это — у нас не было.
 *
 * UX-обоснование:
 *   - 4 таба (Python / Node.js / cURL / Go) — самые популярные у разработчиков
 *     наша ICP (B2B SaaS / agency)
 *   - Кнопка «Скопировать» с feedback (галочка на 1.5 секунды)
 *   - Размещение: между Features и SavingsCalculator
 *     («посмотрел продукт → код одной кнопкой → калькулятор → ценность»)
 *   - Подсветка через grayscale + brand-цвет для ключевых слов;
 *     полноценный prism/highlight.js — отдельная задача в Sprint 11 (overhead +50KB)
 */

type TabKey = 'python' | 'node' | 'curl' | 'go';

const SNIPPETS: Record<TabKey, { label: string; code: string }> = {
  python: {
    label: 'Python',
    code: `from openai import OpenAI

client = OpenAI(
    api_key="sk-brk-...",                      # твой ключ из кабинета
    base_url="https://api.brikko.ru/v1",       # одна строка вместо OpenAI
)

response = client.chat.completions.create(
    model="auto:cheap",                        # умный рутер выберет модель
    messages=[{"role": "user", "content": "Привет!"}],
)

print(response.choices[0].message.content)`,
  },
  node: {
    label: 'Node.js',
    code: `import OpenAI from 'openai';

const client = new OpenAI({
  apiKey: 'sk-brk-...',                        // твой ключ из кабинета
  baseURL: 'https://api.brikko.ru/v1',         // одна строка вместо OpenAI
});

const response = await client.chat.completions.create({
  model: 'auto:cheap',                         // умный рутер выберет модель
  messages: [{ role: 'user', content: 'Привет!' }],
});

console.log(response.choices[0].message.content);`,
  },
  curl: {
    label: 'cURL',
    code: `curl https://api.brikko.ru/v1/chat/completions \\
  -H "Authorization: Bearer sk-brk-..." \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "auto:cheap",
    "messages": [{"role": "user", "content": "Привет!"}]
  }'`,
  },
  go: {
    label: 'Go',
    code: `package main

import (
    "context"
    "fmt"

    openai "github.com/sashabaranov/go-openai"
)

func main() {
    cfg := openai.DefaultConfig("sk-brk-...")
    cfg.BaseURL = "https://api.brikko.ru/v1"   // одна строка вместо OpenAI
    client := openai.NewClientWithConfig(cfg)

    resp, _ := client.CreateChatCompletion(
        context.Background(),
        openai.ChatCompletionRequest{
            Model: "auto:cheap",
            Messages: []openai.ChatCompletionMessage{
                {Role: openai.ChatMessageRoleUser, Content: "Привет!"},
            },
        },
    )

    fmt.Println(resp.Choices[0].Message.Content)
}`,
  },
};

const TAB_ORDER: TabKey[] = ['python', 'node', 'curl', 'go'];

export function CodeSnippetTabs() {
  const [active, setActive] = useState<TabKey>('python');
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(SNIPPETS[active].code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // navigator.clipboard может быть недоступен (старые браузеры) — silent fail
    }
  };

  return (
    <section className="bg-white py-16 lg:py-24" aria-labelledby="quickstart-heading">
      <div className="mx-auto max-w-4xl px-6">
        <div className="text-center">
          <h2
            id="quickstart-heading"
            className="text-3xl font-semibold tracking-tight text-gray-900"
          >
            Подключение — 5 минут
          </h2>
          <p className="mt-3 text-body-large text-gray-700">
            Замени <code className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-body-sm text-gray-900">
              base_url
            </code>{' '}
            и API-ключ — остальной код остаётся тем же. Полная совместимость с OpenAI SDK.
          </p>
        </div>

        <div className="mt-10 overflow-hidden rounded-lg border border-gray-200 bg-gray-50 shadow-sm">
          {/* Tabs row + copy button */}
          <div className="flex items-center justify-between border-b border-gray-200 bg-white px-2 sm:px-4">
            <div role="tablist" aria-label="Выбор языка" className="flex gap-1">
              {TAB_ORDER.map((key) => (
                <button
                  key={key}
                  role="tab"
                  aria-selected={active === key}
                  aria-controls={`snippet-${key}`}
                  type="button"
                  onClick={() => setActive(key)}
                  className={`relative px-3 py-3 text-body-sm font-medium transition-colors sm:px-4 ${
                    active === key
                      ? 'text-brand-700'
                      : 'text-gray-500 hover:text-gray-900'
                  }`}
                >
                  {SNIPPETS[key].label}
                  {active === key ? (
                    <span
                      aria-hidden="true"
                      className="absolute inset-x-2 -bottom-px h-0.5 bg-brand-600"
                    />
                  ) : null}
                </button>
              ))}
            </div>

            <button
              type="button"
              onClick={handleCopy}
              className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-body-sm font-medium text-gray-600 transition-colors hover:bg-gray-100 hover:text-gray-900"
              aria-label={copied ? 'Скопировано' : 'Скопировать в буфер обмена'}
            >
              {copied ? (
                <>
                  <Check className="h-4 w-4 text-success-600" strokeWidth={2} aria-hidden="true" />
                  <span className="text-success-600">Скопировано</span>
                </>
              ) : (
                <>
                  <Copy className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                  <span>Скопировать</span>
                </>
              )}
            </button>
          </div>

          {/* Snippet body */}
          <div
            role="tabpanel"
            id={`snippet-${active}`}
            aria-labelledby={`snippet-tab-${active}`}
          >
            <pre className="overflow-x-auto p-4 text-body-sm leading-relaxed text-gray-900 sm:p-6">
              <code className="font-mono">{SNIPPETS[active].code}</code>
            </pre>
          </div>
        </div>

        <p className="mt-6 text-center text-body-sm text-gray-500">
          Стриминг работает, формат запроса/ответа идентичен. Большинство проектов мигрируют за 5-10 минут.
        </p>
      </div>
    </section>
  );
}
