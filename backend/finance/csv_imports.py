import hashlib
from dataclasses import dataclass

from django.db import IntegrityError, transaction

from .models import Account, ImportBatch, Transaction

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


def commit_import(account: Account, filename: str, content: bytes, parsed: ParsedImport) -> ImportBatch:
    if parsed.errors:
        raise ImportFileError('Correct or remove invalid rows before importing the CSV.')
    if len(filename) > 255:
        raise ImportFileError('The filename must be 255 characters or fewer.')

    digest = file_hash(content)
    try:
        with transaction.atomic():
            batch = ImportBatch.objects.create(
                account=account,
                original_filename=filename,
                file_sha256=digest,
                format_identifier=parsed.format_identifier,
                source_row_count=parsed.source_row_count,
                imported_row_count=len(parsed.rows),
                earliest_transaction_date=min(row['date'] for row in parsed.rows),
                latest_transaction_date=max(row['date'] for row in parsed.rows),
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
                )
                for row in parsed.rows
            ])
    except IntegrityError as error:
        if ImportBatch.objects.filter(account=account, file_sha256=digest).exists():
            raise DuplicateImportError('This exact CSV has already been imported to this account.') from error
        raise
    return batch
