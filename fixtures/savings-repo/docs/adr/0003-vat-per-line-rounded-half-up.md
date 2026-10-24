# 3. VAT per line, rounded half up

Status: Accepted

## Context

Tax authorities accept either per-line or per-invoice rounding as long as an invoice is consistent.
Customers compare the lines they see with the total, so the total must be the sum of the lines.

## Decision

VAT is worked out on each line and rounded there, half up (`money.apply_rate`). The invoice's VAT is
the sum of the lines' VAT. Every place that shows or adds up an amount uses the same rounding.
