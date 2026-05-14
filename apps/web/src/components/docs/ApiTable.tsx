/**
 * ApiTable — таблица параметров API endpoint'а.
 *
 * UX-обоснование:
 *   - Структура: Field / Type / Default / Description. Это формат, к которому
 *     разработчики привыкли (FastAPI swagger, Stripe API, OpenAI docs).
 *     Не выдумываем своё, узнаваемое > креативное.
 *   - Type — mono-шрифт. Имя поля — mono. Description — обычный шрифт.
 *     Это создаёт правильную визуальную иерархию: «техническое» / «человеческое».
 *   - required-pill справа от поля — заметно даже при беглом сканировании.
 *     Альтернатива (asterisk *) хуже: сливается с символами в типе вроде
 *     `string | null`.
 *   - Mobile: горизонтальный scroll вместо stacked-layout. Stacked-layout
 *     мешает сравнивать поля между собой (главная задача такой таблицы).
 *
 * Использование:
 *   <ApiTable rows={[
 *     { field: 'text', type: 'string', required: true, description: '...' },
 *     { field: 'ttl_seconds', type: 'int', default: '3600', description: '...' },
 *   ]} />
 */

export interface ApiRow {
  field: string;
  type: string;
  required?: boolean;
  default?: string;
  description: React.ReactNode;
}

interface Props {
  rows: ReadonlyArray<ApiRow>;
}

export function ApiTable({ rows }: Props) {
  return (
    <div className="mt-6 overflow-x-auto rounded-2xl border border-[var(--hairline)] bg-[var(--bg-base)]">
      <table className="w-full border-collapse text-body-sm">
        <thead className="bg-[var(--bg-elevated)] text-left text-fg-primary">
          <tr>
            <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
              Поле
            </th>
            <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
              Тип
            </th>
            <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
              По умолчанию
            </th>
            <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
              Описание
            </th>
          </tr>
        </thead>
        <tbody className="text-fg-muted">
          {rows.map((r, i) => {
            const isLast = i === rows.length - 1;
            const cellClass = isLast
              ? 'px-4 py-3 align-top'
              : 'border-b border-[var(--hairline)] px-4 py-3 align-top';
            return (
              <tr key={r.field}>
                <td className={cellClass}>
                  <div className="flex flex-wrap items-center gap-2">
                    <code className="font-mono text-body-sm font-semibold text-fg-primary">
                      {r.field}
                    </code>
                    {r.required ? (
                      <span className="brikko-pill">required</span>
                    ) : null}
                  </div>
                </td>
                <td className={cellClass}>
                  <code className="font-mono text-body-sm">{r.type}</code>
                </td>
                <td className={cellClass}>
                  {r.default ? (
                    <code className="font-mono text-body-sm text-fg-faint">
                      {r.default}
                    </code>
                  ) : (
                    <span className="text-fg-faint">—</span>
                  )}
                </td>
                <td className={cellClass}>{r.description}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
