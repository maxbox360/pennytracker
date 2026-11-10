from pathlib import Path
from unittest.mock import patch
from datetime import date
from decimal import Decimal

from django.test import Client, TestCase
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TransactionTestCase
from rest_framework.test import APITestCase

from .categorization_rules import (
    apply_rule_to_transaction,
    evaluate_rules,
    normalize_rule_text,
    pending_matches_for_rule,
    rule_matches,
)
from .accounting import (
    all_time_report,
    category_trend,
    drilldown_queryset,
    drilldown_rows,
    monthly_report,
    range_report,
    year_report,
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
    BudgetTargetSettings,
    CategorizationRule,
    DuplicateResolution,
    ImportBatch,
    ReviewStatus,
    SavingsEffect,
    SubcategoryScope,
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

    def test_unchanged_assignments_remain_editable_after_subcategory_bucket_moves(self):
        subcategory = Subcategory.objects.create(name='Groceries', budget_bucket=BudgetBucket.NEEDS)
        transaction = Transaction.objects.create(
            account=self.account,
            subcategory=subcategory,
            date=date(2026, 1, 15),
            raw_description='Market',
            amount=Decimal('-42.50'),
            classification=Transaction.Classification.EXPENSE,
        )
        subcategory.budget_bucket = BudgetBucket.WANTS
        subcategory.save()

        transaction.refresh_from_db()
        transaction.notes = 'Updated after category moved'
        transaction.save()
        self.assertEqual(transaction.budget_bucket, BudgetBucket.NEEDS)

        savings = Subcategory.objects.create(name='Emergency Fund', budget_bucket=BudgetBucket.SAVINGS)
        transfer = Transaction.objects.create(
            account=self.account,
            subcategory=savings,
            date=date(2026, 1, 16),
            raw_description='Savings transfer',
            amount=Decimal('-100.00'),
            classification=Transaction.Classification.TRANSFER,
            savings_effect=SavingsEffect.CONTRIBUTION,
        )
        savings.budget_bucket = BudgetBucket.WANTS
        savings.save()

        transfer.refresh_from_db()
        transfer.notes = 'Updated after category moved'
        transfer.save()
        self.assertEqual(transfer.budget_bucket, BudgetBucket.SAVINGS)
        self.assertEqual(transfer.savings_effect, SavingsEffect.CONTRIBUTION)

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

    def test_other_inflow_subcategory_has_no_budget_snapshot_and_is_classification_scoped(self):
        other = Subcategory.objects.create(
            name='Gifts',
            scope=SubcategoryScope.OTHER_INFLOW,
            budget_bucket=None,
        )
        incoming = Transaction.objects.create(
            account=self.account,
            subcategory=other,
            date=date(2026, 1, 15),
            raw_description='Synthetic birthday gift',
            amount=Decimal('50.00'),
            classification=Transaction.Classification.OTHER_INFLOW,
        )
        self.assertEqual(incoming.budget_bucket, '')
        self.assertEqual(incoming.review_blockers(), [])

        incoming.classification = Transaction.Classification.EXPENSE
        incoming.save(update_fields=('classification',))
        incoming.refresh_from_db()
        self.assertIsNone(incoming.subcategory)
        self.assertEqual(incoming.budget_bucket, '')

        budget = Subcategory.objects.create(name='Groceries', budget_bucket=BudgetBucket.NEEDS)
        invalid = Transaction(
            account=self.account,
            subcategory=budget,
            date=date(2026, 1, 16),
            raw_description='Synthetic gift',
            amount=Decimal('10.00'),
            classification=Transaction.Classification.OTHER_INFLOW,
        )
        with self.assertRaises(DjangoValidationError):
            invalid.save()

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
            savings_effect=SavingsEffect.CONTRIBUTION,
        )
        self.assertEqual(transaction.budget_bucket, BudgetBucket.SAVINGS)
        self.assertEqual(transaction.review_status, ReviewStatus.PENDING)

    def test_amount_uses_decimal_precision_and_raw_description_is_preserved(self):
        transaction = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 15),
            raw_description='CARD PURCHASE COFFEE SHOP 1234',
            merchant='Local Coffee Roaster',
            amount=Decimal('-1.29'),
        )

        transaction.refresh_from_db()
        self.assertEqual(transaction.amount, Decimal('-1.29'))
        self.assertEqual(transaction.raw_description, 'CARD PURCHASE COFFEE SHOP 1234')
        self.assertEqual(transaction.merchant, 'Local Coffee Roaster')


class SubcategoryScopeMigrationTests(TransactionTestCase):
    def test_existing_subcategories_and_transaction_snapshots_are_preserved(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('finance', '0007_budgettargetsettings_transaction_savings_effect_and_more')])
        old_apps = executor.loader.project_state(
            [('finance', '0007_budgettargetsettings_transaction_savings_effect_and_more')]
        ).apps
        Account = old_apps.get_model('finance', 'Account')
        Subcategory = old_apps.get_model('finance', 'Subcategory')
        Transaction = old_apps.get_model('finance', 'Transaction')
        account = Account.objects.create(name='Migration test account')
        subcategory = Subcategory.objects.create(name='Migration test category', budget_bucket='WANTS')
        record = Transaction.objects.create(
            account_id=account.pk,
            subcategory_id=subcategory.pk,
            date=date(2026, 1, 15),
            raw_description='Synthetic migration transaction',
            amount=Decimal('-12.00'),
            classification='EXPENSE',
            budget_bucket='WANTS',
        )

        try:
            executor = MigrationExecutor(connection)
            executor.migrate([('finance', '0008_remove_categorizationrule_valid_rule_classification_and_more')])
            new_apps = executor.loader.project_state(
                [('finance', '0008_remove_categorizationrule_valid_rule_classification_and_more')]
            ).apps
            NewSubcategory = new_apps.get_model('finance', 'Subcategory')
            NewTransaction = new_apps.get_model('finance', 'Transaction')
            migrated_category = NewSubcategory.objects.get(pk=subcategory.pk)
            migrated_transaction = NewTransaction.objects.get(pk=record.pk)
            self.assertEqual(migrated_category.scope, SubcategoryScope.BUDGET)
            self.assertEqual(migrated_category.budget_bucket, BudgetBucket.WANTS)
            self.assertEqual(migrated_transaction.budget_bucket, BudgetBucket.WANTS)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())


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

    def test_same_subcategory_can_be_resubmitted_after_its_bucket_moves(self):
        expense = Transaction.objects.create(
            account=self.account,
            subcategory=self.needs,
            date=date(2026, 1, 15),
            raw_description='Market',
            amount=Decimal('-42.50'),
            classification=Transaction.Classification.EXPENSE,
        )
        self.needs.budget_bucket = BudgetBucket.WANTS
        self.needs.save()
        response = self.client.patch(
            f'/api/transactions/{expense.pk}/',
            {'subcategory': self.needs.pk},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['budget_bucket'], BudgetBucket.NEEDS)

        savings = Subcategory.objects.create(name='Emergency Fund', budget_bucket=BudgetBucket.SAVINGS)
        transfer = Transaction.objects.create(
            account=self.account,
            subcategory=savings,
            date=date(2026, 1, 16),
            raw_description='Savings transfer',
            amount=Decimal('-100.00'),
            classification=Transaction.Classification.TRANSFER,
            savings_effect=SavingsEffect.CONTRIBUTION,
        )
        savings.budget_bucket = BudgetBucket.NEEDS
        savings.save()
        response = self.client.patch(
            f'/api/transactions/{transfer.pk}/',
            {'subcategory': savings.pk},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['budget_bucket'], BudgetBucket.SAVINGS)
        self.assertEqual(response.data['savings_effect'], SavingsEffect.CONTRIBUTION)

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

    def test_other_inflow_categories_support_bulk_lifecycle_without_budget_buckets(self):
        response = self.client.post(
            '/api/subcategories/bulk-create/',
            {'names': 'Gambling Winnings, Gifts', 'scope': SubcategoryScope.OTHER_INFLOW},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['count'], 2)
        self.assertTrue(all(
            item['scope'] == SubcategoryScope.OTHER_INFLOW and item['budget_bucket'] is None
            for item in response.data['created']
        ))

        category_id = response.data['created'][0]['id']
        changed = self.client.patch(
            f'/api/subcategories/{category_id}/',
            {'name': 'Prize Winnings'},
            format='json',
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.data['name'], 'Prize Winnings')
        archived = self.client.patch(
            f'/api/subcategories/{category_id}/',
            {'is_active': False},
            format='json',
        )
        self.assertEqual(archived.status_code, 200)
        reactivated = self.client.patch(
            f'/api/subcategories/{category_id}/',
            {'is_active': True},
            format='json',
        )
        self.assertEqual(reactivated.status_code, 200)
        self.assertEqual(reactivated.data['scope'], SubcategoryScope.OTHER_INFLOW)
        self.assertIsNone(reactivated.data['budget_bucket'])

    def test_transaction_api_enforces_other_inflow_category_scope(self):
        other = Subcategory.objects.create(
            name='Gifts',
            scope=SubcategoryScope.OTHER_INFLOW,
            budget_bucket=None,
        )
        budget = self.needs
        incoming = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 15),
            raw_description='Synthetic payment from friend',
            amount=Decimal('50.00'),
            classification=Transaction.Classification.UNKNOWN,
        )
        response = self.client.patch(
            f'/api/transactions/{incoming.pk}/',
            {'classification': 'OTHER_INFLOW', 'subcategory': other.pk},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['budget_bucket'], '')

        for classification in ('EXPENSE', 'REFUND', 'TRANSFER'):
            with self.subTest(classification=classification):
                record = Transaction.objects.create(
                    account=self.account,
                    date=date(2026, 1, 16),
                    raw_description='Synthetic transaction',
                    amount=Decimal('-10.00') if classification != 'REFUND' else Decimal('10.00'),
                    classification=classification,
                )
                invalid = self.client.patch(
                    f'/api/transactions/{record.pk}/',
                    {'subcategory': other.pk},
                    format='json',
                )
                self.assertEqual(invalid.status_code, 400)
                self.assertIn('subcategory', invalid.data)

        bad_inflow = self.client.post(
            '/api/transactions/',
            {
                'account': self.account.pk,
                'date': '2026-01-17',
                'raw_description': 'Synthetic gift',
                'amount': '10.00',
                'classification': 'OTHER_INFLOW',
                'subcategory': budget.pk,
            },
            format='json',
        )
        self.assertEqual(bad_inflow.status_code, 400)
        self.assertIn('subcategory', bad_inflow.data)

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
            {'subcategory': savings.id, 'savings_effect': 'CONTRIBUTION'},
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
            name='Example Cafe',
            match_value='COFFEE SHOP',
            classification=Transaction.Classification.EXPENSE,
            subcategory=self.wants,
        )

    def transaction_record(self, description='COFFEE SHOP 1234', classification='EXPENSE', **kwargs):
        return Transaction.objects.create(
            account=self.account,
            date=date(2026, 11, 1),
            raw_description=description,
            amount=Decimal('-12.00'),
            classification=classification,
            **kwargs,
        )

    def test_normalization_trims_collapses_whitespace_and_case_folds_only(self):
        self.assertEqual(normalize_rule_text('  COFFEE SHOP\t  1234  '), 'coffee shop 1234')
        self.assertNotEqual(normalize_rule_text('COFFEE SHOP*'), normalize_rule_text('COFFEE SHOP'))

    def test_rule_matches_expense_but_not_unknown_or_transfer_roundup_rows(self):
        self.rule.match_value = 'COFFEE SHOP'
        self.assertTrue(rule_matches(self.rule, 'EXPENSE', 'COFFEE SHOP  1234'))
        self.assertFalse(rule_matches(self.rule, 'UNKNOWN', 'ROUNDUP *COFFEE SHOP 1234'))
        self.assertFalse(rule_matches(self.rule, 'TRANSFER', 'ROUNDUP *COFFEE SHOP 1234'))
        self.assertFalse(rule_matches(self.rule, 'REFUND', 'Example Cafe refund'))
        self.assertFalse(rule_matches(self.rule, 'EXPENSE', 'Different merchant'))

    def test_same_target_multiple_matches_are_allowed_but_different_targets_conflict(self):
        second_same_target = CategorizationRule.objects.create(
            match_value='COFFEE SHOP 1234',
            subcategory=self.wants,
        )
        result = evaluate_rules('EXPENSE', 'COFFEE SHOP 1234', [self.rule, second_same_target])
        self.assertFalse(result['conflict'])
        self.assertEqual(result['subcategory'], self.wants)
        self.assertEqual(result['rule'], self.rule)

        conflicting = CategorizationRule.objects.create(
            match_value='1234',
            subcategory=self.needs,
        )
        result = evaluate_rules('EXPENSE', 'COFFEE SHOP 1234', [self.rule, second_same_target, conflicting])
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
        self.assertFalse(rule_matches(self.rule, 'EXPENSE', 'COFFEE SHOP 1234'))
        self.wants.is_active = True
        self.wants.budget_bucket = BudgetBucket.SAVINGS
        self.wants.save()
        self.assertFalse(rule_matches(self.rule, 'EXPENSE', 'COFFEE SHOP 1234'))


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
        rule = CategorizationRule.objects.create(match_value='COFFEE SHOP', subcategory=fast_food)
        content = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-11-01,COFFEE SHOP 1234,DEBIT_CARD,-12.00,Posted\n'
            b'2026-11-02,ROUNDUP *COFFEE SHOP 1234,ROUNDUP,-0.25,Posted\n'
        )
        preview = self.client.post(
            '/api/imports/preview/',
            {'account': self.account.id, 'file': self.upload('rules.csv', content)},
            format='multipart',
        )
        self.assertEqual(preview.status_code, 200)
        expense, roundup = preview.data['valid_rows']
        self.assertEqual(expense['classification'], 'EXPENSE')
        self.assertEqual(expense['categorization']['status'], 'AUTO_RESOLVED')
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
        CategorizationRule.objects.create(match_value='COFFEE SHOP', subcategory=fast_food)
        CategorizationRule.objects.create(match_value='1234', subcategory=dining)
        content = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-11-01,COFFEE SHOP 1234,DEBIT_CARD,-12.00,Posted\n'
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
        CategorizationRule.objects.create(match_value='COFFEE SHOP', subcategory=fast_food)
        first = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-11-01,COFFEE SHOP original,DEBIT_CARD,-12.00,Posted\n'
        )
        self.client.post(
            '/api/imports/',
            {'account': self.account.id, 'file': self.upload('first.csv', first)},
            format='multipart',
        )
        second = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-11-01,COFFEE SHOP changed,DEBIT_CARD,-12.00,Posted\n'
        )
        self.client.post(
            '/api/imports/',
            {'account': self.account.id, 'file': self.upload('second.csv', second)},
            format='multipart',
        )
        possible = Transaction.objects.get(raw_description='COFFEE SHOP changed')
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
            'match_value': 'COFFEE SHOP',
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

    def create_rule(self, value='COFFEE SHOP', subcategory=None, **extra):
        return CategorizationRule.objects.create(
            match_value=value,
            subcategory=subcategory or self.wants,
            **extra,
        )

    def create_transaction(self, description='COFFEE SHOP 1234', **extra):
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
            'match_value': 'COFFEE SHOP',
            'classification': 'EXPENSE',
            'subcategory': self.wants.id,
            **overrides,
        }

    def test_rule_crud_normalizes_values_and_rejects_normalized_active_duplicates(self):
        created = self.client.post(
            '/api/categorization-rules/',
            self.payload(match_value='  COFFEE SHOP\t  1234  '),
            format='json',
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data['match_value'], 'COFFEE SHOP 1234')
        duplicate = self.client.post(
            '/api/categorization-rules/',
            self.payload(match_value='COFFEE SHOP 1234'),
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
            {'match_value': 'COFFEE SHOP NEW'},
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
            'COFFEE SHOP same category',
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

        record = self.create_transaction('COFFEE SHOP teaching')
        other = self.create_transaction('COFFEE SHOP second')
        response = self.client.post(
            f'/api/transactions/{record.id}/save-with-rule/',
            {
                'classification': 'EXPENSE',
                'subcategory': self.wants.id,
                'merchant': '',
                'notes': '',
                'match_value': '  COFFEE SHOP  ',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['rule']['match_value'], 'COFFEE SHOP')
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
                'match_value': 'COFFEE SHOP',
            },
            format='json',
        )
        self.assertEqual(failed.status_code, 400)
        uncategorized.refresh_from_db()
        self.assertIsNone(uncategorized.subcategory)

    def test_apply_pending_searches_past_page_and_recomputes_review_state(self):
        rule = self.create_rule()
        ordinary = [
            self.create_transaction(f'COFFEE SHOP {number}')
            for number in range(101)
        ]
        possible_duplicate = self.create_transaction(
            'COFFEE SHOP unresolved',
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
        CategorizationRule.objects.create(match_value='1234', subcategory=self.needs)
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
        first = self.create_transaction('COFFEE SHOP first')
        second = self.create_transaction('COFFEE SHOP second')
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


class MonthlyAccountingTests(APITestCase):
    def setUp(self):
        self.account = Account.objects.create(name='Checking')
        self.needs = Subcategory.objects.create(name='Groceries', budget_bucket=BudgetBucket.NEEDS)
        self.wants = Subcategory.objects.create(name='Dining', budget_bucket=BudgetBucket.WANTS)
        self.savings = Subcategory.objects.create(name='Emergency Fund', budget_bucket=BudgetBucket.SAVINGS)
        self.other_inflow = Subcategory.objects.create(
            name='Gifts',
            scope=SubcategoryScope.OTHER_INFLOW,
            budget_bucket=None,
        )
        BudgetTargetSettings.objects.get(pk=1)

    def create_transaction(
        self,
        day=15,
        classification=Transaction.Classification.EXPENSE,
        amount='-10.00',
        subcategory=None,
        month=10,
        year=2026,
        **kwargs,
    ):
        return Transaction.objects.create(
            account=self.account,
            date=date(year, month, day),
            posted_date=kwargs.pop('posted_date', None),
            raw_description=kwargs.pop('raw_description', f'Synthetic {classification}'),
            amount=Decimal(amount),
            classification=classification,
            subcategory=subcategory,
            **kwargs,
        )

    def report(self, month=10):
        return monthly_report(2026, month, BudgetTargetSettings.objects.get(pk=1))

    def test_monthly_report_defaults_to_latest_transaction_month_or_today(self):
        response = self.client.get('/api/reports/monthly/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['period']['year'], date.today().year)
        self.assertEqual(response.data['period']['month'], date.today().month)

        self.create_transaction(year=2025, month=4)
        self.create_transaction(year=2026, month=2)
        response = self.client.get('/api/reports/monthly/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['period']['year'], 2026)
        self.assertEqual(response.data['period']['month'], 2)

    def test_signed_income_refunds_and_transaction_date_period_boundaries(self):
        self.create_transaction(classification='INCOME', amount='1000.00', day=31, posted_date=date(2026, 11, 2))
        self.create_transaction(classification='INCOME', amount='-100.00', day=1, month=10)
        self.create_transaction(classification='TRANSFER', amount='1000.00', day=2)
        self.create_transaction(classification='REFUND', amount='40.00', subcategory=self.wants, day=3)
        self.create_transaction(classification='UNKNOWN', amount='500.00', day=4, source_type='DEPOSIT')
        self.create_transaction(classification='INCOME', amount='25.00', day=1, month=11)

        report = self.report()
        self.assertEqual(report['summary']['income']['amount'], '900.00')
        self.assertEqual(report['summary']['wants']['amount'], '-40.00')
        self.assertEqual(report['summary']['income']['transaction_count'], 2)
        self.assertEqual(report['data_completeness']['blockers']['UNKNOWN']['transaction_count'], 1)
        self.assertEqual(report['period']['start_date'], '2026-10-01')
        self.assertEqual(report['period']['end_date'], '2026-10-31')

    def test_other_inflows_do_not_affect_income_allocations_or_remaining(self):
        gambling = Subcategory.objects.create(name='Gambling', budget_bucket=BudgetBucket.WANTS)
        winnings = Subcategory.objects.create(
            name='Gambling Winnings',
            scope=SubcategoryScope.OTHER_INFLOW,
            budget_bucket=None,
        )
        self.create_transaction(classification='INCOME', amount='6000.00')
        self.create_transaction(classification='INCOME', amount='200.00', raw_description='Synthetic work payment')
        self.create_transaction(amount='-100.00', subcategory=self.wants, raw_description='Synthetic dinner')
        self.create_transaction(
            classification='REFUND',
            amount='50.00',
            subcategory=self.wants,
            raw_description='Synthetic friend reimbursement',
        )
        self.create_transaction(
            amount='-100.00',
            subcategory=gambling,
            raw_description='Synthetic casino spending',
        )
        self.create_transaction(
            classification='OTHER_INFLOW',
            amount='125.00',
            subcategory=winnings,
            raw_description='Synthetic gambling winnings',
        )
        self.create_transaction(
            classification='OTHER_INFLOW',
            amount='50.00',
            subcategory=self.other_inflow,
            raw_description='Synthetic birthday gift',
        )
        self.create_transaction(
            classification='OTHER_INFLOW',
            amount='30.00',
            raw_description='Synthetic uncategorized inflow',
        )
        self.create_transaction(
            classification='OTHER_INFLOW',
            amount='-5.00',
            subcategory=self.other_inflow,
            raw_description='Synthetic reward correction',
        )
        returned_loan = self.create_transaction(
            classification='TRANSFER',
            amount='100.00',
            raw_description='Synthetic returned loan',
        )
        returned_loan.mark_reviewed()
        self.create_transaction(
            classification='UNKNOWN',
            amount='50.00',
            raw_description='VENMO FROM SYNTHETIC PERSON',
        )

        report = self.report()
        summary = report['summary']
        self.assertEqual(summary['income']['amount'], '6200.00')
        self.assertEqual(summary['other_inflows']['amount'], '200.00')
        self.assertEqual(summary['wants']['amount'], '150.00')
        self.assertEqual(summary['wants']['actual_percent'], '2.42')
        self.assertEqual(summary['remaining']['amount'], '6050.00')
        self.assertEqual(
            {row['subcategory_name']: row['amount'] for row in report['other_inflows_breakdown']},
            {'Gambling Winnings': '125.00', 'Gifts': '45.00', 'Uncategorized': '30.00'},
        )
        self.assertEqual(report['data_completeness']['blockers']['UNKNOWN']['transaction_count'], 1)
        other_rows = drilldown_rows(report, 'other_inflows')
        self.assertEqual(
            sum(Decimal(row['contribution']) for row in other_rows),
            Decimal('200.00'),
        )
        refund_rows = drilldown_rows(report, 'wants')
        self.assertTrue(any(row['classification'] == 'REFUND' for row in refund_rows))
        self.assertFalse(any(row['classification'] == 'OTHER_INFLOW' for row in refund_rows))

        winnings.name = 'Archived winnings'
        winnings.is_active = False
        winnings.save()
        archived_report = self.report()
        self.assertEqual(archived_report['summary']['other_inflows']['amount'], '200.00')
        self.assertIn(
            {'subcategory_name': 'Archived winnings', 'amount': '125.00', 'subcategory': winnings.pk, 'transaction_count': 1},
            archived_report['other_inflows_breakdown'],
        )

        response = self.client.get(
            f'/api/reports/monthly/drilldown/?year=2026&month=10&metric=other_inflows&subcategory={winnings.pk}'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sum(Decimal(row['contribution']) for row in response.data['results']),
            Decimal('125.00'),
        )
        uncategorized = self.client.get(
            '/api/reports/monthly/drilldown/?year=2026&month=10&metric=other_inflows&subcategory=uncategorized'
        )
        self.assertEqual(uncategorized.status_code, 200)
        self.assertEqual(
            sum(Decimal(row['contribution']) for row in uncategorized.data['results']),
            Decimal('30.00'),
        )

    def test_historical_bucket_and_current_subcategory_name_are_used(self):
        expense = self.create_transaction(amount='-50.00', subcategory=self.needs)
        refund = self.create_transaction(classification='REFUND', amount='20.00', subcategory=self.needs)
        Subcategory.objects.filter(pk=self.needs.pk).update(
            name='Household shopping',
            budget_bucket=BudgetBucket.WANTS,
            is_active=False,
        )

        report = self.report()
        self.assertEqual(report['summary']['needs']['amount'], '30.00')
        self.assertEqual(report['summary']['wants']['amount'], '0.00')
        self.assertEqual(report['breakdown']['NEEDS'][0]['subcategory_name'], 'Household shopping')
        expense.refresh_from_db()
        refund.refresh_from_db()
        self.assertEqual(expense.budget_bucket, BudgetBucket.NEEDS)
        self.assertEqual(refund.budget_bucket, BudgetBucket.NEEDS)

    def test_savings_contributions_withdrawals_and_remaining_are_separate(self):
        self.create_transaction(classification='INCOME', amount='600.00')
        self.create_transaction(amount='-50.00', subcategory=self.needs)
        contribution = self.create_transaction(
            classification='TRANSFER',
            amount='-500.00',
            subcategory=self.savings,
            savings_effect=SavingsEffect.CONTRIBUTION,
        )
        withdrawal = self.create_transaction(
            classification='TRANSFER',
            amount='100.00',
            subcategory=self.savings,
            savings_effect=SavingsEffect.WITHDRAWAL,
        )
        ordinary = self.create_transaction(
            classification='TRANSFER',
            amount='-900.00',
        )
        ordinary.mark_reviewed()

        report = self.report()
        self.assertEqual(report['summary']['savings_contributions']['amount'], '500.00')
        self.assertEqual(report['summary']['savings_withdrawals']['amount'], '100.00')
        self.assertEqual(report['summary']['net_savings_activity']['amount'], '400.00')
        self.assertEqual(report['summary']['remaining']['amount'], '50.00')
        self.assertEqual(report['breakdown']['SAVINGS'][0]['amount'], '400.00')
        contribution_rows = drilldown_rows(report, 'savings_contributions')
        withdrawal_rows = drilldown_rows(report, 'savings_withdrawals')
        self.assertEqual(
            Decimal(next(row['contribution'] for row in contribution_rows if row['id'] == contribution.pk)),
            Decimal('500.00'),
        )
        self.assertEqual(
            Decimal(next(row['contribution'] for row in withdrawal_rows if row['id'] == withdrawal.pk)),
            Decimal('100.00'),
        )
        self.assertNotIn(ordinary.pk, {row['id'] for row in contribution_rows + withdrawal_rows})

    def test_negative_bucket_totals_are_not_clamped(self):
        self.create_transaction(classification='REFUND', amount='35.00', subcategory=self.wants)
        report = self.report()
        self.assertEqual(report['summary']['wants']['amount'], '-35.00')
        self.assertIsNone(report['summary']['wants']['actual_percent'])

    def test_unresolved_unknown_uncategorized_conflict_and_pending_transfer_are_flagged(self):
        self.create_transaction(classification='UNKNOWN', amount='9.00')
        self.create_transaction(classification='EXPENSE', amount='-8.00')
        self.create_transaction(
            classification='EXPENSE',
            amount='-7.00',
            categorization_rule_conflict=True,
        )
        pending_transfer = self.create_transaction(classification='TRANSFER', amount='-6.00')
        possible = self.create_transaction(classification='EXPENSE', amount='-5.00', subcategory=self.needs)
        candidate = self.create_transaction(
            classification='EXPENSE',
            amount='-5.00',
            subcategory=self.needs,
            day=14,
        )
        possible.duplicate_candidates.add(candidate)
        possible.duplicate_resolution = DuplicateResolution.UNRESOLVED
        possible.save()
        distinct = self.create_transaction(classification='EXPENSE', amount='-4.00', subcategory=self.needs)
        distinct.duplicate_candidates.add(candidate)
        distinct.duplicate_resolution = DuplicateResolution.DISTINCT
        distinct.save()
        excluded = self.create_transaction(classification='EXCLUDED', amount='-3.00')
        confirmed = self.create_transaction(classification='EXPENSE', amount='-2.00', subcategory=self.needs)
        confirmed.duplicate_candidates.add(candidate)
        confirmed.duplicate_resolution = DuplicateResolution.CONFIRMED_DUPLICATE
        confirmed.duplicate_of = candidate
        confirmed.save()

        report = self.report()
        self.assertEqual(report['summary']['needs']['amount'], '9.00')
        self.assertEqual(report['data_completeness']['affected_transaction_count'], 5)
        self.assertEqual(report['data_completeness']['blockers']['TRANSFER_SAVINGS_UNREVIEWED']['transaction_count'], 1)
        self.assertEqual(report['data_completeness']['excluded']['excluded'], 1)
        self.assertEqual(report['data_completeness']['excluded']['confirmed_duplicate'], 1)
        needs_ids = set(drilldown_queryset(report['_start'], report['_end'], 'needs').values_list('pk', flat=True))
        self.assertNotIn(pending_transfer.pk, needs_ids)
        self.assertNotIn(excluded.pk, needs_ids)

    def test_reviewed_transfer_with_no_effect_is_intentionally_ordinary(self):
        transfer = self.create_transaction(classification='TRANSFER', amount='-40.00')
        transfer.mark_reviewed()
        report = self.report()
        self.assertFalse(report['data_completeness']['is_incomplete'])
        self.assertEqual(report['summary']['needs']['amount'], '0.00')

    def test_savings_effect_api_requires_transfer_and_savings_subcategory(self):
        expense = self.create_transaction(classification='EXPENSE', subcategory=self.needs)
        response = self.client.patch(
            f'/api/transactions/{expense.pk}/',
            {'savings_effect': 'CONTRIBUTION'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('savings_effect', response.data)

        transfer = self.create_transaction(classification='TRANSFER')
        response = self.client.patch(
            f'/api/transactions/{transfer.pk}/',
            {'savings_effect': 'WITHDRAWAL'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('Savings subcategory', str(response.data))

        response = self.client.patch(
            f'/api/transactions/{transfer.pk}/',
            {'subcategory': self.savings.pk, 'savings_effect': 'CONTRIBUTION'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['savings_effect'], 'CONTRIBUTION')
        for effect in ('WITHDRAWAL', 'NONE'):
            response = self.client.patch(
                f'/api/transactions/{transfer.pk}/',
                {'savings_effect': effect},
                format='json',
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['savings_effect'], effect)

    def test_budget_targets_allow_independent_percentages_and_do_not_mutate_history(self):
        transaction_record = self.create_transaction(amount='-30.00', subcategory=self.needs)
        before = transaction_record.updated_at
        response = self.client.put(
            '/api/budget-targets/',
            {'needs_percent': '50.00', 'wants_percent': '32.00', 'savings_percent': '20.00'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['wants_percent'], '32.00')
        self.assertEqual(sum(Decimal(response.data[key]) for key in response.data), Decimal('102.00'))
        report = self.client.get('/api/reports/monthly/?year=2026&month=10')
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.data['summary']['wants']['target_percent'], '32.00')
        transaction_record.refresh_from_db()
        self.assertEqual(transaction_record.updated_at, before)

        invalid = self.client.put(
            '/api/budget-targets/',
            {'needs_percent': '100.01'},
            format='json',
        )
        self.assertEqual(invalid.status_code, 400)

    def test_viewing_old_report_has_no_database_side_effect(self):
        before_targets = list(BudgetTargetSettings.objects.values())
        before_transactions = list(Transaction.objects.values())
        self.client.get('/api/reports/monthly/?year=2020&month=1')
        self.assertEqual(list(BudgetTargetSettings.objects.values()), before_targets)
        self.assertEqual(list(Transaction.objects.values()), before_transactions)

    def test_monthly_drilldowns_reconcile_with_report_totals(self):
        self.create_transaction(classification='INCOME', amount='200.00')
        self.create_transaction(amount='-80.00', subcategory=self.needs)
        self.create_transaction(classification='REFUND', amount='20.00', subcategory=self.needs)
        self.create_transaction(amount='-25.00', subcategory=self.wants)
        self.create_transaction(
            classification='TRANSFER',
            amount='-30.00',
            subcategory=self.savings,
            savings_effect=SavingsEffect.CONTRIBUTION,
        )
        report = self.report()
        for metric, expected in (
            ('income', Decimal('200.00')),
            ('needs', Decimal('60.00')),
            ('wants', Decimal('25.00')),
            ('savings_contributions', Decimal('30.00')),
            ('remaining', Decimal('85.00')),
        ):
            rows = drilldown_rows(report, metric)
            self.assertEqual(sum(Decimal(row['contribution']) for row in rows), expected, metric)

        response = self.client.get('/api/reports/monthly/drilldown/?year=2026&month=10&metric=needs')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(sum(Decimal(row['contribution']) for row in response.data['results']), Decimal('60.00'))

    def test_negative_income_percentages_are_not_reported_and_remaining_can_be_negative(self):
        self.create_transaction(classification='INCOME', amount='-10.00')
        report = self.report()
        self.assertIsNone(report['summary']['needs']['actual_percent'])
        self.assertIsNone(report['summary']['needs']['variance_points'])
        self.assertEqual(report['summary']['remaining']['amount'], '-10.00')

    def test_year_month_all_time_summaries_and_drilldowns_reconcile(self):
        self.create_transaction(classification='INCOME', amount='1000.00', year=2025, month=12)
        self.create_transaction(classification='INCOME', amount='2000.00', year=2026, month=10)
        self.create_transaction(amount='-300.00', subcategory=self.needs, year=2026, month=10)
        self.create_transaction(classification='REFUND', amount='50.00', subcategory=self.needs, year=2026, month=10)
        self.create_transaction(amount='-200.00', subcategory=self.wants, year=2026, month=10)
        self.create_transaction(
            classification='TRANSFER',
            amount='-100.00',
            subcategory=self.savings,
            savings_effect=SavingsEffect.CONTRIBUTION,
            year=2026,
            month=10,
        )
        self.create_transaction(
            classification='TRANSFER',
            amount='25.00',
            subcategory=self.savings,
            savings_effect=SavingsEffect.WITHDRAWAL,
            year=2026,
            month=10,
        )
        self.create_transaction(
            classification='OTHER_INFLOW',
            amount='125.00',
            subcategory=self.other_inflow,
            year=2026,
            month=10,
        )
        targets = BudgetTargetSettings.objects.get(pk=1)
        monthly = monthly_report(2026, 10, targets)
        annual = year_report(2026, targets)
        all_time = all_time_report(targets)

        self.assertEqual(annual['months'][9]['summary'], monthly['summary'])
        self.assertEqual(annual['summary']['income']['amount'], '2000.00')
        self.assertEqual(annual['summary']['needs']['amount'], '250.00')
        self.assertEqual(annual['summary']['wants']['amount'], '200.00')
        self.assertEqual(annual['summary']['savings_contributions']['amount'], '100.00')
        self.assertEqual(annual['summary']['savings_withdrawals']['amount'], '25.00')
        self.assertEqual(annual['summary']['net_savings_activity']['amount'], '75.00')
        self.assertEqual(annual['summary']['remaining']['amount'], '1450.00')
        self.assertEqual(annual['summary']['other_inflows']['amount'], '125.00')
        self.assertEqual(all_time['years'][1]['summary'], annual['summary'])
        self.assertEqual(all_time['period'], {
            'start_date': '2025-12-15',
            'end_date': '2026-10-15',
        })
        self.assertEqual(all_time['summary']['income']['amount'], '3000.00')
        self.assertEqual(all_time['summary']['other_inflows']['amount'], '125.00')
        self.assertEqual(all_time['months'][-1]['summary']['other_inflows']['amount'], '125.00')
        self.assertEqual(all_time['summary']['needs']['actual_percent'], '8.33')
        self.assertEqual(len(annual['months']), 12)
        self.assertEqual(annual['months'][0]['ledger_transaction_count'], 0)
        self.assertEqual(annual['months'][9]['summary'], monthly['summary'])
        self.assertEqual(all_time['monthly_series'][0]['label'], '2025-12')
        self.assertEqual(all_time['monthly_series'][-1]['savings_withdrawals'], '25.00')
        self.assertEqual(all_time['monthly_series'][-1]['net_savings_activity'], '75.00')
        self.assertEqual(all_time['monthly_series'][-1]['remaining'], '1450.00')
        self.assertEqual(all_time['monthly_series'][-1]['other_inflows'], '125.00')
        all_time_drilldown = self.client.get(
            '/api/reports/drilldown/?scope=all&metric=other_inflows'
        )
        self.assertEqual(all_time_drilldown.status_code, 200)
        self.assertEqual(
            sum(Decimal(row['contribution']) for row in all_time_drilldown.data['results']),
            Decimal('125.00'),
        )

        response = self.client.get('/api/reports/yearly/?year=2026')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['months'][9]['summary'], monthly['summary'])
        for metric, expected in (
            ('income', Decimal('2000.00')),
            ('needs', Decimal('250.00')),
            ('wants', Decimal('200.00')),
            ('savings_contributions', Decimal('100.00')),
            ('savings_withdrawals', Decimal('25.00')),
            ('net_savings_activity', Decimal('75.00')),
            ('remaining', Decimal('1450.00')),
            ('other_inflows', Decimal('125.00')),
        ):
            result = self.client.get(
                f'/api/reports/drilldown/?scope=year&year=2026&metric={metric}'
            )
            self.assertEqual(result.status_code, 200)
            self.assertEqual(
                sum(Decimal(row['contribution']) for row in result.data['results']),
                expected,
                metric,
            )
        monthly_api = self.client.get(
            '/api/reports/monthly/?year=2026&month=10'
        )
        self.assertEqual(monthly_api.data['summary'], response.data['months'][9]['summary'])

    def test_annual_percentages_use_aggregate_totals_not_monthly_average(self):
        self.create_transaction(classification='INCOME', amount='100.00', month=1)
        self.create_transaction(amount='-50.00', subcategory=self.needs, month=1)
        self.create_transaction(classification='INCOME', amount='900.00', month=2)
        self.create_transaction(amount='-90.00', subcategory=self.needs, month=2)
        report = year_report(2026, BudgetTargetSettings.objects.get(pk=1))
        self.assertEqual(report['summary']['needs']['actual_percent'], '14.00')

    def test_range_inclusive_boundaries_and_daily_trends_use_transaction_date(self):
        self.create_transaction(classification='INCOME', amount='1.00', year=2025, month=12, day=30)
        self.create_transaction(classification='INCOME', amount='10.00', year=2025, month=12, day=31, posted_date=date(2026, 2, 1))
        self.create_transaction(classification='INCOME', amount='20.00', year=2026, month=1, day=1, posted_date=date(2025, 12, 1))
        self.create_transaction(classification='INCOME', amount='100.00', year=2026, month=1, day=2)
        response = self.client.get('/api/reports/range/', {'start_date': '2025-12-31', 'end_date': '2026-01-01'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['period'], {'start_date': '2025-12-31', 'end_date': '2026-01-01'})
        self.assertEqual(response.data['drilldown'], {'scope': 'range', **response.data['period']})
        self.assertEqual(response.data['summary']['income']['amount'], '30.00')
        self.assertEqual(response.data['ledger_transaction_count'], 2)
        self.assertEqual(response.data['trend_granularity'], 'day')
        self.assertEqual([(point['label'], point['income']) for point in response.data['trend_series']], [
            ('2025-12-31', '10.00'), ('2026-01-01', '20.00'),
        ])
        same_day = self.client.get('/api/reports/range/', {'start_date': '2026-01-01', 'end_date': '2026-01-01'})
        self.assertEqual(same_day.data['summary']['income']['amount'], '20.00')
        self.assertEqual(len(same_day.data['trend_series']), 1)
        cross_month = self.client.get('/api/reports/range/', {'start_date': '2025-12-30', 'end_date': '2026-01-02'})
        self.assertEqual(cross_month.data['summary']['income']['amount'], '131.00')

    def test_range_accounting_reconciles_with_existing_reports_and_drilldowns(self):
        self.create_transaction(classification='INCOME', amount='100.00', month=1)
        self.create_transaction(classification='INCOME', amount='900.00', month=2)
        self.create_transaction(amount='-50.00', subcategory=self.needs, month=1)
        self.create_transaction(amount='-90.00', subcategory=self.needs, month=2)
        self.create_transaction(classification='REFUND', amount='10.00', subcategory=self.needs, month=2)
        self.create_transaction(amount='-70.00', subcategory=self.wants, month=2)
        self.create_transaction(classification='REFUND', amount='20.00', subcategory=self.wants, month=2)
        self.create_transaction(classification='TRANSFER', amount='-100.00', subcategory=self.savings, savings_effect=SavingsEffect.CONTRIBUTION, month=2)
        self.create_transaction(classification='TRANSFER', amount='25.00', subcategory=self.savings, savings_effect=SavingsEffect.WITHDRAWAL, month=2)
        self.create_transaction(classification='OTHER_INFLOW', amount='125.00', subcategory=self.other_inflow, month=2)
        targets = BudgetTargetSettings.objects.get(pk=1)
        targets.needs_percent = Decimal('42.50')
        targets.save()
        month = range_report(date(2026, 2, 1), date(2026, 2, 28), targets)
        year = range_report(date(2026, 1, 1), date(2026, 12, 31), targets)
        entire = range_report(date(2026, 1, 15), date(2026, 2, 15), targets)
        for actual, expected in ((month, monthly_report(2026, 2, targets)), (year, year_report(2026, targets)), (entire, all_time_report(targets))):
            for key in ('summary', 'data_completeness', 'breakdown', 'other_inflows_breakdown'):
                self.assertEqual(actual[key], expected[key], key)
        self.assertEqual(year['summary']['needs']['actual_percent'], '13.00')
        self.assertEqual(year['summary']['needs']['target_percent'], '42.50')
        self.assertEqual(year['summary']['needs']['variance_points'], '-29.50')
        self.assertEqual(year['summary']['wants']['amount'], '50.00')
        self.assertEqual(year['summary']['savings_contributions']['amount'], '100.00')
        self.assertEqual(year['summary']['savings_withdrawals']['amount'], '25.00')
        self.assertEqual(year['summary']['net_savings_activity']['amount'], '75.00')
        self.assertEqual(year['summary']['other_inflows']['amount'], '125.00')
        self.assertEqual(year['summary']['remaining']['amount'], '720.00')
        self.assertEqual(year['trend_granularity'], 'month')
        self.assertFalse(year['trend_series'][2]['has_transactions'])
        for metric, value in year['summary'].items():
            response = self.client.get('/api/reports/drilldown/', {
                'scope': 'range', 'start_date': '2026-01-01', 'end_date': '2026-12-31', 'metric': metric,
            })
            self.assertEqual(response.status_code, 200, metric)
            self.assertEqual(sum((Decimal(row['contribution']) for row in response.data['results']), Decimal('0')), Decimal(value['amount']), metric)
        response = self.client.get('/api/reports/drilldown/', {
            'scope': 'range', 'start_date': '2026-01-01', 'end_date': '2026-12-31',
            'metric': 'needs', 'subcategory': self.needs.pk,
        })
        self.assertEqual(sum(Decimal(row['contribution']) for row in response.data['results']), Decimal('130.00'))

    def test_range_completeness_duplicates_and_reviewed_transfer(self):
        self.create_transaction(classification='UNKNOWN', month=2, day=1)
        self.create_transaction(classification='EXPENSE', month=2, day=2)
        self.create_transaction(classification='REFUND', amount='5.00', month=2, day=2)
        self.create_transaction(classification='EXPENSE', categorization_rule_conflict=True, month=2, day=3)
        candidate = self.create_transaction(classification='EXPENSE', subcategory=self.needs, month=2, day=4)
        possible = self.create_transaction(classification='EXPENSE', subcategory=self.needs, month=2, day=5)
        possible.duplicate_candidates.add(candidate)
        possible.duplicate_resolution = DuplicateResolution.UNRESOLVED
        possible.save()
        confirmed = self.create_transaction(classification='EXPENSE', subcategory=self.needs, month=2, day=6)
        confirmed.duplicate_candidates.add(candidate)
        confirmed.duplicate_resolution = DuplicateResolution.CONFIRMED_DUPLICATE
        confirmed.duplicate_of = candidate
        confirmed.save()
        transfer = self.create_transaction(classification='TRANSFER', month=2, day=7)
        transfer.mark_reviewed()
        report = self.client.get('/api/reports/range/', {'start_date': '2026-02-01', 'end_date': '2026-02-08'}).data
        self.assertEqual(report['data_completeness']['affected_transaction_count'], 5)
        self.assertEqual(set(report['data_completeness']['blockers']), {'UNKNOWN', 'UNCATEGORIZED', 'RULE_CONFLICT', 'UNRESOLVED_DUPLICATE'})
        self.assertEqual(report['data_completeness']['blockers']['UNCATEGORIZED']['transaction_count'], 3)
        self.assertEqual(report['data_completeness']['excluded']['confirmed_duplicate'], 1)
        self.assertEqual(report['summary']['needs']['amount'], '10.00')
        self.assertTrue(report['trend_series'][0]['is_incomplete'])
        self.assertFalse(report['trend_series'][7]['has_transactions'])
        self.assertFalse(report['trend_series'][7]['is_incomplete'])

    def test_range_empty_negative_and_validation(self):
        params = {'start_date': '2026-02-01', 'end_date': '2026-02-01'}
        empty = self.client.get('/api/reports/range/', params).data
        self.assertEqual(empty['ledger_transaction_count'], 0)
        self.assertFalse(empty['data_completeness']['is_incomplete'])
        self.assertIsNone(empty['summary']['needs']['actual_percent'])
        self.assertFalse(empty['trend_series'][0]['has_transactions'])
        self.create_transaction(classification='REFUND', amount='25.00', subcategory=self.wants, month=2, day=1)
        negative = self.client.get('/api/reports/range/', params).data
        self.assertEqual(negative['summary']['wants']['amount'], '-25.00')
        self.assertEqual(negative['summary']['remaining']['amount'], '25.00')
        self.assertIsNone(negative['summary']['wants']['actual_percent'])
        for invalid in (
            {}, {'start_date': '2026-02-01'}, {'start_date': '2026-02-30', 'end_date': '2026-03-01'},
            {'start_date': '2026-2-01', 'end_date': '2026-03-01'},
            {'start_date': '2026-03-01', 'end_date': '2026-02-01'},
        ):
            self.assertEqual(self.client.get('/api/reports/range/', invalid).status_code, 400)
            self.assertEqual(self.client.get('/api/reports/drilldown/', {'scope': 'range', 'metric': 'income', **invalid}).status_code, 400)
        maximum = self.client.get('/api/reports/range/', {'start_date': '9999-12-31', 'end_date': '9999-12-31'})
        self.assertEqual(maximum.status_code, 200)
        maximum_month = self.client.get('/api/reports/range/', {'start_date': '9999-11-01', 'end_date': '9999-12-31'})
        self.assertEqual(maximum_month.status_code, 200)
        self.assertEqual(maximum_month.data['trend_series'][-1]['label'], '9999-12')

    def test_range_monthly_trends_clip_edge_months_and_mark_partial_periods(self):
        self.create_transaction(classification='INCOME', amount='100.00', month=1, day=1)
        self.create_transaction(classification='INCOME', amount='10.00', month=1, day=20)
        self.create_transaction(classification='UNKNOWN', month=2, day=1)
        self.create_transaction(classification='INCOME', amount='20.00', month=3, day=10)
        self.create_transaction(classification='INCOME', amount='200.00', month=3, day=20)
        result = self.client.get('/api/reports/range/', {
            'start_date': '2026-01-15', 'end_date': '2026-03-15',
        }).data
        self.assertEqual(result['trend_granularity'], 'month')
        self.assertEqual([point['income'] for point in result['trend_series']], ['10.00', '0.00', '20.00'])
        self.assertTrue(result['trend_series'][1]['has_transactions'])
        self.assertTrue(result['trend_series'][1]['is_incomplete'])
        self.assertEqual(result['summary']['income']['amount'], '30.00')

    def test_range_drilldown_is_paginated_and_inclusive(self):
        for day in (1, 2):
            for _ in range(51):
                self.create_transaction(classification='INCOME', amount='1.00', month=2, day=day)
        self.create_transaction(classification='INCOME', amount='999.00', month=2, day=3)
        params = {'scope': 'range', 'start_date': '2026-02-01', 'end_date': '2026-02-02', 'metric': 'income'}
        first = self.client.get('/api/reports/drilldown/', params).data
        second = self.client.get('/api/reports/drilldown/', {**params, 'page': first['next']}).data
        self.assertEqual(first['count'], 102)
        self.assertEqual(len(first['results']), 100)
        self.assertEqual(len(second['results']), 2)
        self.assertEqual(sum(Decimal(row['contribution']) for row in first['results'] + second['results']), Decimal('102.00'))

    def test_historical_completeness_propagates_and_distinguishes_empty_months(self):
        self.create_transaction(classification='UNKNOWN', month=2)
        self.create_transaction(classification='EXPENSE', amount='-5.00', month=3)
        report = year_report(2026, BudgetTargetSettings.objects.get(pk=1))
        self.assertEqual(report['data_completeness']['affected_transaction_count'], 2)
        self.assertTrue(report['months'][1]['data_completeness']['is_incomplete'])
        self.assertTrue(report['months'][2]['data_completeness']['is_incomplete'])
        self.assertEqual(report['months'][0]['ledger_transaction_count'], 0)
        self.assertFalse(report['months'][0]['data_completeness']['is_incomplete'])
        self.assertEqual(report['monthly_series'][0]['needs'], '0.00')

    def test_all_time_empty_and_year_negative_periods(self):
        targets = BudgetTargetSettings.objects.get(pk=1)
        empty = all_time_report(targets)
        self.assertEqual(empty['period'], {'start_date': None, 'end_date': None})
        self.assertEqual(empty['years'], [])
        self.assertEqual(empty['monthly_series'], [])
        self.create_transaction(classification='INCOME', amount='-10.00', month=1)
        self.create_transaction(classification='REFUND', amount='25.00', subcategory=self.wants, month=1)
        report = year_report(2026, targets)
        self.assertEqual(report['summary']['income']['amount'], '-10.00')
        self.assertIsNone(report['summary']['wants']['actual_percent'])
        self.assertEqual(report['summary']['wants']['amount'], '-25.00')
        self.assertEqual(report['summary']['remaining']['amount'], '15.00')

    def test_ranked_categories_keep_negative_net_and_historical_bucket_after_move(self):
        self.create_transaction(amount='-20.00', subcategory=self.wants, year=2025, month=5)
        self.create_transaction(classification='REFUND', amount='30.00', subcategory=self.wants, year=2025, month=5)
        Subcategory.objects.filter(pk=self.wants.pk).update(
            name='Dining at home',
            budget_bucket=BudgetBucket.NEEDS,
        )
        self.wants.refresh_from_db()
        self.create_transaction(amount='-40.00', subcategory=self.wants, year=2026, month=6)
        self.create_transaction(classification='UNKNOWN', year=2026, month=5)
        historical = all_time_report(BudgetTargetSettings.objects.get(pk=1))
        self.assertEqual(historical['breakdown']['WANTS'][0]['amount'], '-10.00')
        self.assertEqual(historical['breakdown']['NEEDS'][0]['amount'], '40.00')
        self.assertEqual(historical['breakdown']['WANTS'][0]['subcategory_name'], 'Dining at home')
        trend = category_trend(self.wants.pk, 'all', BudgetTargetSettings.objects.get(pk=1))
        self.assertEqual(trend['series'][0]['buckets']['WANTS']['amount'], '-10.00')
        self.assertEqual(trend['series'][13]['buckets']['NEEDS']['amount'], '40.00')
        self.assertEqual(trend['series'][13]['buckets']['WANTS']['amount'], '0.00')
        self.assertTrue(trend['series'][12]['is_incomplete'])
        self.assertEqual(trend['series'][12]['affected_transaction_count'], 1)
        month_trend = category_trend(
            self.wants.pk,
            'month',
            BudgetTargetSettings.objects.get(pk=1),
            2026,
            5,
        )
        self.assertEqual(month_trend['scope'], {'type': 'month', 'year': 2026, 'month': 5})
        self.assertTrue(month_trend['series'][0]['is_incomplete'])
        response = self.client.get(
            f'/api/reports/category-trend/?subcategory={self.wants.pk}&scope=month&year=2026&month=5'
        )
        self.assertEqual(response.status_code, 200)

    def test_historical_api_empty_year_and_category_trend_validation(self):
        response = self.client.get('/api/reports/yearly/?year=2024')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['months']), 12)
        self.assertTrue(all(month['ledger_transaction_count'] == 0 for month in response.data['months']))
        self.assertEqual(self.client.get('/api/reports/all-time/').data['period'], {
            'start_date': None,
            'end_date': None,
        })
        missing = self.client.get('/api/reports/category-trend/?subcategory=999')
        self.assertEqual(missing.status_code, 400)


class ReviewReportReconciliationTests(APITestCase):
    def setUp(self):
        self.account = Account.objects.create(name='Checking')
        self.wants = Subcategory.objects.create(name='Dining', budget_bucket=BudgetBucket.WANTS)

    def make(self, classification, day=15, month=10, **kwargs):
        return Transaction.objects.create(
            account=self.account,
            date=date(2026, month, day),
            raw_description='Synthetic row',
            amount=Decimal('-10.00'),
            classification=classification,
            **kwargs,
        )

    def review_ids(self):
        response = self.client.get('/api/transactions/?review_status=PENDING&page_size=100')
        return {item['id'] for item in response.json()['results']}

    def blocker_count(self, url):
        return self.client.get(url).json()['data_completeness']['affected_transaction_count']

    def assert_reports_agree(self, expected):
        self.assertEqual(self.blocker_count('/api/reports/all-time/'), expected)
        self.assertEqual(self.blocker_count('/api/reports/yearly/?year=2026'), expected)
        self.assertEqual(self.blocker_count('/api/reports/monthly/?year=2026&month=10'), expected)

    def test_unknown_counted_by_reports_is_in_review_until_resolved(self):
        unknown = self.make(Transaction.Classification.UNKNOWN)
        self.assertIn(unknown.pk, self.review_ids())
        self.assert_reports_agree(1)

        unknown.classification = Transaction.Classification.EXCLUDED
        unknown.save()
        self.assert_reports_agree(0)
        unknown.mark_reviewed()
        self.assertNotIn(unknown.pk, self.review_ids())

    def test_confirmed_duplicate_and_excluded_are_not_attention(self):
        original = self.make(Transaction.Classification.UNKNOWN)
        duplicate = self.make(Transaction.Classification.UNKNOWN)
        duplicate.duplicate_candidates.add(original)
        duplicate.duplicate_resolution = DuplicateResolution.CONFIRMED_DUPLICATE
        duplicate.duplicate_of = original
        duplicate.save()
        self.make(Transaction.Classification.EXCLUDED)

        self.assertEqual(self.review_ids(), {original.pk})
        self.assert_reports_agree(1)

    def test_rule_completed_expense_is_not_a_stale_blocker(self):
        record = self.make(Transaction.Classification.EXPENSE, subcategory=self.wants)
        self.assertEqual(record.review_status, ReviewStatus.NOT_REQUIRED)
        self.assertNotIn(record.pk, self.review_ids())
        self.assert_reports_agree(0)

    def test_every_report_blocker_is_pending_review(self):
        self.make(Transaction.Classification.UNKNOWN)
        self.make(Transaction.Classification.EXPENSE)
        self.make(Transaction.Classification.TRANSFER)
        self.make(Transaction.Classification.EXPENSE, subcategory=self.wants, categorization_rule_conflict=True)
        from .accounting import _blocker_union, _eligibility_blockers
        blocked = set(
            Transaction.objects.filter(_blocker_union(_eligibility_blockers())).values_list('pk', flat=True)
        )
        self.assertTrue(blocked)
        self.assertLessEqual(blocked, self.review_ids())


class ReviewStateRepairMigrationTests(TransactionTestCase):
    def test_repair_migration_returns_stale_rows_to_review(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('finance', '0008_remove_categorizationrule_valid_rule_classification_and_more')])
        apps = executor.loader.project_state(
            [('finance', '0008_remove_categorizationrule_valid_rule_classification_and_more')]
        ).apps
        Old = apps.get_model('finance', 'Transaction')
        account = apps.get_model('finance', 'Account').objects.create(name='Repair test')

        def row(**kwargs):
            return Old.objects.create(
                account_id=account.pk, date=date(2026, 1, 1), raw_description='Synthetic',
                amount=Decimal('-1.00'), review_status='NOT_REQUIRED', **kwargs,
            ).pk

        stale = row(classification='UNKNOWN')
        confirmed = row(classification='UNKNOWN', duplicate_resolution='CONFIRMED_DUPLICATE')
        excluded = row(classification='EXCLUDED')
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
            statuses = dict(Transaction.objects.filter(pk__in=(stale, confirmed, excluded)).values_list('pk', 'review_status'))
            self.assertEqual(statuses[stale], 'PENDING')
            self.assertEqual(statuses[confirmed], 'NOT_REQUIRED')
            self.assertEqual(statuses[excluded], 'NOT_REQUIRED')
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())


class SavingsTransferRuleTests(APITestCase):
    def setUp(self):
        self.account = Account.objects.create(name='Checking')
        self.wants = Subcategory.objects.create(name='Dining', budget_bucket=BudgetBucket.WANTS)
        self.needs = Subcategory.objects.create(name='Groceries', budget_bucket=BudgetBucket.NEEDS)
        self.fund = Subcategory.objects.create(name='Emergency Fund', budget_bucket=BudgetBucket.SAVINGS)
        self.goal = Subcategory.objects.create(name='Vacation Goal', budget_bucket=BudgetBucket.SAVINGS)
        self.other = Subcategory.objects.create(
            name='Gifts', scope=SubcategoryScope.OTHER_INFLOW, budget_bucket=None,
        )

    def savings_rule(self, value='AUTO SAVE', effect=SavingsEffect.CONTRIBUTION, target=None,
                     match_type=RuleMatchType.STARTS_WITH, **kwargs):
        return CategorizationRule.objects.create(
            classification='TRANSFER',
            savings_effect=effect,
            match_type=match_type,
            match_value=value,
            subcategory=target or self.fund,
            **kwargs,
        )

    def txn(self, description='AUTO SAVE * 1', classification='UNKNOWN', amount='-1.00', **kwargs):
        return Transaction.objects.create(
            account=self.account, date=date(2026, 3, 5), raw_description=description,
            amount=Decimal(amount), classification=classification, **kwargs,
        )

    def payload(self, **overrides):
        return {
            'classification': 'TRANSFER', 'match_type': 'STARTS_WITH', 'match_value': 'AUTO SAVE *',
            'savings_effect': 'CONTRIBUTION', 'subcategory': self.fund.id, **overrides,
        }

    def preview(self, content):
        return self.client.post(
            '/api/imports/preview/',
            {'account': self.account.id, 'file': SimpleUploadedFile('s.csv', content, content_type='text/csv')},
            format='multipart',
        )

    # --- matching ---
    def test_starts_with_is_case_insensitive_whitespace_normalized_and_prefix_only(self):
        rule = self.savings_rule(value='  auto   save * ')
        for description, expected in (
            ('AUTO SAVE * 77', True),
            ('  Auto \t Save *  77', True),
            ('PAYMENT AUTO SAVE * 77', False),
            ('AUTO SAVER 77', False),
        ):
            self.assertEqual(rule_matches(rule, 'UNKNOWN', description), expected, description)

    def test_existing_rules_default_to_expense_category_and_never_change_classification(self):
        rule = CategorizationRule.objects.create(match_value='COFFEE', subcategory=self.wants)
        self.assertFalse(rule.allow_unknown)
        self.assertEqual(rule.savings_effect, SavingsEffect.NONE)
        self.assertFalse(rule_matches(rule, 'UNKNOWN', 'COFFEE'))
        self.assertTrue(rule_matches(rule, 'EXPENSE', 'COFFEE'))
        self.assertEqual(evaluate_rules('EXPENSE', 'COFFEE', [rule])['classification'], 'EXPENSE')

    def test_expense_contains_rule_can_use_starts_with_but_still_requires_expense(self):
        rule = CategorizationRule.objects.create(
            match_value='COFFEE', match_type=RuleMatchType.STARTS_WITH, subcategory=self.wants,
        )
        self.assertTrue(rule_matches(rule, 'EXPENSE', 'Coffee shop'))
        self.assertFalse(rule_matches(rule, 'EXPENSE', 'The coffee shop'))

    # --- eligibility ---
    def test_savings_rule_eligibility_matrix(self):
        rule = self.savings_rule()
        for classification, unresolved, expected in (
            ('UNKNOWN', True, True),
            ('TRANSFER', True, True),
            ('TRANSFER', False, False),
            ('EXPENSE', True, False),
            ('INCOME', True, False),
            ('REFUND', True, False),
            ('EXCLUDED', True, False),
            ('OTHER_INFLOW', True, False),
        ):
            self.assertEqual(
                rule_matches(rule, classification, 'AUTO SAVE * 1', unresolved), expected,
                (classification, unresolved),
            )

    def test_savings_rule_does_not_compete_with_expense_rule_on_expense_transactions(self):
        expense_rule = CategorizationRule.objects.create(match_value='AUTO SAVE', subcategory=self.wants)
        savings_rule = self.savings_rule()
        expense = evaluate_rules('EXPENSE', 'AUTO SAVE * 1', [expense_rule, savings_rule])
        self.assertFalse(expense['conflict'])
        self.assertEqual(expense['subcategory'], self.wants)
        self.assertEqual(expense['classification'], 'EXPENSE')
        unknown = evaluate_rules('UNKNOWN', 'AUTO SAVE * 1', [expense_rule, savings_rule])
        self.assertEqual(unknown['classification'], 'TRANSFER')
        self.assertEqual(unknown['subcategory'], self.fund)

    # --- conflicts ---
    def test_conflicts_depend_on_semantic_outcome_not_rule_id(self):
        same_a = self.savings_rule(value='AUTO SAVE')
        same_b = self.savings_rule(value='AUTO', match_type=RuleMatchType.CONTAINS)
        shared = evaluate_rules('UNKNOWN', 'AUTO SAVE * 1', [same_a, same_b])
        self.assertFalse(shared['conflict'])
        self.assertEqual((shared['savings_effect'], shared['subcategory']), (SavingsEffect.CONTRIBUTION, self.fund))
        effect = self.savings_rule(value='AUTO SAVE *', effect=SavingsEffect.WITHDRAWAL)
        target = self.savings_rule(value='AUTO SAVE *', target=self.goal)
        for other in (effect, target):
            result = evaluate_rules('UNKNOWN', 'AUTO SAVE * 1', [same_a, other])
            self.assertTrue(result['conflict'])
            self.assertIsNone(result['rule'])
            self.assertEqual(result['savings_effect'], SavingsEffect.NONE)
            reverse = evaluate_rules('UNKNOWN', 'AUTO SAVE * 1', [other, same_a])
            self.assertTrue(reverse['conflict'])

    # --- API validation ---
    def test_api_validates_targets_and_kind_fields(self):
        ok = self.client.post('/api/categorization-rules/', self.payload(), format='json')
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.data['classification'], 'TRANSFER')
        archived = Subcategory.objects.create(name='Old', budget_bucket=BudgetBucket.SAVINGS, is_active=False)
        for subcategory in (self.needs, self.wants, self.other, archived):
            response = self.client.post(
                '/api/categorization-rules/',
                self.payload(match_value='X', subcategory=subcategory.id), format='json',
            )
            self.assertEqual(response.status_code, 400, subcategory.name)
        for bad in ({'savings_effect': 'NONE'}, {'classification': 'EXPENSE'}, {'match_type': 'EQUALS'}):
            response = self.client.post(
                '/api/categorization-rules/', self.payload(match_value='Y', **bad), format='json',
            )
            self.assertEqual(response.status_code, 400, bad)
        expense_to_savings = self.client.post('/api/categorization-rules/', {
            'match_value': 'Z', 'subcategory': self.fund.id,
        }, format='json')
        self.assertEqual(expense_to_savings.status_code, 400)
        expense_effect = self.client.post('/api/categorization-rules/', {
            'match_value': 'Z', 'subcategory': self.wants.id, 'savings_effect': 'CONTRIBUTION',
        }, format='json')
        self.assertEqual(expense_effect.status_code, 400)

    def test_same_condition_active_savings_rules_are_rejected_as_duplicates(self):
        self.assertEqual(self.client.post('/api/categorization-rules/', self.payload(), format='json').status_code, 201)
        duplicate = self.client.post(
            '/api/categorization-rules/', self.payload(savings_effect='WITHDRAWAL'), format='json',
        )
        self.assertEqual(duplicate.status_code, 400)

    # --- import ---
    def test_import_preview_and_confirm_agree_and_apply_savings_rule(self):
        rule = self.savings_rule(value='AUTO SAVE *')
        withdrawal = self.savings_rule(value='AUTO PULL', effect=SavingsEffect.WITHDRAWAL, target=self.goal)
        content = (
            b'Date,Description,Type,Amount,Status\n'
            b'2026-03-05,AUTO SAVE * 1,TRANSFER_OUT,-1.00,Posted\n'
            b'2026-03-06,AUTO PULL 2,TRANSFER_IN,5.00,Posted\n'
            b'2026-03-07,NOT MATCHING 3,DEBIT_CARD,-9.00,Posted\n'
        )
        preview = self.preview(content)
        self.assertEqual(preview.status_code, 200)
        first, second, third = preview.data['valid_rows']
        self.assertEqual(first['categorization']['status'], 'AUTO_RESOLVED')
        self.assertEqual(first['categorization']['classification'], 'TRANSFER')
        self.assertEqual(first['categorization']['savings_effect'], 'CONTRIBUTION')
        self.assertEqual(second['categorization']['savings_effect'], 'WITHDRAWAL')
        self.assertEqual(second['categorization']['subcategory']['name'], 'Vacation Goal')
        self.assertEqual(preview.data['review_required_row_count'], 1)

        confirm = self.client.post(
            '/api/imports/',
            {'account': self.account.id, 'file': SimpleUploadedFile('s.csv', content, content_type='text/csv')},
            format='multipart',
        )
        self.assertEqual(confirm.status_code, 201)
        one, two, three = Transaction.objects.order_by('source_row')
        self.assertEqual(
            (one.classification, one.savings_effect, one.subcategory, one.budget_bucket,
             one.categorized_by_rule, one.review_status),
            ('TRANSFER', 'CONTRIBUTION', self.fund, 'SAVINGS', rule, ReviewStatus.NOT_REQUIRED),
        )
        self.assertEqual((two.savings_effect, two.subcategory, two.categorized_by_rule), ('WITHDRAWAL', self.goal, withdrawal))
        self.assertEqual((three.classification, three.review_status), ('EXPENSE', ReviewStatus.PENDING))

    def test_import_conflict_and_possible_duplicate_stay_in_review(self):
        self.savings_rule()
        self.savings_rule(value='AUTO', match_type=RuleMatchType.CONTAINS, effect=SavingsEffect.WITHDRAWAL)
        content = b'Date,Description,Type,Amount,Status\n2026-03-05,AUTO SAVE * 1,TRANSFER_OUT,-1.00,Posted\n'
        preview = self.preview(content)
        row = preview.data['valid_rows'][0]
        self.assertEqual(row['categorization']['status'], 'RULE_CONFLICT')
        self.assertEqual(preview.data['review_required_row_count'], 1)
        self.client.post(
            '/api/imports/',
            {'account': self.account.id, 'file': SimpleUploadedFile('s.csv', content, content_type='text/csv')},
            format='multipart',
        )
        record = Transaction.objects.get()
        self.assertEqual((record.classification, record.savings_effect), ('UNKNOWN', SavingsEffect.NONE))
        self.assertTrue(record.categorization_rule_conflict)
        self.assertEqual(record.review_status, ReviewStatus.PENDING)

        CategorizationRule.objects.all().delete()
        Transaction.objects.all().delete()
        self.savings_rule()
        self.txn(description='AUTO SAVE * 1', classification='EXPENSE', amount='-1.00')
        dup = self.preview(content)
        self.assertEqual(dup.data['valid_rows'][0]['overlap_status'], 'POSSIBLE_DUPLICATE')
        self.assertEqual(dup.data['valid_rows'][0]['categorization']['status'], 'AUTO_RESOLVED')
        self.assertEqual(dup.data['review_required_row_count'], 1)

    # --- apply to pending ---
    def test_apply_to_pending_spans_pages_skips_resolved_and_recomputes_review(self):
        rule = self.savings_rule()
        unknowns = [self.txn(description=f'AUTO SAVE * {i}') for i in range(103)]
        loose_transfer = self.txn(classification='TRANSFER', description='AUTO SAVE * T')
        manual = self.txn(classification='TRANSFER', description='AUTO SAVE * M',
                          savings_effect=SavingsEffect.WITHDRAWAL, subcategory=self.goal)
        manual.mark_reviewed()
        reviewed_transfer = self.txn(classification='TRANSFER', description='AUTO SAVE * R')
        reviewed_transfer.mark_reviewed()
        expense = self.txn(classification='EXPENSE', description='AUTO SAVE * E', subcategory=self.wants)
        duplicate = self.txn(description='AUTO SAVE * D', duplicate_resolution=DuplicateResolution.UNRESOLVED)

        counts = self.client.get(f'/api/categorization-rules/{rule.id}/pending-matches/')
        self.assertEqual(counts.data['matching_pending_count'], 105)
        response = self.client.post(f'/api/categorization-rules/{rule.id}/apply-to-pending/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['updated_count'], 105)

        unknowns[102].refresh_from_db()
        self.assertEqual(
            (unknowns[102].classification, unknowns[102].savings_effect, unknowns[102].subcategory,
             unknowns[102].budget_bucket, unknowns[102].categorized_by_rule, unknowns[102].review_status),
            ('TRANSFER', 'CONTRIBUTION', self.fund, 'SAVINGS', rule, ReviewStatus.NOT_REQUIRED),
        )
        loose_transfer.refresh_from_db()
        self.assertEqual(loose_transfer.review_status, ReviewStatus.NOT_REQUIRED)
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.savings_effect, SavingsEffect.CONTRIBUTION)
        self.assertEqual(duplicate.review_status, ReviewStatus.PENDING)
        for untouched, effect in ((manual, SavingsEffect.WITHDRAWAL), (reviewed_transfer, SavingsEffect.NONE)):
            untouched.refresh_from_db()
            self.assertEqual(untouched.savings_effect, effect)
            self.assertIsNone(untouched.categorized_by_rule)
        expense.refresh_from_db()
        self.assertEqual((expense.classification, expense.subcategory), ('EXPENSE', self.wants))

    def test_apply_skips_conflicting_candidates_and_unusable_rules_are_rejected(self):
        rule = self.savings_rule()
        self.savings_rule(value='AUTO SAVE *', effect=SavingsEffect.WITHDRAWAL)  # conflicting rule
        record = self.txn()
        counts = self.client.get(f'/api/categorization-rules/{rule.id}/pending-matches/')
        self.assertEqual((counts.data['matching_pending_count'], counts.data['conflicting_pending_count']), (0, 1))
        self.fund.is_active = False
        self.fund.save()
        response = self.client.post(f'/api/categorization-rules/{rule.id}/apply-to-pending/')
        self.assertEqual(response.status_code, 400)
        record.refresh_from_db()
        self.assertEqual(record.classification, 'UNKNOWN')

    # --- provenance ---
    def test_manual_edit_clears_provenance_and_rule_edits_do_not_rewrite_history(self):
        rule = self.savings_rule()
        self.client.post(f'/api/categorization-rules/{rule.id}/apply-to-pending/')
        record = self.txn()
        self.client.post(f'/api/categorization-rules/{rule.id}/apply-to-pending/')
        record.refresh_from_db()
        self.assertEqual(record.categorized_by_rule, rule)
        self.client.patch(f'/api/categorization-rules/{rule.id}/', {'is_active': False}, format='json')
        record.refresh_from_db()
        self.assertEqual(record.categorized_by_rule, rule)
        self.assertEqual(record.savings_effect, SavingsEffect.CONTRIBUTION)
        record.savings_effect = SavingsEffect.WITHDRAWAL
        record.save()
        record.refresh_from_db()
        self.assertIsNone(record.categorized_by_rule)

    def test_rule_with_provenance_cannot_be_deleted(self):
        rule = self.savings_rule()
        self.txn()
        self.client.post(f'/api/categorization-rules/{rule.id}/apply-to-pending/')
        self.assertEqual(self.client.delete(f'/api/categorization-rules/{rule.id}/').status_code, 409)

    # --- accounting parity / reconciliation ---
    def test_rule_generated_savings_reports_identically_to_manual_and_leaves_review(self):
        rule = self.savings_rule()
        withdrawal = self.savings_rule(value='AUTO PULL', effect=SavingsEffect.WITHDRAWAL)
        self.txn(description='AUTO SAVE * 1', amount='-100.00')
        self.txn(description='AUTO PULL * 1', amount='40.00')
        for current in (rule, withdrawal):
            self.client.post(f'/api/categorization-rules/{current.id}/apply-to-pending/')
        urls = (
            '/api/reports/all-time/',
            '/api/reports/yearly/?year=2026',
            '/api/reports/monthly/?year=2026&month=3',
        )
        automated = [self.client.get(url).json() for url in urls]
        Transaction.objects.all().delete()
        for description, amount, effect, target in (
            ('AUTO SAVE * 1', '-100.00', SavingsEffect.CONTRIBUTION, self.fund),
            ('AUTO PULL * 1', '40.00', SavingsEffect.WITHDRAWAL, self.fund),
        ):
            manual = self.txn(description=description, amount=amount, classification='TRANSFER',
                              savings_effect=effect, subcategory=target)
            manual.mark_reviewed()
        for url, expected in zip(urls, automated):
            actual = self.client.get(url).json()
            self.assertEqual(actual['summary'], expected['summary'], url)
            self.assertEqual(actual['data_completeness'], expected['data_completeness'], url)
            self.assertFalse(actual['data_completeness']['is_incomplete'])
        self.assertEqual(automated[0]['summary']['savings_contributions']['amount'], '100.00')

    def test_rule_completed_rows_are_not_in_review_and_not_report_blockers(self):
        rule = self.savings_rule()
        record = self.txn()
        self.client.post(f'/api/categorization-rules/{rule.id}/apply-to-pending/')
        pending = {item['id'] for item in self.client.get('/api/transactions/?review_status=PENDING').json()['results']}
        self.assertNotIn(record.pk, pending)
        self.assertFalse(self.client.get('/api/reports/all-time/').json()['data_completeness']['is_incomplete'])


class RuleKindMigrationTests(TransactionTestCase):
    def test_existing_expense_rules_become_expense_category_rules(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('finance', '0009_repair_stale_review_state')])
        apps = executor.loader.project_state([('finance', '0009_repair_stale_review_state')]).apps
        subcategory = apps.get_model('finance', 'Subcategory').objects.create(name='Legacy', budget_bucket='WANTS')
        rule_id = apps.get_model('finance', 'CategorizationRule').objects.create(
            match_value='LEGACY', subcategory_id=subcategory.pk,
        ).pk
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
            rule = CategorizationRule.objects.get(pk=rule_id)
            self.assertEqual(
                (rule.allow_unknown, rule.classification, rule.savings_effect, rule.match_type),
                (False, 'EXPENSE', SavingsEffect.NONE, 'CONTAINS'),
            )
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())

    def test_existing_outcomes_and_provenance_survive_generalization(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('finance', '0010_savings_transfer_rules')])
        apps = executor.loader.project_state([('finance', '0010_savings_transfer_rules')]).apps
        account = apps.get_model('finance', 'Account').objects.create(name='Synthetic account')
        category = apps.get_model('finance', 'Subcategory')
        wants = category.objects.create(name='Synthetic dining', budget_bucket='WANTS')
        savings = category.objects.create(name='Synthetic savings', budget_bucket='SAVINGS')
        old_rule = apps.get_model('finance', 'CategorizationRule')
        expense = old_rule.objects.create(match_value='EXAMPLE CAFE', subcategory_id=wants.pk)
        transfer = old_rule.objects.create(
            rule_kind='SAVINGS_TRANSFER', classification='TRANSFER',
            savings_effect='CONTRIBUTION', match_type='STARTS_WITH',
            match_value='AUTO SAVE *', subcategory_id=savings.pk,
        )
        record = apps.get_model('finance', 'Transaction').objects.create(
            account_id=account.pk, date=date(2026, 3, 5), raw_description='EXAMPLE CAFE',
            amount=Decimal('-5.00'), classification='EXPENSE', subcategory_id=wants.pk,
            budget_bucket='WANTS', categorized_by_rule_id=expense.pk, review_status='NOT_REQUIRED',
        )
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
            expense_rule = CategorizationRule.objects.get(pk=expense.pk)
            savings_rule = CategorizationRule.objects.get(pk=transfer.pk)
            migrated = Transaction.objects.get(pk=record.pk)
            self.assertFalse(expense_rule.allow_unknown)
            self.assertFalse(rule_matches(expense_rule, 'UNKNOWN', 'EXAMPLE CAFE'))
            self.assertTrue(rule_matches(expense_rule, 'EXPENSE', 'EXAMPLE CAFE'))
            self.assertTrue(rule_matches(savings_rule, 'UNKNOWN', 'AUTO SAVE * 1'))
            self.assertEqual((savings_rule.classification, savings_rule.savings_effect, savings_rule.subcategory_id),
                             ('TRANSFER', 'CONTRIBUTION', savings.pk))
            self.assertEqual((migrated.classification, migrated.subcategory_id, migrated.budget_bucket,
                              migrated.categorized_by_rule_id, migrated.review_status, migrated.manually_resolved),
                             ('EXPENSE', wants.pk, 'WANTS', expense.pk, 'NOT_REQUIRED', False))
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
