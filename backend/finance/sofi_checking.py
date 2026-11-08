import csv
import io
import re
from datetime import date
from decimal import Decimal

from .csv_imports import ImportFileError, ParsedImport

FORMAT_IDENTIFIER = 'sofi-checking-v1'
REQUIRED_COLUMNS = {'Date', 'Description', 'Type', 'Amount', 'Status'}
AMOUNT_PATTERN = re.compile(r'-?\d+(?:\.\d{1,2})?\Z')


def parse(content: bytes) -> ParsedImport:
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise ImportFileError('The CSV must be UTF-8 encoded.') from error

    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    try:
        headers = next(reader, None)
        if not headers:
            raise ImportFileError('The CSV is empty or has no header row.')
        headers = [header.strip() for header in headers]
        if len(headers) != len(set(headers)):
            raise ImportFileError('The CSV contains duplicate column headers.')
        missing = REQUIRED_COLUMNS - set(headers)
        if missing:
            raise ImportFileError(
                f'Unsupported CSV structure; missing required columns: {", ".join(sorted(missing))}.'
            )

        indexes = {header: headers.index(header) for header in REQUIRED_COLUMNS}
        rows = []
        errors = []
        source_row_count = 0

        for source_row, values in enumerate(reader, start=1):
            if not values:
                continue
            source_row_count += 1
            row_errors = []
            if len(values) != len(headers):
                errors.append({
                    'source_row': source_row,
                    'errors': ['The row does not have the same number of columns as the header.'],
                })
                continue

            def value(column):
                return values[indexes[column]]

            date_text = value('Date').strip()
            try:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_text):
                    raise ValueError
                transaction_date = date.fromisoformat(date_text)
            except ValueError:
                row_errors.append('Date must use YYYY-MM-DD format.')

            description = value('Description')
            if not description.strip():
                row_errors.append('Description is required.')

            source_type = value('Type').strip()
            if not source_type:
                row_errors.append('Type is required.')
            elif len(source_type) > 50:
                row_errors.append('Type must be 50 characters or fewer.')

            amount_text = value('Amount').strip()
            try:
                if not AMOUNT_PATTERN.fullmatch(amount_text):
                    raise ValueError
                whole_amount = amount_text.lstrip('-').partition('.')[0].lstrip('0')
                if len(whole_amount) > 12:
                    raise ValueError
                amount = Decimal(amount_text)
            except (ValueError, ArithmeticError):
                row_errors.append('Amount must be a plain decimal with at most two fractional digits.')

            if value('Status').strip().casefold() != 'posted':
                row_errors.append('Only Posted transactions can be imported.')

            if row_errors:
                errors.append({'source_row': source_row, 'errors': row_errors})
                continue

            normalized_type = source_type.upper()
            classification = 'UNKNOWN'
            if normalized_type == 'DEBIT_CARD' and amount < 0:
                classification = 'EXPENSE'
            elif normalized_type == 'INTEREST_EARNED' and amount > 0:
                classification = 'INCOME'

            rows.append({
                'source_row': source_row,
                'date': transaction_date,
                'raw_description': description,
                'amount': amount,
                'source_type': source_type,
                'classification': classification,
            })

        if source_row_count == 0:
            raise ImportFileError('The CSV contains no transaction rows.')
        return ParsedImport(rows, errors, source_row_count, FORMAT_IDENTIFIER)
    except csv.Error as error:
        raise ImportFileError('The CSV contains malformed quoting or row formatting.') from error
