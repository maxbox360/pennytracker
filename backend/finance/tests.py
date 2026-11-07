from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APITestCase

from .models import Account, BudgetBucket, Subcategory, Transaction


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
