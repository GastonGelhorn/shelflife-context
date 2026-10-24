# 2. JSON files for storage

Status: Accepted

## Context

A few thousand invoices a year, one writer, backups by copying a folder.

## Decision

One JSON file per invoice, written to a temporary file and renamed. No database until there is a
second writer.
