# Architecture

```
api ──► storage ──► models
 │ ├──► numbering
 │ ├──► render ──► totals ──► tax ──► discounts ──► money
 │ ├──► audit
 │ └──► notifications
export/csv_export ──► totals
```

- **api** — the functions the web layer calls. They take ids, load from storage, change, save.
- **storage** — `JsonStorage(root)`: one file per invoice under `invoices/`, customers in `customers.json`.
- **models** — dataclasses: `Customer`, `LineItem`, `Invoice`. `Invoice.from_dict` ignores keys it does not know.
- **discounts** — a line's discount is basis points of quantity × unit price, rounded half up.
- **tax** — the VAT on one line.
- **totals** — net, tax and gross for a line or an invoice.
- **render** — the plain-text invoice sent to customers.
- **numbering** — `INV-<year>-<4 digits>`, consecutive per year, assigned at finalisation.
- **audit** — one JSON line per change in `audit.log` beside the data.
- **notifications** — the outbox the mailer drains.

## Invoice life cycle

`draft` → `finalized` (gets its number, issue and due dates) → `void`. Only drafts take new lines.
A finalized invoice is never edited; it is voided or corrected by another document.
