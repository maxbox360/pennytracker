from pathlib import Path
from unittest.mock import patch
from datetime import date
from decimal import Decimal

from django.test import Client, TestCase
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.test import APITestCase

from .categorization_rules import (
    apply_rule_to_transaction,
    evaluate_rules,
    normalize_rule_text,
    pending_matches_for_rule,
    rule_matches,
)
from .csv_imports import (
    MAX_UPLOAD_BYTES,
    DuplicateImportError,
    ImportFileError,
    ParsedImport,
    commit_import,
    file_hash,
    overlap_results,
    parse_csv,
)
from .models import (
    Account,
    BudgetBucket,
    CategorizationRule,
    DuplicateResolution,
    ImportBatch,
    ReviewStatus,
    RuleMatchField,
    RuleMatchType,
    Subcategory,
    Transaction,
)


class TransactionModelTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(name='Checking')

    def test_active_subcategory_names_are_case_insensitively_unique_in_database(self):
        Subcategory.objects.create(name='Fast Food', budget_bucket=BudgetBucket.WANTS)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Subcategory.objects.create(name=' fast food ', budget_bucket=BudgetBucket.NEEDS)

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
            classification=Transaction.Classification.EXPENSE,
        )
        self.assertEqual(transaction.budget_bucket, BudgetBucket.NEEDS)

        needs.budget_bucket = BudgetBucket.WANTS
        needs.save()
        transaction.refresh_from_db()
        self.assertEqual(transaction.budget_bucket, BudgetBucket.NEEDS)

        transaction.subcategory = wants
        transaction.save()
        self.assertEqual(transaction.budget_bucket, BudgetBucket.WANTS)

    def test_classification_change_clears_invalid_subcategory_and_bucket(self):
        wants = Subcategory.objects.create(name='Dining', budget_bucket=BudgetBucket.WANTS)
        transaction = Transaction.objects.create(
            account=self.account,
            subcategory=wants,
            date=date(2026, 1, 15),
            raw_description='Restaurant',
            amount=Decimal('-20.00'),
            classification=Transaction.Classification.EXPENSE,
        )

        transaction.classification = Transaction.Classification.INCOME
        transaction.save(update_fields=('classification',))
        transaction.refresh_from_db()

        self.assertIsNone(transaction.subcategory)
        self.assertEqual(transaction.budget_bucket, '')
        self.assertEqual(transaction.review_status, ReviewStatus.NOT_REQUIRED)

    def test_unresolved_edits_return_reviewed_transactions_to_pending(self):
        wants = Subcategory.objects.create(name='Dining', budget_bucket=BudgetBucket.WANTS)
        transaction = Transaction.objects.create(
            account=self.account,
            subcategory=wants,
            date=date(2026, 1, 15),
            raw_description='Restaurant',
            amount=Decimal('-20.00'),
            classification=Transaction.Classification.EXPENSE,
            review_status=ReviewStatus.REVIEWED,
        )
        transaction.classification = Transaction.Classification.UNKNOWN
        transaction.save(update_fields=('classification',))
        transaction.refresh_from_db()

        self.assertIsNone(transaction.subcategory)
        self.assertEqual(transaction.budget_bucket, '')
        self.assertEqual(transaction.review_status, ReviewStatus.PENDING)

    def test_transfer_savings_category_is_allowed_but_other_transfer_category_is_not(self):
        savings = Subcategory.objects.create(name='Emergency Fund', budget_bucket=BudgetBucket.SAVINGS)
        transaction = Transaction.objects.create(
            account=self.account,
            subcategory=savings,
            date=date(2026, 1, 15),
            raw_description='Savings transfer',
            amount=Decimal('-100.00'),
            classification=Transaction.Classification.TRANSFER,
        )
        self.assertEqual(transaction.budget_bucket, BudgetBucket.SAVINGS)
        self.assertEqual(transaction.review_status, ReviewStatus.PENDING)

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

    def test_subcategory_names_are_case_insensitively_unique_and_archived_names_can_be_reactivated(self):
        response = self.client.post(
            '/api/subcategories/',
            {'name': '  Fast Food  ', 'budget_bucket': BudgetBucket.WANTS},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['name'], 'Fast Food')

        duplicate = self.client.post(
            '/api/subcategories/',
            {'name': 'FAST FOOD', 'budget_bucket': BudgetBucket.NEEDS},
            format='json',
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn('already exists', str(duplicate.data['name']))
        self.assertEqual(Subcategory.objects.count(), 2)

        existing = Subcategory.objects.get(name='Fast Food')
        archived = self.client.patch(
            f'/api/subcategories/{existing.id}/',
            {'is_active': False},
            format='json',
        )
        self.assertEqual(archived.status_code, 200)
        self.assertFalse(archived.data['is_active'])
        archived_duplicate = self.client.post(
            '/api/subcategories/',
            {'name': 'fast food', 'budget_bucket': BudgetBucket.WANTS},
            format='json',
        )
        self.assertEqual(archived_duplicate.status_code, 400)
        self.assertIn('archived', str(archived_duplicate.data['name']))

        reactivated = self.client.patch(
            f'/api/subcategories/{existing.id}/',
            {'is_active': True},
            format='json',
        )
        self.assertEqual(reactivated.status_code, 200)
        self.assertTrue(reactivated.data['is_active'])
        self.assertEqual(Subcategory.objects.filter(name__iexact='fast food').count(), 1)

    def test_bulk_subcategory_create_splits_trims_and_ignores_empty_entries(self):
        batches = (
            ('Fuel, Utilities, Rent', ['Fuel', 'Utilities', 'Rent']),
            ('Medical\nInsurance\nTransit', ['Medical', 'Insurance', 'Transit']),
            ('Pets,\nChildcare, Gifts', ['Pets', 'Childcare', 'Gifts']),
            ('  Dental, , Vision\n\n  Fitness , ', ['Dental', 'Vision', 'Fitness']),
        )
        for names, expected in batches:
            with self.subTest(names=names):
                response = self.client.post(
                    '/api/subcategories/bulk-create/',
                    {'names': names, 'budget_bucket': BudgetBucket.WANTS},
                    format='json',
                )
                self.assertEqual(response.status_code, 201)
                self.assertEqual([item['name'] for item in response.data['created']], expected)
                self.assertTrue(all(
                    item['budget_bucket'] == BudgetBucket.WANTS
                    for item in response.data['created']
                ))

    def test_bulk_subcategory_create_skips_active_and_repeated_case_insensitive_names(self):
        response = self.client.post(
            '/api/subcategories/bulk-create/',
            {'names': 'Fuel, groceries, FUEL, Utilities', 'budget_bucket': BudgetBucket.WANTS},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(
            [item['name'] for item in response.data['created']],
            ['Fuel', 'Utilities'],
        )
        self.assertEqual(response.data['already_exists'], ['Groceries'])
        self.assertEqual(response.data['duplicate_count'], 1)
        self.assertEqual(Subcategory.objects.filter(name__iexact='fuel').count(), 1)

    def test_bulk_subcategory_create_is_idempotent_when_submitted_again(self):
        body = {'names': 'Groceries, Fuel', 'budget_bucket': BudgetBucket.NEEDS}
        first = self.client.post('/api/subcategories/bulk-create/', body, format='json')
        second = self.client.post('/api/subcategories/bulk-create/', body, format='json')

        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.data['count'], 1)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.data['count'], 0)
        self.assertEqual(second.data['already_exists'], ['Groceries', 'Fuel'])
        self.assertEqual(Subcategory.objects.filter(name__iexact='fuel').count(), 1)

    def test_bulk_subcategory_create_skips_archived_names_but_creates_other_entries(self):
        Subcategory.objects.create(
            name='Archived Fuel',
            budget_bucket=BudgetBucket.NEEDS,
            is_active=False,
        )
        response = self.client.post(
            '/api/subcategories/bulk-create/',
            {'names': 'New Category, archived fuel', 'budget_bucket': BudgetBucket.NEEDS},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['archived'], ['Archived Fuel'])
        self.assertTrue(Subcategory.objects.filter(name='New Category').exists())
        self.assertFalse(Subcategory.objects.get(name='Archived Fuel').is_active)

    def test_bulk_subcategory_create_rejects_invalid_name_atomically(self):
        response = self.client.post(
            '/api/subcategories/bulk-create/',
            {'names': f'New Category, {"x" * 101}', 'budget_bucket': BudgetBucket.NEEDS},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['invalid'][0]['name'], 'x' * 101)
        self.assertFalse(Subcategory.objects.filter(name='New Category').exists())

    def test_bulk_subcategory_create_requires_at_least_one_name(self):
        response = self.client.post(
            '/api/subcategories/bulk-create/',
            {'names': ' , \n ', 'budget_bucket': BudgetBucket.NEEDS},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Subcategory.objects.count(), 1)

    def test_category_moves_preserve_existing_transaction_bucket_snapshots(self):
        category = Subcategory.objects.create(name='Dining', budget_bucket=BudgetBucket.WANTS)
        existing = Transaction.objects.create(
            account=self.account,
            subcategory=category,
            date=date(2026, 1, 15),
            raw_description='Restaurant',
            amount=Decimal('-20.00'),
            classification=Transaction.Classification.EXPENSE,
        )

        moved = self.client.patch(
            f'/api/subcategories/{category.id}/',
            {'name': 'Dining Out', 'budget_bucket': BudgetBucket.NEEDS},
            format='json',
        )
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(moved.data['name'], 'Dining Out')
        existing.refresh_from_db()
        self.assertEqual(existing.budget_bucket, BudgetBucket.WANTS)

        future = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 16),
            raw_description='Future restaurant',
            amount=Decimal('-15.00'),
            classification=Transaction.Classification.EXPENSE,
        )
        assigned = self.client.patch(
            f'/api/transactions/{future.id}/',
            {'subcategory': category.id},
            format='json',
        )
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(assigned.data['budget_bucket'], BudgetBucket.NEEDS)

    def test_archived_subcategories_cannot_be_assigned_to_new_transactions(self):
        archived = Subcategory.objects.create(
            name='Old category',
            budget_bucket=BudgetBucket.NEEDS,
            is_active=False,
        )
        transaction = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 15),
            raw_description='Grocery store',
            amount=Decimal('-10.00'),
            classification=Transaction.Classification.EXPENSE,
        )

        response = self.client.patch(
            f'/api/transactions/{transaction.id}/',
            {'subcategory': archived.id},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        transaction.refresh_from_db()
        self.assertIsNone(transaction.subcategory)

    def test_create_and_assign_subcategory_is_atomic_and_snapshots_current_bucket(self):
        transaction = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 15),
            raw_description='Restaurant',
            amount=Decimal('-12.00'),
            classification=Transaction.Classification.EXPENSE,
        )
        response = self.client.post(
            f'/api/transactions/{transaction.id}/create-subcategory-and-assign/',
            {'name': 'Fast Food', 'budget_bucket': BudgetBucket.WANTS},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['subcategory']['name'], 'Fast Food')
        self.assertEqual(response.data['transaction']['budget_bucket'], BudgetBucket.WANTS)
        transaction.refresh_from_db()
        self.assertEqual(transaction.subcategory.name, 'Fast Food')

        draft_classification = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 16),
            raw_description='Unreviewed restaurant',
            amount=Decimal('-8.00'),
            classification=Transaction.Classification.UNKNOWN,
        )
        response = self.client.post(
            f'/api/transactions/{draft_classification.id}/create-subcategory-and-assign/',
            {
                'name': 'Cafe',
                'budget_bucket': BudgetBucket.NEEDS,
                'classification': Transaction.Classification.EXPENSE,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['transaction']['classification'], Transaction.Classification.EXPENSE)
        self.assertEqual(response.data['transaction']['budget_bucket'], BudgetBucket.NEEDS)

        invalid = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 17),
            raw_description='Unknown transaction',
            amount=Decimal('-5.00'),
            classification=Transaction.Classification.UNKNOWN,
        )
        response = self.client.post(
            f'/api/transactions/{invalid.id}/create-subcategory-and-assign/',
            {'name': 'Must Roll Back', 'budget_bucket': BudgetBucket.NEEDS},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Subcategory.objects.filter(name='Must Roll Back').exists())
        invalid.refresh_from_db()
        self.assertIsNone(invalid.subcategory)

    def test_income_and_transfer_category_eligibility(self):
        response = self.client.post(
            '/api/transactions/',
            {
                'account': self.account.id,
                'subcategory': self.needs.id,
                'date': '2026-01-15',
                'raw_description': 'Income',
                'amount': '100.00',
                'classification': 'INCOME',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('subcategory', response.data)

        response = self.client.post(
            '/api/transactions/',
            {
                'account': self.account.id,
                'subcategory': self.needs.id,
                'date': '2026-01-15',
                'raw_description': 'Transfer',
                'amount': '-100.00',
                'classification': 'TRANSFER',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_classification_edit_clears_ineligible_subcategory_and_bucket_atomically(self):
        transaction = Transaction.objects.create(
            account=self.account,
            subcategory=self.needs,
            date=date(2026, 1, 15),
            raw_description='Groceries',
            amount=Decimal('-20.00'),
            classification=Transaction.Classification.EXPENSE,
        )
        response = self.client.patch(
            f'/api/transactions/{transaction.id}/',
            {'classification': 'INCOME'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['subcategory'])
        self.assertEqual(response.data['budget_bucket'], '')

    def test_refund_requires_needs_or_wants_subcategory_to_be_reviewed(self):
        transaction = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 15),
            raw_description='Refund',
            amount=Decimal('10.00'),
            classification=Transaction.Classification.REFUND,
        )
        response = self.client.post(f'/api/transactions/{transaction.id}/mark-reviewed/')
        self.assertEqual(response.status_code, 400)

        response = self.client.patch(
            f'/api/transactions/{transaction.id}/',
            {'subcategory': self.needs.id},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['budget_bucket'], BudgetBucket.NEEDS)
        self.assertEqual(response.data['review_status'], ReviewStatus.PENDING)

        response = self.client.post(f'/api/transactions/{transaction.id}/mark-reviewed/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['review_status'], ReviewStatus.REVIEWED)

    def test_transfer_can_be_reviewed_without_category_and_savings_assignment_is_snapshotted(self):
        savings = Subcategory.objects.create(name='Emergency Fund', budget_bucket=BudgetBucket.SAVINGS)
        transaction = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 15),
            raw_description='Savings transfer',
            amount=Decimal('-100.00'),
            classification=Transaction.Classification.TRANSFER,
        )
        response = self.client.patch(
            f'/api/transactions/{transaction.id}/',
            {'subcategory': savings.id},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['budget_bucket'], BudgetBucket.SAVINGS)
        response = self.client.post(f'/api/transactions/{transaction.id}/mark-reviewed/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['review_status'], ReviewStatus.REVIEWED)

    def test_classifying_as_transfer_returns_not_required_transaction_to_review(self):
        transaction = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 15),
            raw_description='Unclear bank entry',
            amount=Decimal('-100.00'),
            classification=Transaction.Classification.INCOME,
        )
        response = self.client.patch(
            f'/api/transactions/{transaction.id}/',
            {'classification': 'TRANSFER'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['review_status'], ReviewStatus.PENDING)

    def test_individual_edit_preserves_raw_description_and_updates_merchant_and_notes(self):
        transaction = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 15),
            raw_description='SQ *COFFEE 123',
            amount=Decimal('-4.00'),
            classification=Transaction.Classification.UNKNOWN,
        )
        response = self.client.patch(
            f'/api/transactions/{transaction.id}/',
            {'merchant': 'Coffee Shop', 'notes': 'Work meeting'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['raw_description'], 'SQ *COFFEE 123')
        self.assertEqual(response.data['merchant'], 'Coffee Shop')
        self.assertEqual(response.data['notes'], 'Work meeting')

    def test_bulk_subcategory_validation_is_atomic(self):
        income = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 15),
            raw_description='Paycheck',
            amount=Decimal('100.00'),
            classification=Transaction.Classification.INCOME,
        )
        expense = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 16),
            raw_description='Grocery store',
            amount=Decimal('-10.00'),
            classification=Transaction.Classification.EXPENSE,
        )

        response = self.client.post(
            '/api/transactions/bulk/',
            {
                'operation': 'subcategory',
                'transaction_ids': [income.id, expense.id],
                'subcategory': self.needs.id,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        income.refresh_from_db()
        expense.refresh_from_db()
        self.assertIsNone(income.subcategory)
        self.assertIsNone(expense.subcategory)

    def test_bulk_classification_clears_stale_categories_and_review_action_is_atomic(self):
        expense = Transaction.objects.create(
            account=self.account,
            subcategory=self.needs,
            date=date(2026, 1, 15),
            raw_description='Grocery store',
            amount=Decimal('-10.00'),
            classification=Transaction.Classification.EXPENSE,
        )
        unknown = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 16),
            raw_description='Unknown transaction',
            amount=Decimal('-4.00'),
        )
        response = self.client.post(
            '/api/transactions/bulk/',
            {
                'operation': 'mark_reviewed',
                'transaction_ids': [expense.id, unknown.id],
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        expense.refresh_from_db()
        self.assertEqual(expense.review_status, ReviewStatus.NOT_REQUIRED)

        response = self.client.post(
            '/api/transactions/bulk/',
            {
                'operation': 'classification',
                'transaction_ids': [expense.id],
                'classification': 'INCOME',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        expense.refresh_from_db()
        self.assertIsNone(expense.subcategory)
        self.assertEqual(expense.budget_bucket, '')

    def test_transaction_list_filters_review_state_account_and_reason(self):
        unknown = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 15),
            raw_description='Unknown transaction',
            amount=Decimal('-4.00'),
        )
        response = self.client.get('/api/transactions/?review_status=PENDING&review_reason=UNKNOWN')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.data['results']], [unknown.id])
        response = self.client.get('/api/transactions/?start_date=bad-date')
        self.assertEqual(response.status_code, 400)

    def test_transaction_api_orders_and_filters_server_side(self):
        wants = Subcategory.objects.create(name='Dining', budget_bucket=BudgetBucket.WANTS)
        other_account = Account.objects.create(name='Savings')
        older = Transaction.objects.create(
            account=self.account,
            subcategory=self.needs,
            date=date(2026, 1, 10),
            raw_description='Grocery market',
            merchant='Market',
            amount=Decimal('-20.00'),
            source_type='DEBIT_CARD',
            classification=Transaction.Classification.EXPENSE,
        )
        newer = Transaction.objects.create(
            account=other_account,
            subcategory=wants,
            date=date(2026, 1, 20),
            raw_description='Restaurant',
            merchant='Cafe',
            amount=Decimal('-8.00'),
            source_type='CARD',
            classification=Transaction.Classification.EXPENSE,
        )
        unknown = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 25),
            raw_description='From Emergency Fund Vault',
            amount=Decimal('500.00'),
            source_type='DEPOSIT',
            classification=Transaction.Classification.UNKNOWN,
        )

        response = self.client.get('/api/transactions/?ordering=date')
        self.assertEqual([item['id'] for item in response.data['results']], [older.id, newer.id, unknown.id])
        response = self.client.get('/api/transactions/?ordering=-amount')
        self.assertEqual([item['id'] for item in response.data['results']], [unknown.id, newer.id, older.id])
        for ordering in (
            'date', '-date', 'description', '-description', 'account', '-account',
            'source_type', '-source_type', 'classification', '-classification',
            'subcategory', '-subcategory', 'review_status', '-review_status', 'amount',
        ):
            with self.subTest(ordering=ordering):
                self.assertEqual(
                    self.client.get(f'/api/transactions/?ordering={ordering}').status_code,
                    200,
                )
        response = self.client.get('/api/transactions/?search=market&source_type=debit&account=%d' % self.account.id)
        self.assertEqual([item['id'] for item in response.data['results']], [older.id])
        response = self.client.get(f'/api/transactions/?subcategory={wants.id}&budget_bucket=WANTS')
        self.assertEqual([item['id'] for item in response.data['results']], [newer.id])
        response = self.client.get('/api/transactions/?subcategory=uncategorized&classification=UNKNOWN')
        self.assertEqual([item['id'] for item in response.data['results']], [unknown.id])

    def test_transaction_api_validates_ordering_and_filter_values(self):
        for query in (
            '?ordering=raw_description',
            '?subcategory=all',
            '?budget_bucket=OTHER',
            '?review_status=UNSEEN',
        ):
            with self.subTest(query=query):
                response = self.client.get(f'/api/transactions/{query}')
                self.assertEqual(response.status_code, 400)

    def test_transaction_api_paginates_with_stable_total_and_navigation(self):
        Transaction.objects.bulk_create([
            Transaction(
                account=self.account,
                date=date(2026, 1, 1),
                raw_description=f'Transaction {index}',
                amount=Decimal('-1.00'),
                classification=Transaction.Classification.EXPENSE,
                subcategory=self.needs,
                budget_bucket=BudgetBucket.NEEDS,
            )
            for index in range(101)
        ])
        first_page = self.client.get('/api/transactions/?ordering=date&page=1')
        second_page = self.client.get('/api/transactions/?ordering=date&page=2')

        self.assertEqual(first_page.data['count'], 101)
        self.assertEqual(len(first_page.data['results']), 100)
        self.assertIsNone(first_page.data['previous'])
        self.assertIsNotNone(first_page.data['next'])
        self.assertEqual(len(second_page.data['results']), 1)
        self.assertIsNotNone(second_page.data['previous'])
        self.assertIsNone(second_page.data['next'])

    def test_unknown_transaction_can_be_changed_to_transfer_and_reviewed_without_category(self):
        transaction = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 15),
            raw_description='From Emergency Fund Vault',
            amount=Decimal('100.00'),
            classification=Transaction.Classification.UNKNOWN,
        )
        classified = self.client.patch(
            f'/api/transactions/{transaction.id}/',
            {'classification': 'TRANSFER'},
            format='json',
        )
        self.assertEqual(classified.status_code, 200)
        self.assertEqual(classified.data['classification'], 'TRANSFER')
        self.assertIsNone(classified.data['subcategory'])
        self.assertEqual(classified.data['review_status'], ReviewStatus.PENDING)

        reviewed = self.client.post(f'/api/transactions/{transaction.id}/mark-reviewed/')
        self.assertEqual(reviewed.status_code, 200)
        self.assertEqual(reviewed.data['review_status'], ReviewStatus.REVIEWED)

    def test_transactions_are_retained_instead_of_deleted(self):
        transaction = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 15),
            raw_description='Auditable transaction',
            amount=Decimal('-4.00'),
        )
        response = self.client.delete(f'/api/transactions/{transaction.id}/')
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Transaction.objects.filter(pk=transaction.pk).exists())


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

    def test_generic_deposit_remains_unknown_regardless_of_positive_amount(self):
        parsed = parse_csv(
            b'Date,Description,Type,Amount,Status\n'
            b'2026-02-01,From Emergency Fund Vault,DEPOSIT,500.00,Posted\n'
        )
        self.assertEqual(parsed.rows[0]['classification'], 'UNKNOWN')

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


class OverlapImportTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(name='Checking')

    def row(self, source_row=1, description='MERCHANT X', amount='-10.00', source_type='DEBIT_CARD'):
        return {
            'source_row': source_row,
            'date': date(2026, 10, 10),
            'posted_date': None,
            'raw_description': description,
            'amount': Decimal(amount),
            'source_type': source_type,
            'classification': Transaction.Classification.EXPENSE,
        }

    def parsed(self, rows):
        return ParsedImport(rows, [], len(rows), 'synthetic-v1')

    def test_fingerprint_multiplicity_matches_existing_occurrences_and_flags_excess(self):
        repeated_rows = [self.row(1), self.row(2)]
        first_batch = commit_import(self.account, 'two.csv', b'two occurrences', self.parsed(repeated_rows))
        incoming = [self.row(1), self.row(2), self.row(3)]
        outcomes = overlap_results(self.account, incoming)

        self.assertEqual(
            [row['overlap_status'] for row in outcomes],
            ['ALREADY_REPRESENTED', 'ALREADY_REPRESENTED', 'POSSIBLE_DUPLICATE'],
        )
        second_batch = commit_import(self.account, 'three.csv', b'three occurrences', self.parsed(incoming))
        self.assertEqual(first_batch.imported_row_count, 2)
        self.assertEqual(second_batch.source_row_count, 3)
        self.assertEqual(second_batch.imported_row_count, 1)
        self.assertEqual(second_batch.already_represented_row_count, 2)
        self.assertEqual(second_batch.possible_duplicate_row_count, 1)
        self.assertEqual(Transaction.objects.count(), 3)
        possible = Transaction.objects.get(import_batch=second_batch)
        self.assertEqual(possible.duplicate_resolution, DuplicateResolution.UNRESOLVED)
        self.assertEqual(possible.duplicate_candidates.count(), 2)

    def test_possible_candidates_use_same_date_amount_and_matching_description_or_type(self):
        first = self.row(description='Merchant Alpha', source_type='DEBIT_CARD')
        commit_import(self.account, 'first.csv', b'first version', self.parsed([first]))
        similar = self.row(description='Merchant Beta', source_type='DEBIT_CARD')
        outcome = overlap_results(self.account, [similar])[0]

        self.assertEqual(outcome['overlap_status'], 'POSSIBLE_DUPLICATE')
        self.assertEqual([candidate.raw_description for candidate in outcome['duplicate_candidates']], ['Merchant Alpha'])

    def test_matching_is_account_scoped_and_identical_in_file_rows_are_not_collapsed(self):
        rows = [self.row(1), self.row(2)]
        other_account = Account.objects.create(name='Other Checking')
        commit_import(self.account, 'first.csv', b'first account', self.parsed([rows[0]]))

        self.assertEqual(
            [row['overlap_status'] for row in overlap_results(other_account, rows)],
            ['NEW', 'NEW'],
        )
        results = overlap_results(self.account, rows)
        self.assertEqual(
            [row['overlap_status'] for row in results],
            ['ALREADY_REPRESENTED', 'POSSIBLE_DUPLICATE'],
        )

    def test_description_fingerprint_ignores_case_and_repeated_whitespace(self):
        commit_import(
            self.account,
            'first.csv',
            b'first version',
            self.parsed([self.row(description='Merchant   X')]),
        )
        incoming = self.row(description='  MERCHANT X  ')
        self.assertEqual(
            overlap_results(self.account, [incoming])[0]['overlap_status'],
            'ALREADY_REPRESENTED',
        )


class CategorizationRuleTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(name='Checking')
        self.wants = Subcategory.objects.create(name='Fast Food', budget_bucket=BudgetBucket.WANTS)
        self.needs = Subcategory.objects.create(name='Restaurants', budget_bucket=BudgetBucket.NEEDS)
        self.rule = CategorizationRule.objects.create(
            name='Popeyes',
            match_value='POPEYES',
            classification=Transaction.Classification.EXPENSE,
            subcategory=self.wants,
        )

    def transaction_record(self, description='POPEYES 13171', classification='EXPENSE', **kwargs):
        return Transaction.objects.create(
            account=self.account,
            date=date(2026, 11, 1),
            raw_description=description,
            amount=Decimal('-12.00'),
            classification=classification,
            **kwargs,
        )

    def test_normalization_trims_collapses_whitespace_and_case_folds_only(self):
        self.assertEqual(normalize_rule_text('  Popeyes\t  13171  '), 'popeyes 13171')
        self.assertNotEqual(normalize_rule_text('POPEYES*'), normalize_rule_text('POPEYES'))

    def test_rule_matches_expense_but_not_unknown_or_transfer_popeyes_rows(self):
        self.rule.match_value = 'POPEYES 13171'
        self.assertTrue(rule_matches(self.rule, 'EXPENSE', 'POPEYES  13171'))
        self.assertFalse(rule_matches(self.rule, 'UNKNOWN', 'Roundup *Popeyes 13171'))
        self.assertFalse(rule_matches(self.rule, 'TRANSFER', 'Roundup *Popeyes 13171'))
        self.assertFalse(rule_matches(self.rule, 'REFUND', 'Popeyes refund'))
        self.assertFalse(rule_matches(self.rule, 'EXPENSE', 'Different merchant'))

    def test_same_target_multiple_matches_are_allowed_but_different_targets_conflict(self):
        second_same_target = CategorizationRule.objects.create(
            match_value='Popeyes 13171',
            subcategory=self.wants,
        )
        result = evaluate_rules('EXPENSE', 'Popeyes 13171', [self.rule, second_same_target])
        self.assertFalse(result['conflict'])
        self.assertEqual(result['subcategory'], self.wants)
        self.assertEqual(result['rule'], self.rule)

        conflicting = CategorizationRule.objects.create(
            match_value='13171',
            subcategory=self.needs,
        )
        result = evaluate_rules('EXPENSE', 'Popeyes 13171', [self.rule, second_same_target, conflicting])
        self.assertTrue(result['conflict'])
        self.assertIsNone(result['subcategory'])
        self.assertEqual(len(result['rules']), 3)

    def test_archived_target_is_not_applied_and_manual_edit_clears_provenance(self):
        record = self.transaction_record()
        apply_rule_to_transaction(record, self.rule)
        record.refresh_from_db()
        self.assertEqual(record.subcategory, self.wants)
        self.assertEqual(record.budget_bucket, BudgetBucket.WANTS)
        self.assertEqual(record.categorized_by_rule, self.rule)

        record.subcategory = self.needs
        record.save()
        record.refresh_from_db()
        self.assertIsNone(record.categorized_by_rule)
        self.assertEqual(record.budget_bucket, BudgetBucket.NEEDS)

        self.wants.is_active = False
        self.wants.save()
        self.assertFalse(rule_matches(self.rule, 'EXPENSE', 'Popeyes 13171'))
        self.wants.is_active = True
        self.wants.budget_bucket = BudgetBucket.SAVINGS
        self.wants.save()
        self.assertFalse(rule_matches(self.rule, 'EXPENSE', 'Popeyes 13171'))


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
        self.assertEqual(preview.data['new_row_count'], 6)
        self.assertEqual(preview.data['review_required_row_count'], 5)
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
        self.assertEqual(batch.already_represented_row_count, 0)
        self.assertEqual(batch.possible_duplicate_row_count, 0)
        self.assertEqual([transaction.source_row for transaction in transactions], [1, 2, 3, 4, 5, 6])
        self.assertEqual(transactions[0].source_type, 'DEBIT_CARD')
        self.assertEqual(transactions[2].classification, 'UNKNOWN')
        self.assertIsNone(transactions[0].posted_date)

    def test_preview_and_confirmation_apply_rules_but_do_not_infer_classification(self):
        fast_food = Subcategory.objects.create(name='Fast Food', budget_bucket=BudgetBucket.WANTS)
        rule = CategorizationRule.objects.create(match_value='POPEYES', subcategory=fast_food)
        content = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-11-01,POPEYES 13171,DEBIT_CARD,-12.00,Posted\n'
            b'2026-11-02,Roundup *Popeyes 13171,ROUNDUP,-0.25,Posted\n'
        )
        preview = self.client.post(
            '/api/imports/preview/',
            {'account': self.account.id, 'file': self.upload('rules.csv', content)},
            format='multipart',
        )
        self.assertEqual(preview.status_code, 200)
        expense, roundup = preview.data['valid_rows']
        self.assertEqual(expense['classification'], 'EXPENSE')
        self.assertEqual(expense['categorization']['status'], 'AUTO_CATEGORIZED')
        self.assertEqual(expense['categorization']['subcategory']['name'], 'Fast Food')
        self.assertEqual(roundup['classification'], 'UNKNOWN')
        self.assertEqual(roundup['categorization']['status'], 'NOT_CATEGORIZED')
        self.assertEqual(preview.data['review_required_row_count'], 1)

        response = self.client.post(
            '/api/imports/',
            {'account': self.account.id, 'file': self.upload('rules-confirm.csv', content)},
            format='multipart',
        )
        self.assertEqual(response.status_code, 201)
        imported = list(Transaction.objects.order_by('source_row'))
        self.assertEqual(imported[0].subcategory, fast_food)
        self.assertEqual(imported[0].budget_bucket, BudgetBucket.WANTS)
        self.assertEqual(imported[0].categorized_by_rule, rule)
        self.assertEqual(imported[0].review_status, ReviewStatus.NOT_REQUIRED)
        self.assertIsNone(imported[1].subcategory)
        self.assertEqual(imported[1].review_status, ReviewStatus.PENDING)

        imported[1].classification = Transaction.Classification.TRANSFER
        imported[1].save()
        self.assertFalse(rule_matches(rule, imported[1].classification, imported[1].raw_description))

    def test_preview_displays_rule_conflicts_and_confirmation_keeps_them_pending(self):
        fast_food = Subcategory.objects.create(name='Fast Food', budget_bucket=BudgetBucket.WANTS)
        dining = Subcategory.objects.create(name='Dining', budget_bucket=BudgetBucket.NEEDS)
        CategorizationRule.objects.create(match_value='POPEYES', subcategory=fast_food)
        CategorizationRule.objects.create(match_value='13171', subcategory=dining)
        content = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-11-01,POPEYES 13171,DEBIT_CARD,-12.00,Posted\n'
        )
        preview = self.client.post(
            '/api/imports/preview/',
            {'account': self.account.id, 'file': self.upload('conflict.csv', content)},
            format='multipart',
        )
        self.assertEqual(preview.data['valid_rows'][0]['categorization']['status'], 'RULE_CONFLICT')
        self.assertEqual(
            {rule['subcategory_name'] for rule in preview.data['valid_rows'][0]['categorization']['rules']},
            {'Fast Food', 'Dining'},
        )
        self.assertEqual(preview.data['review_required_row_count'], 1)

        self.client.post(
            '/api/imports/',
            {'account': self.account.id, 'file': self.upload('conflict-confirm.csv', content)},
            format='multipart',
        )
        imported = Transaction.objects.get(import_batch__isnull=False)
        self.assertIsNone(imported.subcategory)
        self.assertTrue(imported.categorization_rule_conflict)
        self.assertEqual(imported.review_status, ReviewStatus.PENDING)
        self.assertIn('RULE_CONFLICT', imported.review_blockers())

    def test_possible_duplicate_may_be_categorized_but_stays_pending(self):
        fast_food = Subcategory.objects.create(name='Fast Food', budget_bucket=BudgetBucket.WANTS)
        CategorizationRule.objects.create(match_value='POPEYES', subcategory=fast_food)
        first = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-11-01,POPEYES original,DEBIT_CARD,-12.00,Posted\n'
        )
        self.client.post(
            '/api/imports/',
            {'account': self.account.id, 'file': self.upload('first.csv', first)},
            format='multipart',
        )
        second = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-11-01,POPEYES changed,DEBIT_CARD,-12.00,Posted\n'
        )
        self.client.post(
            '/api/imports/',
            {'account': self.account.id, 'file': self.upload('second.csv', second)},
            format='multipart',
        )
        possible = Transaction.objects.get(raw_description='POPEYES changed')
        self.assertEqual(possible.subcategory, fast_food)
        self.assertEqual(possible.duplicate_resolution, DuplicateResolution.UNRESOLVED)
        self.assertEqual(possible.review_status, ReviewStatus.PENDING)

    def test_preview_shows_overlap_candidates_without_writing(self):
        existing_content = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-02-01,Same Coffee,DEBIT_CARD,-4.00,Posted\n'
        )
        commit_import(
            self.account,
            'first.csv',
            existing_content,
            parse_csv(existing_content),
        )
        overlap_content = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-02-01,Same Coffee,DEBIT_CARD,-4.00,Posted\n'
            b'2026-02-01,Changed Coffee,DEBIT_CARD,-4.00,Posted\n'
            b'2026-02-02,New transaction,ROUNDUP,-0.75,Posted\n'
        )
        preview = self.client.post(
            '/api/imports/preview/',
            {'account': self.account.id, 'file': self.upload('overlap.csv', overlap_content)},
            format='multipart',
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.data['new_row_count'], 1)
        self.assertEqual(preview.data['already_represented_row_count'], 1)
        self.assertEqual(preview.data['possible_duplicate_row_count'], 1)
        possible = next(row for row in preview.data['valid_rows'] if row['overlap_status'] == 'POSSIBLE_DUPLICATE')
        self.assertEqual(possible['duplicate_candidates'][0]['raw_description'], 'Same Coffee')
        self.assertEqual(ImportBatch.objects.count(), 1)
        self.assertEqual(Transaction.objects.count(), 1)

    def test_confirm_recomputes_overlap_and_records_only_nonrepresented_rows(self):
        content = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-02-01,One transaction,DEBIT_CARD,-4.00,Posted\n'
        )
        parsed = parse_csv(content)
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 2, 1),
            raw_description='One transaction',
            amount=Decimal('-4.00'),
            source_type='DEBIT_CARD',
            classification=Transaction.Classification.EXPENSE,
        )
        batch = commit_import(self.account, 'same-rows.csv', b'not-the-same-file', parsed)

        self.assertEqual(batch.imported_row_count, 0)
        self.assertEqual(batch.already_represented_row_count, 1)
        self.assertEqual(Transaction.objects.count(), 1)

    def test_source_fields_cannot_be_changed_through_transaction_api(self):
        batch = commit_import(self.account, 'source.csv', SYNTHETIC_CSV, parse_csv(SYNTHETIC_CSV))
        record = Transaction.objects.filter(import_batch=batch).first()
        attempted_updates = (
            {'account': Account.objects.create(name='Other').id},
            {'date': '2026-03-01'},
            {'posted_date': '2026-03-01'},
            {'raw_description': 'overwritten'},
            {'amount': '-999.00'},
            {'source_type': 'MANUAL'},
            {'import_batch': None},
            {'source_row': 999},
        )
        for update in attempted_updates:
            response = self.client.patch(
                f'/api/transactions/{record.id}/',
                update,
                format='json',
            )
            self.assertEqual(response.status_code, 400, update)
            self.assertTrue(
                any('cannot be edited' in str(value) for value in response.data.values()),
                response.data,
            )

    def test_possible_duplicate_context_and_resolution_remain_separate_from_classification(self):
        first_content = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-02-01,Merchant Alpha,DEBIT_CARD,-4.00,Posted\n'
        )
        commit_import(self.account, 'first.csv', first_content, parse_csv(first_content))
        second_content = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-02-01,Merchant Beta,DEBIT_CARD,-4.00,Posted\n'
        )
        batch = commit_import(
            self.account,
            'second.csv',
            second_content,
            parse_csv(second_content),
        )
        possible = Transaction.objects.get(import_batch=batch)
        response = self.client.get(f'/api/transactions/{possible.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['duplicate_candidates'][0]['raw_description'], 'Merchant Alpha')

        candidate_id = possible.duplicate_candidates.get().id
        response = self.client.post(
            f'/api/transactions/{possible.id}/resolve-duplicate/',
            {'resolution': 'CONFIRMED_DUPLICATE', 'duplicate_of': candidate_id},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        possible.refresh_from_db()
        self.assertEqual(possible.duplicate_resolution, DuplicateResolution.CONFIRMED_DUPLICATE)
        self.assertEqual(possible.duplicate_of_id, candidate_id)
        self.assertEqual(possible.classification, Transaction.Classification.EXPENSE)
        self.assertEqual(possible.review_status, ReviewStatus.REVIEWED)

    def test_marking_possible_duplicate_distinct_keeps_normal_review_requirements(self):
        first_content = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-02-01,Merchant Alpha,DEBIT_CARD,-4.00,Posted\n'
        )
        commit_import(self.account, 'first.csv', first_content, parse_csv(first_content))
        second_content = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-02-01,Merchant Beta,DEBIT_CARD,-4.00,Posted\n'
        )
        batch = commit_import(self.account, 'second.csv', second_content, parse_csv(second_content))
        possible = Transaction.objects.get(import_batch=batch)

        response = self.client.post(
            f'/api/transactions/{possible.id}/resolve-duplicate/',
            {'resolution': 'DISTINCT'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        possible.refresh_from_db()
        self.assertEqual(possible.duplicate_resolution, DuplicateResolution.DISTINCT)
        self.assertEqual(possible.review_status, ReviewStatus.PENDING)
        self.assertIsNone(possible.duplicate_of)
        self.assertEqual(possible.classification, Transaction.Classification.EXPENSE)

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

    def test_category_mutations_require_csrf_token(self):
        body = {'name': 'Groceries', 'budget_bucket': BudgetBucket.NEEDS}
        rejected = self.client.post(
            '/api/subcategories/',
            body,
            content_type='application/json',
            HTTP_ORIGIN='http://localhost:5173',
        )
        self.assertEqual(rejected.status_code, 403)

        token = self.client.get('/api/csrf/').json()['csrfToken']
        accepted = self.client.post(
            '/api/subcategories/',
            body,
            content_type='application/json',
            HTTP_ORIGIN='http://localhost:5173',
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(accepted.status_code, 201)
        self.assertEqual(Subcategory.objects.count(), 1)

        bulk_body = {'names': 'Fuel, Utilities', 'budget_bucket': BudgetBucket.NEEDS}
        rejected_bulk = self.client.post(
            '/api/subcategories/bulk-create/',
            bulk_body,
            content_type='application/json',
            HTTP_ORIGIN='http://localhost:5173',
        )
        self.assertEqual(rejected_bulk.status_code, 403)
        accepted_bulk = self.client.post(
            '/api/subcategories/bulk-create/',
            bulk_body,
            content_type='application/json',
            HTTP_ORIGIN='http://localhost:5173',
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(accepted_bulk.status_code, 201)
        self.assertEqual(Subcategory.objects.count(), 3)

    def test_categorization_rule_mutations_require_csrf_token(self):
        category = Subcategory.objects.create(name='Fast Food', budget_bucket=BudgetBucket.WANTS)
        body = {
            'match_field': 'RAW_DESCRIPTION',
            'match_type': 'CONTAINS',
            'match_value': 'POPEYES',
            'classification': 'EXPENSE',
            'subcategory': category.id,
        }
        rejected = self.client.post(
            '/api/categorization-rules/',
            body,
            content_type='application/json',
            HTTP_ORIGIN='http://localhost:5173',
        )
        self.assertEqual(rejected.status_code, 403)

        token = self.client.get('/api/csrf/').json()['csrfToken']
        accepted = self.client.post(
            '/api/categorization-rules/',
            body,
            content_type='application/json',
            HTTP_ORIGIN='http://localhost:5173',
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(accepted.status_code, 201)
        self.assertEqual(CategorizationRule.objects.count(), 1)


class CategorizationRuleApiTests(APITestCase):
    def setUp(self):
        self.account = Account.objects.create(name='Checking')
        self.wants = Subcategory.objects.create(name='Fast Food', budget_bucket=BudgetBucket.WANTS)
        self.needs = Subcategory.objects.create(name='Restaurants', budget_bucket=BudgetBucket.NEEDS)

    def create_rule(self, value='POPEYES', subcategory=None, **extra):
        return CategorizationRule.objects.create(
            match_value=value,
            subcategory=subcategory or self.wants,
            **extra,
        )

    def create_transaction(self, description='POPEYES 13171', **extra):
        return Transaction.objects.create(
            account=self.account,
            date=date(2026, 11, 1),
            raw_description=description,
            amount=Decimal('-12.00'),
            classification=Transaction.Classification.EXPENSE,
            **extra,
        )

    def payload(self, **overrides):
        return {
            'match_field': 'RAW_DESCRIPTION',
            'match_type': 'CONTAINS',
            'match_value': 'POPEYES',
            'classification': 'EXPENSE',
            'subcategory': self.wants.id,
            **overrides,
        }

    def test_rule_crud_normalizes_values_and_rejects_normalized_active_duplicates(self):
        created = self.client.post(
            '/api/categorization-rules/',
            self.payload(match_value='  Popeyes\t  13171  '),
            format='json',
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data['match_value'], 'Popeyes 13171')
        duplicate = self.client.post(
            '/api/categorization-rules/',
            self.payload(match_value='POPEYES 13171'),
            format='json',
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn('active rule already', str(duplicate.data))

        unsupported = self.client.post(
            '/api/categorization-rules/',
            self.payload(match_field='MERCHANT'),
            format='json',
        )
        self.assertEqual(unsupported.status_code, 400)
        equals = self.client.post(
            '/api/categorization-rules/',
            self.payload(match_type='EQUALS'),
            format='json',
        )
        self.assertEqual(equals.status_code, 400)

    def test_archived_and_classification_incompatible_targets_are_rejected(self):
        self.wants.is_active = False
        self.wants.save()
        archived = self.client.post('/api/categorization-rules/', self.payload(), format='json')
        self.assertEqual(archived.status_code, 400)
        self.wants.is_active = True
        self.wants.save()

        savings = Subcategory.objects.create(name='Savings goal', budget_bucket=BudgetBucket.SAVINGS)
        invalid_bucket = self.client.post(
            '/api/categorization-rules/',
            self.payload(subcategory=savings.id),
            format='json',
        )
        self.assertEqual(invalid_bucket.status_code, 400)
        invalid_classification = self.client.post(
            '/api/categorization-rules/',
            self.payload(classification='UNKNOWN'),
            format='json',
        )
        self.assertEqual(invalid_classification.status_code, 400)

    def test_manual_override_and_rule_edits_preserve_historical_assignment(self):
        rule = self.create_rule()
        record = self.create_transaction(subcategory=self.wants, categorized_by_rule=rule)
        response = self.client.patch(
            f'/api/transactions/{record.id}/',
            {'subcategory': self.needs.id},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        record.refresh_from_db()
        self.assertEqual(record.subcategory, self.needs)
        self.assertEqual(record.budget_bucket, BudgetBucket.NEEDS)
        self.assertIsNone(record.categorized_by_rule)

        record.categorized_by_rule = rule
        record.subcategory = self.wants
        record.budget_bucket = BudgetBucket.WANTS
        record.save()
        response = self.client.patch(
            f'/api/categorization-rules/{rule.id}/',
            {'match_value': 'POPEYES NEW'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        record.refresh_from_db()
        self.assertEqual(record.subcategory, self.wants)
        self.assertEqual(record.budget_bucket, BudgetBucket.WANTS)
        self.assertEqual(record.categorized_by_rule, rule)

        disabled = self.client.patch(
            f'/api/categorization-rules/{rule.id}/',
            {'is_active': False},
            format='json',
        )
        self.assertEqual(disabled.status_code, 200)
        record.refresh_from_db()
        self.assertEqual(record.subcategory, self.wants)
        self.assertEqual(record.categorized_by_rule, rule)
        self.assertEqual(
            self.client.delete(f'/api/categorization-rules/{rule.id}/').status_code,
            409,
        )

        same_category = self.create_transaction(
            'POPEYES same category',
            subcategory=self.wants,
            categorized_by_rule=rule,
        )
        response = self.client.patch(
            f'/api/transactions/{same_category.id}/',
            {'subcategory': self.wants.id},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        same_category.refresh_from_db()
        self.assertIsNone(same_category.categorized_by_rule)

    def test_review_edit_creates_rule_only_after_explicit_opt_in_and_is_atomic(self):
        record = self.create_transaction()
        response = self.client.patch(
            f'/api/transactions/{record.id}/',
            {'subcategory': self.wants.id},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CategorizationRule.objects.count(), 0)

        other = self.create_transaction('POPEYES second')
        response = self.client.post(
            f'/api/transactions/{record.id}/save-with-rule/',
            {
                'classification': 'EXPENSE',
                'subcategory': self.wants.id,
                'merchant': '',
                'notes': '',
                'match_value': '  POPEYES  ',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['rule']['match_value'], 'POPEYES')
        self.assertEqual(response.data['matching_pending_count'], 1)
        self.assertEqual(response.data['rule']['subcategory'], self.wants.id)
        self.assertIsNone(response.data['transaction']['categorized_by_rule'])
        self.assertEqual(CategorizationRule.objects.count(), 1)
        self.assertIsNone(other.subcategory)

        uncategorized = self.create_transaction('Other merchant')
        failed = self.client.post(
            f'/api/transactions/{uncategorized.id}/save-with-rule/',
            {
                'classification': 'EXPENSE',
                'subcategory': self.wants.id,
                'match_value': 'POPEYES',
            },
            format='json',
        )
        self.assertEqual(failed.status_code, 400)
        uncategorized.refresh_from_db()
        self.assertIsNone(uncategorized.subcategory)

    def test_apply_pending_searches_past_page_and_recomputes_review_state(self):
        rule = self.create_rule()
        ordinary = [
            self.create_transaction(f'POPEYES {number}')
            for number in range(101)
        ]
        possible_duplicate = self.create_transaction(
            'POPEYES unresolved',
            duplicate_resolution=DuplicateResolution.UNRESOLVED,
        )
        counts = self.client.get(f'/api/categorization-rules/{rule.id}/pending-matches/')
        self.assertEqual(counts.status_code, 200)
        self.assertEqual(counts.data['matching_pending_count'], 102)

        response = self.client.post(f'/api/categorization-rules/{rule.id}/apply-to-pending/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['updated_count'], 102)
        self.assertEqual(response.data['conflicting_count'], 0)
        ordinary[0].refresh_from_db()
        possible_duplicate.refresh_from_db()
        self.assertEqual(ordinary[0].subcategory, self.wants)
        self.assertEqual(ordinary[0].review_status, ReviewStatus.NOT_REQUIRED)
        self.assertEqual(possible_duplicate.subcategory, self.wants)
        self.assertEqual(possible_duplicate.review_status, ReviewStatus.PENDING)
        self.assertEqual(possible_duplicate.duplicate_resolution, DuplicateResolution.UNRESOLVED)

    def test_conflicting_pending_matches_are_not_applied(self):
        rule = self.create_rule()
        CategorizationRule.objects.create(match_value='13171', subcategory=self.needs)
        record = self.create_transaction()
        counts = self.client.get(f'/api/categorization-rules/{rule.id}/pending-matches/')
        self.assertEqual(counts.data['matching_pending_count'], 0)
        self.assertEqual(counts.data['conflicting_pending_count'], 1)

        response = self.client.post(f'/api/categorization-rules/{rule.id}/apply-to-pending/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['updated_count'], 0)
        self.assertEqual(response.data['conflicting_count'], 1)
        record.refresh_from_db()
        self.assertIsNone(record.subcategory)
        self.assertEqual(record.review_status, ReviewStatus.PENDING)

    def test_apply_pending_is_atomic_when_a_transaction_cannot_be_saved(self):
        rule = self.create_rule()
        first = self.create_transaction('POPEYES first')
        second = self.create_transaction('POPEYES second')
        original_save = Transaction.save

        def fail_second(record, *args, **kwargs):
            if record.pk == second.pk and 'subcategory' in kwargs.get('update_fields', ()):
                raise DjangoValidationError({'subcategory': 'synthetic failure'})
            return original_save(record, *args, **kwargs)

        with patch.object(Transaction, 'save', fail_second):
            response = self.client.post(f'/api/categorization-rules/{rule.id}/apply-to-pending/')
        self.assertEqual(response.status_code, 400)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNone(first.subcategory)
        self.assertIsNone(second.subcategory)
