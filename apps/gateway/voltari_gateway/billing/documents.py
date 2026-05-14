"""Comply Pack — auto-generation of РФ закрывающих документов (Sprint 4 Поток M).

What we generate:

* **Акт выполненных работ** (per-transaction) — for one TOPUP/CHARGE row.
* **Сводный счёт** (period invoice) — aggregated charges between [from, to].
* **УПД** — universal transfer document, per-period, conventionally bundles
  ставку НДС=20% (gateway is service, not goods, but customers' accounting
  systems require the form anyway).

PDF stack: ``fpdf2`` (pure-Python, ~200 KB, Alpine-friendly). We use core
PDF fonts (Helvetica) — those are baked into every PDF reader, no font
file shipped. Cyrillic text is rendered via the bundled DejaVu Sans which
``fpdf2`` ships out-of-the-box (``add_font(... uni=True)``).

WHY NOT ReportLab / WeasyPrint:

* WeasyPrint pulls Cairo / Pango — extra ~50 MB on Alpine, not worth it
  for three-form generator.
* ReportLab is acceptable but ~3 MB and licensing is BSD-with-restrictions
  on the chart toolkit; we need text only.

Каждый документ — A4, single page, header (Voltari brand + customer
requisites), table-style body, signature placeholder at bottom.

Legal disclaimer: документы — first draft for the customer's bookkeeping
to verify; gateway does NOT certify them as exhaustive substitutes for
manual оформление по 402-ФЗ. We log this on every issuance so the audit
trail shows "issued by Voltari at TS, customer reviewed manually".
"""

from __future__ import annotations

import io
import os
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from voltari_gateway.db.models import Transaction, TransactionKind

# ---------- legal thresholds -------------------------------------------------
#
# Sprint 8 — explicit minimum-amount thresholds for закрывающие документы.
# Below these thresholds the documents are not legally meaningful and
# accountants reject them (sub-1000₽ акт is line-noise on a quarterly
# invoice; sub-10000₽ УПД carries no НДС break-out value). They were
# previously hard-coded in API handlers as "magic numbers"; surfacing them
# here lets ops/legal change a policy in one place and lets tests assert
# the public constants instead of the business numbers.
#
# Source: 06_Operations/legal_documents_policy.md (Brikko ops handbook,
# 2026-04-30) + CEO 30.04 §10 #6.

# Акт выполненных работ: per-transaction, выдаётся с 1000 ₽.
AKT_MIN_AMOUNT_KOPECKS: int = 1_000_00

# УПД: per-transaction (или агрегация за период), выдаётся с 10000 ₽.
UPD_MIN_AMOUNT_KOPECKS: int = 10_000_00


# ---------- types ------------------------------------------------------------


@dataclass(frozen=True)
class CustomerInfo:
    """Customer side of the document — what we know about them.

    For full легальность the customer's INN/full-name/address must come
    from the account profile (BRIEF.md TD-001). When fields are missing we
    render placeholders ``<укажите …>``; the document still issues so the
    customer can fill manually before signing.
    """

    full_name: str  # ИП/ООО name OR physical-person name
    inn: str | None = None
    kpp: str | None = None
    address: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class GatewayInfo:
    """Voltari side — comes from settings.brand_* + future TD-001 fields."""

    brand_name: str = "Brikko"
    legal_entity: str = "<ИП [Фамилия Имя Отчество]>"  # TD-001 placeholder; updated post-launch
    inn: str = "<ИНН Brikko>"
    address: str = "<юридический адрес Brikko>"
    email: str = "billing@brikko.ru"


@dataclass(frozen=True)
class AktLine:
    """One row in акт-таблице.

    For per-transaction акт this is a single row ("услуги LLM-шлюза за
    период с …"); for period-invoice we group by model / day / etc.
    """

    description: str
    quantity: int
    unit: str  # "шт.", "запросов", etc.
    price_kop: int  # per unit, for display
    total_kop: int


# ---------- PDF helpers ------------------------------------------------------


def _kop_to_rub_str(kop: int) -> str:
    """Format kopecks as "1 234,56 ₽" (РФ convention: comma decimal,
    NBSP thousand-sep). The output goes into PDF rendering — fpdf2 handles
    NBSP fine in DejaVu / core fonts."""
    rubles, kopecks = divmod(abs(kop), 100)
    sign = "-" if kop < 0 else ""
    s = f"{rubles:,}".replace(",", " ")
    return f"{sign}{s},{kopecks:02d} RUB"


def _short_uuid(u: uuid.UUID) -> str:
    """Document number from UUID — first 8 hex chars uppercased."""
    return str(u)[:8].upper()


def _find_unicode_font() -> tuple[Path, Path] | None:
    """Search known system locations for a Cyrillic-capable TTF + bold variant.

    Returns ``(regular_path, bold_path)`` on first match, else ``None``.

    Search order:

    1. ``VOLTARI_PDF_FONT_DIR`` env var (op-controlled override).
    2. Common Linux/Alpine paths (``ttf-dejavu`` on Alpine puts files in
       ``/usr/share/fonts/dejavu/``).
    3. Windows Fonts dir (Arial supports Cyrillic — used in dev).
    4. macOS — Supplemental fonts.

    Bold path may equal regular path on systems with no bold variant —
    fpdf2 will render bold via emulation in that case (uglier, still works).
    """
    candidates: list[tuple[Path, Path]] = []

    env_dir = os.environ.get("VOLTARI_PDF_FONT_DIR")
    if env_dir:
        d = Path(env_dir)
        candidates.append((d / "DejaVuSans.ttf", d / "DejaVuSans-Bold.ttf"))

    # Alpine + Debian/Ubuntu DejaVu locations.
    candidates.extend(
        [
            (
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            ),
            (
                Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
                Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
            ),
        ]
    )
    # Windows Arial (always Cyrillic-capable since Vista).
    win_fonts = Path("C:/Windows/Fonts")
    candidates.append((win_fonts / "arial.ttf", win_fonts / "arialbd.ttf"))
    # macOS Arial (Cyrillic support varies; Helvetica does not — prefer Arial).
    mac_fonts = Path("/System/Library/Fonts/Supplemental")
    candidates.append((mac_fonts / "Arial.ttf", mac_fonts / "Arial Bold.ttf"))

    for reg, bold in candidates:
        if reg.exists():
            return reg, bold if bold.exists() else reg
    return None


class _VoltariPdf(FPDF):
    """FPDF subclass with our paper / font / margins.

    Font strategy: search system fonts for a Cyrillic-capable TTF.

    * **Production (Alpine Docker)** — DejaVu Sans bundled via ``apk add
      ttf-dejavu`` in the Dockerfile (TD-045 closed Sprint 5). Files live at
      ``/usr/share/fonts/dejavu/DejaVuSans*.ttf``.
    * **Dev (Windows)** — Arial из ``C:/Windows/Fonts`` (Cyrillic-capable
      since Vista).
    * **Dev (macOS)** — Arial из ``/System/Library/Fonts/Supplemental/``.

    Если ни один Unicode-шрифт не найден (редкий edge — sandboxed runtime,
    custom slim image), fallback на **транслитерацию**: Cyrillic → ASCII
    через GOST 7.79 перед передачей в fpdf2's Helvetica. Документ всё равно
    создаётся; бухгалтер видит латинскую транскрипцию (которую парсят
    бухпрограммы, хоть это и не идеально визуально).
    """

    def __init__(self) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_auto_page_break(auto=True, margin=15)
        self.set_margins(left=15, top=15, right=15)
        font_paths = _find_unicode_font()
        self._transliterate = False
        if font_paths is not None:
            reg, bold = font_paths
            try:
                self.add_font("VoltariSans", style="", fname=str(reg))
                self.add_font("VoltariSans", style="B", fname=str(bold))
                self._font = "VoltariSans"
            except Exception:
                self._font = "Helvetica"
                self._transliterate = True
        else:
            self._font = "Helvetica"
            self._transliterate = True
        self.set_font(self._font, size=10)

    def _txt(self, s: str) -> str:
        """Apply transliteration when no Unicode font is loaded."""
        if not self._transliterate:
            return s
        return _transliterate_ru(s)

    def line_cell(
        self,
        w: float,
        h: float,
        text: str,
        *,
        border: int | str = 0,
        align: str = "L",
    ) -> None:
        """``cell(... ln=True)`` replacement using the post-2.5.2 API.

        Centralised so we don't sprinkle ``new_x=``/``new_y=`` literals
        through 20 call sites. Behaves like ``cell`` but moves the cursor
        to the next line at column 0 (left margin) afterwards — matching
        the historical ``ln=True`` semantics.
        """
        # fpdf2 typed border as ``Literal[0, 1] | str``; we accept the
        # broader ``int | str`` and pass through. Real values are 0 or 1.
        self.cell(
            w,
            h,
            text,
            border=border,  # type: ignore[arg-type]
            align=align,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

    def header_block(self, gateway: GatewayInfo, doc_title: str, doc_no: str) -> None:
        """Top of every doc: brand + doc title + number."""
        self.set_font(self._font, style="B", size=14)
        self.line_cell(0, 8, self._txt(f"{gateway.brand_name}"))
        self.set_font(self._font, style="", size=8)
        self.line_cell(0, 5, self._txt(f"{gateway.legal_entity}, ИНН {gateway.inn}"))
        self.line_cell(0, 5, self._txt(gateway.address))
        self.line_cell(0, 5, self._txt(gateway.email))
        self.ln(4)

        self.set_font(self._font, style="B", size=12)
        self.line_cell(0, 7, self._txt(doc_title))
        self.set_font(self._font, style="", size=9)
        self.line_cell(0, 5, self._txt(f"№ {doc_no} от {datetime.now(UTC).strftime('%d.%m.%Y')}"))
        self.ln(4)

    def customer_block(self, customer: CustomerInfo) -> None:
        self.set_font(self._font, style="B", size=10)
        self.line_cell(0, 6, self._txt("Заказчик:"))
        self.set_font(self._font, style="", size=9)
        self.line_cell(0, 5, self._txt(customer.full_name))
        if customer.inn:
            line = f"ИНН {customer.inn}" + (f", КПП {customer.kpp}" if customer.kpp else "")
            self.line_cell(0, 5, self._txt(line))
        else:
            self.line_cell(0, 5, self._txt("<укажите ИНН / КПП>"))
        self.line_cell(0, 5, self._txt(customer.address or "<укажите юридический адрес>"))
        self.ln(4)

    def lines_table(self, lines: Sequence[AktLine]) -> None:
        """Table: №, описание, кол-во, ед., цена, сумма."""
        self.set_font(self._font, style="B", size=9)
        widths = [10, 80, 18, 18, 28, 30]  # mm
        headers = ["№", "Описание", "Кол.", "Ед.", "Цена", "Сумма"]
        for w, h in zip(widths, headers, strict=True):
            self.cell(w, 7, self._txt(h), border=1, align="C")
        self.ln()
        self.set_font(self._font, style="", size=9)
        total = 0
        for i, line in enumerate(lines, start=1):
            self.cell(widths[0], 6, str(i), border=1, align="R")
            # Truncate long descriptions to fit the cell width.
            self.cell(widths[1], 6, self._txt(line.description[:60]), border=1)
            self.cell(widths[2], 6, str(line.quantity), border=1, align="R")
            self.cell(widths[3], 6, self._txt(line.unit), border=1, align="C")
            self.cell(widths[4], 6, _kop_to_rub_str(line.price_kop), border=1, align="R")
            self.cell(widths[5], 6, _kop_to_rub_str(line.total_kop), border=1, align="R")
            self.ln()
            total += line.total_kop
        # Total row.
        self.set_font(self._font, style="B", size=9)
        self.cell(sum(widths[:5]), 7, self._txt("Итого:"), border=1, align="R")
        self.cell(widths[5], 7, _kop_to_rub_str(total), border=1, align="R")
        self.ln(10)

    def signature_block(self) -> None:
        self.set_font(self._font, style="", size=9)
        self.cell(95, 6, self._txt("Исполнитель: __________________"), border=0)
        self.cell(95, 6, self._txt("Заказчик: __________________"), border=0)
        self.ln(8)
        self.cell(95, 5, self._txt("(подпись, печать)"), border=0)
        self.cell(95, 5, self._txt("(подпись, печать)"), border=0)


# ---------- public API -------------------------------------------------------


def render_akt_for_transaction(
    *,
    transaction: Transaction,
    customer: CustomerInfo,
    gateway: GatewayInfo,
) -> bytes:
    """Per-transaction АКТ. One row in the goods table = the txn itself.

    Used for TOPUP (most common — customer paid 1000₽, gets акт for the
    deposit) and CHARGE (less common — customer wants per-call акт).

    Refunds use a negative amount in the same form; bookkeeping conventions
    in РФ render those as a separate "корректировочный" doc, but we issue
    the same shape and let the customer's accountant relabel.
    """
    pdf = _VoltariPdf()
    pdf.add_page()
    pdf.header_block(
        gateway, "Акт выполненных работ (оказанных услуг)", _short_uuid(transaction.id)
    )
    pdf.customer_block(customer)

    description = _txn_description(transaction)
    line = AktLine(
        description=description,
        quantity=1,
        unit="шт.",
        price_kop=abs(transaction.amount_kopecks),
        total_kop=abs(transaction.amount_kopecks),
    )
    pdf.lines_table([line])

    # Period clause.
    pdf.set_font(pdf._font, style="", size=9)
    pdf.multi_cell(
        0,
        5,
        pdf._txt(
            f"Услуги оказаны полностью в дату {transaction.created_at.strftime('%d.%m.%Y')}. "
            "Заказчик претензий по объёму, качеству и срокам оказания услуг не имеет."
        ),
    )
    pdf.ln(8)
    pdf.signature_block()
    return _pdf_to_bytes(pdf)


def render_period_invoice(
    *,
    transactions: Sequence[Transaction],
    period_from: datetime,
    period_to: datetime,
    customer: CustomerInfo,
    gateway: GatewayInfo,
) -> bytes:
    """Сводный счёт за период. Group transactions by ``type`` for clarity:
    TOPUP/AUTOREFILL on top (income side), CHARGE/REFUND below.

    Empty period → still issues a doc with an empty body (the accountant
    needs to know "no activity in this period" for their journal).
    """
    pdf = _VoltariPdf()
    pdf.add_page()
    pdf.header_block(
        gateway,
        f"Счёт за период {period_from.strftime('%d.%m.%Y')} — {period_to.strftime('%d.%m.%Y')}",
        _short_uuid(uuid.uuid4()),
    )
    pdf.customer_block(customer)

    lines: list[AktLine] = []
    for tx in transactions:
        lines.append(
            AktLine(
                description=_txn_description(tx),
                quantity=1,
                unit="шт.",
                price_kop=abs(tx.amount_kopecks),
                total_kop=abs(tx.amount_kopecks),
            )
        )
    if not lines:
        # Visible "no rows" note avoids a confusing empty table.
        pdf.set_font(pdf._font, style="", size=9)
        pdf.line_cell(0, 7, pdf._txt("В указанном периоде операций не зарегистрировано."))
    else:
        pdf.lines_table(lines)

    pdf.ln(4)
    pdf.signature_block()
    return _pdf_to_bytes(pdf)


def render_upd(
    *,
    transactions: Sequence[Transaction],
    period_from: datetime,
    period_to: datetime,
    customer: CustomerInfo,
    gateway: GatewayInfo,
    vat_rate: float = 0.0,  # gateway as a service typically выставляет 0% / без НДС
) -> bytes:
    """УПД (универсальный передаточный документ) — extends invoice with
    explicit НДС column. ``vat_rate=0`` produces "Без НДС" rendering,
    standard for небольшой ИП на УСН/самозанятого.

    Layout differs from period-invoice mainly in the heading and the extra
    "Ставка НДС / Сумма НДС" columns — but for a solo MVP that doesn't yet
    have a registered ИНН (TD-001) we simplify to the same lines table
    plus a footer with the VAT total. The customer's accountant fills in
    the canonical 12-column УПД manually if needed.
    """
    pdf = _VoltariPdf()
    pdf.add_page()
    pdf.header_block(
        gateway,
        f"УПД за период {period_from.strftime('%d.%m.%Y')} — {period_to.strftime('%d.%m.%Y')}",
        _short_uuid(uuid.uuid4()),
    )
    pdf.customer_block(customer)

    lines: list[AktLine] = []
    for tx in transactions:
        lines.append(
            AktLine(
                description=_txn_description(tx),
                quantity=1,
                unit="шт.",
                price_kop=abs(tx.amount_kopecks),
                total_kop=abs(tx.amount_kopecks),
            )
        )
    if not lines:
        pdf.set_font(pdf._font, style="", size=9)
        pdf.line_cell(0, 7, pdf._txt("В указанном периоде операций не зарегистрировано."))
    else:
        pdf.lines_table(lines)

    total = sum(abs(tx.amount_kopecks) for tx in transactions)
    pdf.set_font(pdf._font, style="", size=9)
    if vat_rate <= 0:
        pdf.line_cell(0, 6, pdf._txt("Ставка НДС: Без НДС"))
        pdf.line_cell(0, 6, pdf._txt(f"Всего к оплате: {_kop_to_rub_str(total)}"))
    else:
        vat_amount = int(total * vat_rate / (1.0 + vat_rate))
        pdf.line_cell(0, 6, pdf._txt(f"Ставка НДС: {int(vat_rate * 100)}%"))
        pdf.line_cell(0, 6, pdf._txt(f"в т.ч. НДС: {_kop_to_rub_str(vat_amount)}"))
        pdf.line_cell(0, 6, pdf._txt(f"Всего к оплате: {_kop_to_rub_str(total)}"))

    pdf.ln(8)
    pdf.signature_block()
    return _pdf_to_bytes(pdf)


# ---------- internals --------------------------------------------------------


# GOST 7.79-2000 Russian → Latin transliteration table (subset). Only
# for fallback rendering when no Unicode font is available — the PDF
# stays parseable even on a stripped-down container without ttf-dejavu.
_RU_TO_LAT: dict[str, str] = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "j",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "c",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "''",
    "ы": "y",
    "ь": "'",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}
# Replace currency / dash glyphs that latin-1 also rejects.
_NON_CYRILLIC_REPLACEMENTS: dict[str, str] = {
    "₽": "RUB",
    "—": "-",
    "–": "-",
    "«": '"',
    "»": '"',
    "№": "No.",
    "→": "->",
    "…": "...",
    "✅": "[OK]",
}


def _transliterate_ru(s: str) -> str:
    """Transliterate Cyrillic text to ASCII for fpdf2 Helvetica fallback."""
    out: list[str] = []
    for ch in s:
        replaced = _NON_CYRILLIC_REPLACEMENTS.get(ch)
        if replaced is not None:
            out.append(replaced)
            continue
        if ch in _RU_TO_LAT:
            out.append(_RU_TO_LAT[ch])
        elif ch.isupper() and ch.lower() in _RU_TO_LAT:
            out.append(_RU_TO_LAT[ch.lower()].capitalize())
        else:
            # ASCII chars + anything Helvetica supports natively.
            out.append(ch)
    return "".join(out)


_TXN_KIND_LABEL: dict[str, str] = {
    TransactionKind.TOPUP.value: "Пополнение баланса",
    TransactionKind.CHARGE.value: "Списание за услуги LLM-шлюза",
    TransactionKind.REFUND.value: "Возврат на баланс",
    TransactionKind.SUBSCRIPTION.value: "Абонентская плата",
    TransactionKind.AUTOREFILL.value: "Автопополнение баланса",
}


def _txn_description(tx: Transaction) -> str:
    """Human-readable line for the goods table."""
    label = _TXN_KIND_LABEL.get(tx.type.value, str(tx.type.value))
    meta = tx.meta or {}
    model = meta.get("model")
    if model:
        return f"{label} — модель {model}"
    return label


def _pdf_to_bytes(pdf: FPDF) -> bytes:
    """fpdf2's ``output()`` returns bytes-like; normalise to bytes."""
    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


__all__ = [
    "AKT_MIN_AMOUNT_KOPECKS",
    "UPD_MIN_AMOUNT_KOPECKS",
    "AktLine",
    "CustomerInfo",
    "GatewayInfo",
    "render_akt_for_transaction",
    "render_period_invoice",
    "render_upd",
]
