from pathlib import Path
from unittest.mock import patch
from datetime import date
from decimal import Decimal

from django.test import Client, TestCase
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from .csv_imports import (
    MAX_UPLOAD_BYTES,
    DuplicateImportError,
    ImportFileError,
    commit_import,
    file_hash,
    parse_csv,
)
from .models import Account, BudgetBucket, ImportBatch, Subcategory, Transaction


class TransactionModelTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(name='Checking')

    def test_expense_can_be_uncategorized_and_unknown_is_not_review_state(self):
        transaction = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 15),
            raw_description='Grocery store',
            amount=Decimal('-42.50'),
            classification=Transaction.Classification.EXPENSE,
        )

        self.assertEqual(transaction.classification, 'EXPENSE')
        self.assertIsNone(transaction.subcategory)
        self.assertEqual(transaction.budget_bucket, '')

    def test_bucket_is_snapshotted_and_only_changes_on_recategorization(self):
        needs = Subcategory.objects.create(name='Groceries', budget_bucket=BudgetBucket.NEEDS)
        wants = Subcategory.objects.create(name='Dining', budget_bucket=BudgetBucket.WANTS)
        transaction = Transaction.objects.create(
            account=self.account,
            subcategory=needs,
            date=date(2026, 1, 15),
            raw_description='Market',
            amount=Decimal('-42.50'),
        )
        self.assertEqual(transaction.budget_bucket, BudgetBucket.NEEDS)

        needs.budget_bucket = BudgetBucket.WANTS
        needs.save()
        transaction.refresh_from_db()
        self.assertEqual(transaction.budget_bucket, BudgetBucket.NEEDS)

        transaction.subcategory = wants
        transaction.save()
        self.assertEqual(transaction.budget_bucket, BudgetBucket.WANTS)

    def test_amount_uses_decimal_precision_and_raw_description_is_preserved(self):
        transaction = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 15),
            raw_description='SQ *LOCAL COFFEE ROA 4791234567 AR',
            merchant='Local Coffee Roaster',
            amount=Decimal('-1.29'),
        )

        transaction.refresh_from_db()
        self.assertEqual(transaction.amount, Decimal('-1.29'))
        self.assertEqual(transaction.raw_description, 'SQ *LOCAL COFFEE ROA 4791234567 AR')
        self.assertEqual(transaction.merchant, 'Local Coffee Roaster')


class LedgerApiTests(APITestCase):
    def setUp(self):
        self.account = Account.objects.create(name='Checking')
        self.needs = Subcategory.objects.create(name='Groceries', budget_bucket=BudgetBucket.NEEDS)

    def test_transaction_api_serializes_decimal_as_string_and_returns_snapshot(self):
        response = self.client.post(
            '/api/transactions/',
            {
                'account': self.account.id,
                'subcategory': self.needs.id,
                'date': '2026-01-15',
                'raw_description': 'Market',
                'amount': '-42.50',
                'classification': 'EXPENSE',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['amount'], '-42.50')
        self.assertEqual(response.data['classification'], 'EXPENSE')
        self.assertEqual(response.data['budget_bucket'], 'NEEDS')
        self.assertEqual(response.data['subcategory_name'], 'Groceries')

    def test_unknown_classification_is_valid_without_a_subcategory(self):
        response = self.client.post(
            '/api/transactions/',
            {
                'account': self.account.id,
                'date': '2026-01-15',
                'raw_description': 'New transaction',
                'amount': '10.00',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['classification'], 'UNKNOWN')
        self.assertIsNone(response.data['subcategory'])
        self.assertEqual(response.data['budget_bucket'], '')

    def test_api_rejects_invalid_classification_and_bucket_values(self):
        response = self.client.post(
            '/api/transactions/',
            {
                'account': self.account.id,
                'date': '2026-01-15',
                'raw_description': 'Invalid transaction',
                'amount': '10.00',
                'classification': 'UNCATEGORIZED',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('classification', response.data)

        response = self.client.post(
            '/api/subcategories/',
            {'name': 'Invalid', 'budget_bucket': 'OTHER'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('budget_bucket', response.data)


FIXTURE_PATH = Path(__file__).parent / 'tests' / 'fixtures' / 'sofi_checking.csv'
SYNTHETIC_CSV = FIXTURE_PATH.read_bytes()


class CsvImportParserTests(TestCase):
    def test_parses_synthetic_file_and_preserves_decimal_signs_and_source_type(self):
        parsed = parse_csv(SYNTHETIC_CSV)

        self.assertEqual(parsed.source_row_count, 6)
        self.assertEqual(parsed.errors, [])
        self.assertEqual(parsed.rows[0]['date'], date(2026, 2, 1))
        self.assertEqual(parsed.rows[0]['amount'], Decimal('-12.34'))
        self.assertEqual(parsed.rows[0]['classification'], 'EXPENSE')
        self.assertEqual(parsed.rows[1]['amount'], Decimal('0.06'))
        self.assertEqual(parsed.rows[1]['classification'], 'INCOME')
        self.assertEqual(parsed.rows[2]['classification'], 'UNKNOWN')
        self.assertEqual(parsed.rows[3]['raw_description'], 'Synthetic "Paper, Shop"')
        self.assertEqual(parsed.rows[4]['classification'], 'UNKNOWN')
        self.assertEqual(parsed.rows[5]['classification'], 'UNKNOWN')
        self.assertEqual(parsed.format_identifier, 'sofi-checking-v1')

    def test_requires_supported_columns_and_strict_iso_dates(self):
        with self.assertRaisesRegex(ImportFileError, 'missing required columns'):
            parse_csv(b'Date,Description,Amount\n2026-02-01,Example,-1.00\n')

        parsed = parse_csv(
            b'Date,Description,Type,Amount,Status\n02/01/2026,Example,DEBIT_CARD,-1.00,Posted\n'
        )
        self.assertEqual(parsed.errors[0]['source_row'], 1)
        self.assertIn('Date must use YYYY-MM-DD format.', parsed.errors[0]['errors'])

    def test_rejects_bad_amounts_wrong_column_count_and_non_posted_rows(self):
        content = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-02-01,Example,DEBIT_CARD,$1.00,Posted\n'
            b'2026-02-02,Example,DEBIT_CARD,-1.00,Pending\n'
            b'2026-02-03,Example,DEBIT_CARD,-1.00,Posted,extra\n'
        )
        parsed = parse_csv(content)
        self.assertEqual(parsed.rows, [])
        self.assertEqual(parsed.source_row_count, 3)
        self.assertEqual([row['source_row'] for row in parsed.errors], [1, 2, 3])

    def test_rejects_malformed_encoding_csv_and_oversized_upload(self):
        with self.assertRaisesRegex(ImportFileError, 'UTF-8'):
            parse_csv(b'\xff')
        with self.assertRaisesRegex(ImportFileError, 'malformed quoting'):
            parse_csv(b'Date,Description,Type,Amount,Status\n2026-02-01,"broken,DEBIT_CARD,-1.00,Posted\n')
        with self.assertRaisesRegex(ImportFileError, '20 MiB'):
            parse_csv(b'x' * (MAX_UPLOAD_BYTES + 1))

    def test_hash_is_exact_file_content(self):
        self.assertEqual(file_hash(SYNTHETIC_CSV), file_hash(SYNTHETIC_CSV))
        self.assertNotEqual(file_hash(SYNTHETIC_CSV), file_hash(SYNTHETIC_CSV + b'\n'))


class CsvImportApiTests(APITestCase):
    def setUp(self):
        self.account = Account.objects.create(name='Synthetic Checking')

    def upload(self, path, content=SYNTHETIC_CSV):
        return SimpleUploadedFile(path, content, content_type='text/csv')

    def test_preview_does_not_write_and_confirm_imports_with_batch_traceability(self):
        preview = self.client.post(
            '/api/imports/preview/',
            {'account': self.account.id, 'file': self.upload('synthetic.csv')},
            format='multipart',
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.data['valid_row_count'], 6)
        self.assertTrue(preview.data['can_import'])
        self.assertEqual(ImportBatch.objects.count(), 0)
        self.assertEqual(Transaction.objects.count(), 0)

        result = self.client.post(
            '/api/imports/',
            {'account': self.account.id, 'file': self.upload('renamed.csv')},
            format='multipart',
        )
        self.assertEqual(result.status_code, 201)
        batch = ImportBatch.objects.get()
        transactions = list(Transaction.objects.filter(import_batch=batch).order_by('source_row'))
        self.assertEqual(batch.original_filename, 'renamed.csv')
        self.assertEqual(batch.account, self.account)
        self.assertEqual(batch.file_sha256, file_hash(SYNTHETIC_CSV))
        self.assertEqual(batch.source_row_count, 6)
        self.assertEqual(batch.imported_row_count, 6)
        self.assertEqual([transaction.source_row for transaction in transactions], [1, 2, 3, 4, 5, 6])
        self.assertEqual(transactions[0].source_type, 'DEBIT_CARD')
        self.assertEqual(transactions[2].classification, 'UNKNOWN')
        self.assertIsNone(transactions[0].posted_date)

    def test_preview_accepts_local_vite_origin_without_csrf_exemption_changes(self):
        response = self.client.post(
            '/api/imports/preview/',
            {'account': self.account.id, 'file': self.upload('synthetic.csv')},
            format='multipart',
            HTTP_ORIGIN='http://localhost:5173',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['valid_row_count'], 6)
        self.assertEqual(Transaction.objects.count(), 0)

    def test_local_vite_origins_are_trusted(self):
        self.assertIn('http://localhost:5173', settings.CSRF_TRUSTED_ORIGINS)
        self.assertIn('http://127.0.0.1:5173', settings.CSRF_TRUSTED_ORIGINS)

    def test_import_batch_tracks_arbitrary_transaction_date_range_not_filename(self):
        content = (
            b'Date,Description,Type,Amount,Current balance,Status\n'
            b'2024-01-03,Earlier synthetic,DEBIT_CARD,-1.25,10.00,Posted\n'
            b'2026-11-07,Later synthetic,ROUNDUP,-0.50,9.50,Posted\n'
        )
        preview = self.client.post(
            '/api/imports/preview/',
            {'account': self.account.id, 'file': self.upload('January-2025.csv', content)},
            format='multipart',
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.data['earliest_transaction_date'], '2024-01-03')
        self.assertEqual(preview.data['latest_transaction_date'], '2026-11-07')
        self.assertEqual(
            [row['amount'] for row in preview.data['valid_rows']],
            ['-1.25', '-0.50'],
        )
        self.assertEqual(preview.data['valid_rows'][1]['classification'], 'UNKNOWN')
        self.assertNotIn('budget_bucket', preview.data['valid_rows'][0])
        self.assertEqual(ImportBatch.objects.count(), 0)

        result = self.client.post(
            '/api/imports/',
            {'account': self.account.id, 'file': self.upload('January-2025.csv', content)},
            format='multipart',
        )
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.data['earliest_transaction_date'], '2024-01-03')
        self.assertEqual(result.data['latest_transaction_date'], '2026-11-07')
        self.assertEqual(Transaction.objects.count(), 2)

        history = self.client.get('/api/import-batches/')
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.data[0]['earliest_transaction_date'], '2024-01-03')
        self.assertEqual(history.data[0]['latest_transaction_date'], '2026-11-07')
        self.assertEqual(history.data[0]['original_filename'], 'January-2025.csv')

    def test_exact_file_reimport_is_blocked_for_same_account_only(self):
        parsed = parse_csv(SYNTHETIC_CSV)
        commit_import(self.account, 'one.csv', SYNTHETIC_CSV, parsed)
        preview = self.client.post(
            '/api/imports/preview/',
            {'account': self.account.id, 'file': self.upload('renamed.csv')},
            format='multipart',
        )
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.data['already_imported'])
        self.assertFalse(preview.data['can_import'])

        duplicate = self.client.post(
            '/api/imports/',
            {'account': self.account.id, 'file': self.upload('renamed.csv')},
            format='multipart',
        )
        self.assertEqual(duplicate.status_code, 409)

        other_account = Account.objects.create(name='Other Checking')
        allowed = self.client.post(
            '/api/imports/',
            {'account': other_account.id, 'file': self.upload('same-content.csv')},
            format='multipart',
        )
        self.assertEqual(allowed.status_code, 201)

    def test_invalid_rows_block_the_entire_import(self):
        content = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-02-01,Valid,DEBIT_CARD,-1.00,Posted\n'
            b'2026-02-02,Invalid,DEBIT_CARD,-2.00,Pending\n'
        )
        response = self.client.post(
            '/api/imports/',
            {'account': self.account.id, 'file': self.upload('invalid.csv', content)},
            format='multipart',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ImportBatch.objects.count(), 0)
        self.assertEqual(Transaction.objects.count(), 0)

    def test_import_write_failure_rolls_back_batch(self):
        parsed = parse_csv(SYNTHETIC_CSV)
        with patch('finance.csv_imports.Transaction.objects.bulk_create', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                commit_import(self.account, 'synthetic.csv', SYNTHETIC_CSV, parsed)
        self.assertEqual(ImportBatch.objects.count(), 0)
        self.assertEqual(Transaction.objects.count(), 0)

    def test_inactive_accounts_and_oversized_files_are_rejected(self):
        self.account.is_active = False
        self.account.save()
        inactive = self.client.post(
            '/api/imports/preview/',
            {'account': self.account.id, 'file': self.upload('synthetic.csv')},
            format='multipart',
        )
        self.assertEqual(inactive.status_code, 400)

        self.account.is_active = True
        self.account.save()
        too_large = self.client.post(
            '/api/imports/preview/',
            {
                'account': self.account.id,
                'file': self.upload('large.csv', b'x' * (MAX_UPLOAD_BYTES + 1)),
            },
            format='multipart',
        )
        self.assertEqual(too_large.status_code, 400)
        self.assertIn('20 MiB', too_large.data['detail'])


class CsrfFlowTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.account = Account.objects.create(name='Synthetic Checking')

    def upload(self):
        return SimpleUploadedFile('synthetic.csv', SYNTHETIC_CSV, content_type='text/csv')

    def test_csrf_endpoint_sets_cookie_and_token_allows_preview(self):
        token_response = self.client.get('/api/csrf/')
        self.assertEqual(token_response.status_code, 200)
        token = token_response.json()['csrfToken']
        self.assertIn('csrftoken', self.client.cookies)

        response = self.client.post(
            '/api/imports/preview/',
            {'account': self.account.id, 'file': self.upload()},
            HTTP_ORIGIN='http://localhost:5173',
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['valid_row_count'], 6)
        self.assertEqual(ImportBatch.objects.count(), 0)
        self.assertEqual(Transaction.objects.count(), 0)

    def test_missing_or_untrusted_csrf_requests_are_rejected(self):
        missing_token = self.client.post(
            '/api/imports/preview/',
            {'account': self.account.id, 'file': self.upload()},
            HTTP_ORIGIN='http://localhost:5173',
        )
        self.assertEqual(missing_token.status_code, 403)

        token_response = self.client.get('/api/csrf/')
        token = token_response.json()['csrfToken']
        invalid_token = self.client.post(
            '/api/imports/preview/',
            {'account': self.account.id, 'file': self.upload()},
            HTTP_ORIGIN='http://localhost:5173',
            HTTP_X_CSRFTOKEN='invalid-token',
        )
        self.assertEqual(invalid_token.status_code, 403)

        untrusted_origin = self.client.post(
            '/api/imports/preview/',
            {'account': self.account.id, 'file': self.upload()},
            HTTP_ORIGIN='http://evil.example',
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(untrusted_origin.status_code, 403)
        self.assertEqual(ImportBatch.objects.count(), 0)
        self.assertEqual(Transaction.objects.count(), 0)
