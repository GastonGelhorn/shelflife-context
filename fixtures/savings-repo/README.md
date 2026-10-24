# invoicer

The invoicing service behind the billing pages: drafts, numbering, VAT, totals, the text a customer
receives, and the CSV the accountants download. No web framework here; the web layer calls
`invoicer.api` and gets plain data back.

```sh
python3 -m unittest discover -s tests -t .
```

- `invoicer/api.py` is the only module the web layer imports.
- Money is integer cents and rates are basis points ([ADR 1](docs/adr/0001-money-in-integer-cents.md)).
- Invoices are JSON files under `data/` ([ADR 2](docs/adr/0002-json-files-for-storage.md)).
- VAT is worked out per line ([ADR 3](docs/adr/0003-vat-per-line-rounded-half-up.md)).

See [architecture](docs/architecture.md) for how the modules fit together.
