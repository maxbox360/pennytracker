import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction

from .models import (
    Account,
    DuplicateResolution,
    ImportBatch,
    ReviewStatus,
    Transaction,
    TransactionClassification,
)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class ImportFileError(ValueError):
    pass


class DuplicateImportError(ValueError):
    pass


@dataclass
class ParsedImport:
    rows: list[dict]
    errors: list[dict]
    source_row_count: int
    format_identifier: str


def parse_csv(content: bytes) -> ParsedImport:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ImportFileError('CSV files must be 20 MiB or smaller.')
    from .sofi_checking import parse

    return parse(content)


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def normalized_description(value: str) -> str:
    return re.sub(r'\s+', ' ', value).strip().casefold()


def transaction_fingerprint(account_id: int, row: dict) -> tuple:
    return (
        account_id,
        row['date'],
        row.get('posted_date'),
        Decimal(row['amount']).quantize(Decimal('0.01')),
        row['source_type'].strip().casefold(),
        normalized_description(row['raw_description']),
    )


def overlap_results(account: Account, rows: list[dict]) -> list[dict]:
    # ponytail: materialize one account's ledger for exact matching; move counts into SQL if it outgrows memory.
    existing = list(
        Transaction.objects.filter(account=account)
        .exclude(duplicate_resolution=DuplicateResolution.CONFIRMED_DUPLICATE)
        .select_related('account')
    )
    by_fingerprint = defaultdict(list)
    by_date_amount = defaultdict(list)
    for candidate in existing:
        candidate_row = {
            'date': candidate.date,
            'posted_date': candidate.posted_date,
            'amount': candidate.amount,
            'source_type': candidate.source_type,
            'raw_description': candidate.raw_description,
        }
        by_fingerprint[transaction_fingerprint(account.id, candidate_row)].append(candidate)
        by_date_amount[(candidate.date, candidate.amount)].append(candidate)

    incoming_occurrences = defaultdict(int)
    results = []
    for row in rows:
        fingerprint = transaction_fingerprint(account.id, row)
        occurrence = incoming_occurrences[fingerprint]
        incoming_occurrences[fingerprint] += 1
        represented = by_fingerprint[fingerprint]
        if occurrence < len(represented):
            results.append({
                **row,
                'overlap_status': 'ALREADY_REPRESENTED',
                'duplicate_candidates': [],
                'overlap_reason': 'An identical occurrence is already represented for this account.',
            })
            continue

        description = normalized_description(row['raw_description'])
        source_type = row['source_type'].strip().casefold()
        candidates = [
            candidate
            for candidate in by_date_amount[(row['date'], row['amount'])]
            if (
                normalized_description(candidate.raw_description) == description
                or candidate.source_type.strip().casefold() == source_type
            )
        ]
        if candidates:
            results.append({
                **row,
                'overlap_status': 'POSSIBLE_DUPLICATE',
                'duplicate_candidates': candidates,
                'overlap_reason': 'Same account, date, and amount with matching description or source type.',
            })
        else:
            results.append({
                **row,
                'overlap_status': 'NEW',
                'duplicate_candidates': [],
                'overlap_reason': '',
            })
    return results


def commit_import(account: Account, filename: str, content: bytes, parsed: ParsedImport) -> ImportBatch:
    if parsed.errors:
        raise ImportFileError('Correct or remove invalid rows before importing the CSV.')
    if len(filename) > 255:
        raise ImportFileError('The filename must be 255 characters or fewer.')

    digest = file_hash(content)
    try:
        with transaction.atomic():
            rows = overlap_results(account, parsed.rows)
            imported_rows = [
                row for row in rows if row['overlap_status'] != 'ALREADY_REPRESENTED'
            ]
            possible_duplicate_count = sum(
                row['overlap_status'] == 'POSSIBLE_DUPLICATE' for row in imported_rows
            )
            batch = ImportBatch.objects.create(
                account=account,
                original_filename=filename,
                file_sha256=digest,
                format_identifier=parsed.format_identifier,
                source_row_count=parsed.source_row_count,
                imported_row_count=len(imported_rows),
                already_represented_row_count=sum(
                    row['overlap_status'] == 'ALREADY_REPRESENTED' for row in rows
                ),
                possible_duplicate_row_count=possible_duplicate_count,
                earliest_transaction_date=min(row['date'] for row in parsed.rows)
                if parsed.rows else None,
                latest_transaction_date=max(row['date'] for row in parsed.rows)
                if parsed.rows else None,
            )
            Transaction.objects.bulk_create([
                Transaction(
                    account=account,
                    import_batch=batch,
                    source_row=row['source_row'],
                    date=row['date'],
                    raw_description=row['raw_description'],
                    amount=row['amount'],
                    source_type=row['source_type'],
                    classification=row['classification'],
                    review_status=(
                        ReviewStatus.PENDING
                        if row['overlap_status'] == 'POSSIBLE_DUPLICATE'
                        or row['classification'] in (
                            TransactionClassification.UNKNOWN,
                            TransactionClassification.EXPENSE,
                            TransactionClassification.REFUND,
                            TransactionClassification.TRANSFER,
                        )
                        else ReviewStatus.NOT_REQUIRED
                    ),
                    duplicate_resolution=(
                        DuplicateResolution.UNRESOLVED
                        if row['overlap_status'] == 'POSSIBLE_DUPLICATE'
                        else DuplicateResolution.NONE
                    ),
                )
                for row in imported_rows
            ])
            if possible_duplicate_count:
                imported_by_row = {
                    item.source_row: item
                    for item in Transaction.objects.filter(import_batch=batch)
                }
                for row in imported_rows:
                    if row['overlap_status'] == 'POSSIBLE_DUPLICATE':
                        imported_by_row[row['source_row']].duplicate_candidates.set(
                            row['duplicate_candidates']
                        )
    except IntegrityError as error:
        if ImportBatch.objects.filter(account=account, file_sha256=digest).exists():
            raise DuplicateImportError('This exact CSV has already been imported to this account.') from error
        raise
    return batch
