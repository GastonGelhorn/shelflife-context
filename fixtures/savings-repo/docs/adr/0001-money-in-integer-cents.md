# 1. Money in integer cents

Status: Accepted

## Context

Floats lost cents in the old PHP billing, and Decimal leaked into JSON as strings in some places and
numbers in others.

## Decision

Every amount is an `int` in cents. Every rate is an `int` in basis points (2100 = 21%). Formatting
happens only at the edges, with `money.format_amount` for people and `money.decimal_string` for files.
