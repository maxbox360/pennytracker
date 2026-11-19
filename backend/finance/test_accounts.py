from datetime import date
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from rest_framework.test import APITestCase

from .accounting import monthly_report
from .models import Account, BudgetBucket, BudgetTargetSettings, CategorizationRule, Subcategory, Transaction
from .tests import SYNTHETIC_CSV


class AccountApiTests(APITestCase):
    def create_account(self, name='Example Checking', account_type='CHECKING', **kwargs):
        return self.client.post('/api/accounts/', {
            'name': name, 'account_type': account_type, **kwargs,
        }, format='json')

    def patch_account(self, account, **kwargs):
        return self.client.patch(f'/api/accounts/{account.pk}/', kwargs, format='json')

    def upload(self):
        return SimpleUploadedFile('example.csv', SYNTHETIC_CSV, content_type='text/csv')

    def test_create_all_supported_types_and_defaults(self):
        for account_type in Account.Type.values:
            with self.subTest(account_type=account_type):
                result = self.create_account(f'Example {account_type}', account_type)
                self.assertEqual(result.status_code, 201)
                self.assertEqual(result.data['account_type'], account_type)
                self.assertTrue(result.data['is_active'])
                self.assertFalse(result.data['is_savings_destination'])

        self.assertEqual(self.client.post('/api/accounts/', {'name': 'Example'}, format='json').status_code, 400)

    def test_names_are_trimmed_and_active_collisions_are_rejected(self):
        result = self.create_account('  Example Checking  ', institution='Example Bank')
        self.assertEqual(result.data['name'], 'Example Checking')
        self.assertEqual(result.data['institution'], 'Example Bank')
        self.assertEqual(self.create_account('   ').status_code, 400)
        duplicate = self.create_account(' example CHECKING ')
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn('already exists', str(duplicate.data['name']))

        account = Account.objects.get(pk=result.data['id'])
        self.patch_account(account, is_active=False)
        self.assertEqual(self.create_account('example checking').status_code, 201)
        duplicate = self.patch_account(account, is_active=True)
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn('already exists', str(duplicate.data['name']))
        self.assertEqual(self.patch_account(account, name='Example Archived').status_code, 200)
        self.assertEqual(self.patch_account(account, is_active=True).status_code, 200)
        self.assertEqual(self.patch_account(account, name='EXAMPLE CHECKING').status_code, 400)

    def test_savings_destination_types_and_valid_transitions(self):
        for account_type in Account.Type.values:
            with self.subTest(account_type=account_type):
                result = self.create_account(
                    f'Example {account_type}', account_type, is_savings_destination=True,
                )
                self.assertEqual(result.status_code, 201 if account_type in ('SAVINGS', 'INVESTMENT') else 400)
                if result.status_code == 400:
                    self.assertIn('Savings', str(result.data['is_savings_destination']))

        account = Account.objects.create(name='Example Savings', account_type=Account.Type.SAVINGS)
        self.assertEqual(self.patch_account(account, is_savings_destination=True).status_code, 200)
        account.refresh_from_db()
        self.assertEqual(self.patch_account(account, account_type='CREDIT_CARD').status_code, 400)
        self.assertEqual(self.patch_account(
            account, account_type='CREDIT_CARD', is_savings_destination=False,
        ).status_code, 200)
        self.assertEqual(self.patch_account(account, account_type='INVESTMENT', is_savings_destination=True).status_code, 200)

    def test_legacy_invalid_destinations_can_be_edited_without_silent_cleanup(self):
        for account_type in ('CHECKING', 'CREDIT_CARD', 'LOAN', 'OTHER'):
            with self.subTest(account_type=account_type):
                account = Account.objects.create(
                    name=f'Example {account_type}', account_type=account_type, is_savings_destination=True,
                )
                self.assertEqual(self.patch_account(account, institution='Example Credit Union').status_code, 200)
                self.assertEqual(self.patch_account(account, is_active=False).status_code, 200)
                self.assertEqual(self.patch_account(account, is_active=True).status_code, 200)
                self.assertEqual(self.patch_account(account, account_type='CREDIT_CARD' if account_type != 'CREDIT_CARD' else 'LOAN').status_code, 400)
                account.refresh_from_db()
                self.assertTrue(account.is_savings_destination)
                self.assertEqual(account.account_type, account_type)
                self.assertEqual(self.patch_account(account, is_savings_destination=False).status_code, 200)

    def test_rename_archive_and_reactivate_preserve_history_and_import_eligibility(self):
        account = Account.objects.create(name='Example Checking')
        category = Subcategory.objects.create(name='Example Needs', budget_bucket=BudgetBucket.NEEDS)
        rule = CategorizationRule.objects.create(
            match_value='EXAMPLE', classification='EXPENSE', subcategory=category,
        )
        record = Transaction.objects.create(
            account=account, date=date(2026, 1, 15), raw_description='EXAMPLE PURCHASE',
            amount=Decimal('-12.34'), classification='EXPENSE', subcategory=category,
            categorized_by_rule=rule,
        )
        pending = Transaction.objects.create(
            account=account, date=date(2026, 1, 16), raw_description='EXAMPLE UNKNOWN',
            amount=Decimal('-1.00'), classification='UNKNOWN',
        )
        before = Transaction.objects.values().get(pk=record.pk)
        summary = monthly_report(2026, 1, BudgetTargetSettings.objects.get(pk=1))['summary']
        result = self.patch_account(
            account, name='Example Renamed', account_type='SAVINGS',
            institution='Example Bank', is_savings_destination=True,
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.patch_account(account, is_active=False).status_code, 200)
        account.refresh_from_db()
        self.assertFalse(account.is_active)
        self.assertEqual(Transaction.objects.values().get(pk=record.pk), before)
        self.assertEqual(monthly_report(2026, 1, BudgetTargetSettings.objects.get(pk=1))['summary'], summary)
        self.assertIn(account.pk, [item['id'] for item in self.client.get('/api/accounts/').data])
        for query in ('', f'&review_status={record.review_status}'):
            result = self.client.get(f'/api/transactions/?account={account.pk}{query}')
            self.assertTrue(any(row['id'] == record.pk and row['account_name'] == 'Example Renamed' for row in result.data['results']))
        review = self.client.get(f'/api/transactions/?account={account.pk}&review_status=PENDING')
        self.assertTrue(any(row['id'] == pending.pk for row in review.data['results']))
        drilldown = self.client.get('/api/reports/monthly/drilldown/?year=2026&month=1&metric=needs')
        self.assertEqual(drilldown.status_code, 200)
        self.assertTrue(any(row['id'] == record.pk and row['account'] == 'Example Renamed' for row in drilldown.data['results']))
        for endpoint in ('/api/imports/preview/', '/api/imports/'):
            result = self.client.post(endpoint, {'account': account.pk, 'file': self.upload()}, format='multipart')
            self.assertEqual(result.status_code, 400)
            self.assertIn('active account', result.data['detail'])
        self.assertEqual(self.patch_account(account, is_active=True).status_code, 200)
        result = self.client.post('/api/imports/preview/', {'account': account.pk, 'file': self.upload()}, format='multipart')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(Transaction.objects.values().get(pk=record.pk), before)
        result = self.client.post('/api/imports/', {'account': account.pk, 'file': self.upload()}, format='multipart')
        self.assertEqual(result.status_code, 201)
        self.assertEqual(Transaction.objects.values().get(pk=record.pk), before)

    def test_savings_destination_never_classifies_or_changes_transfer_accounting(self):
        account = Account.objects.create(name='Example Savings', account_type=Account.Type.SAVINGS)
        savings = Subcategory.objects.create(name='Example Goal', budget_bucket=BudgetBucket.SAVINGS)
        ordinary = Transaction.objects.create(
            account=account, date=date(2026, 1, 15), raw_description='EXAMPLE TRANSFER',
            amount=Decimal('-20.00'), classification='TRANSFER',
        )
        contribution = Transaction.objects.create(
            account=account, date=date(2026, 1, 16), raw_description='EXAMPLE SAVING',
            amount=Decimal('-30.00'), classification='TRANSFER',
            savings_effect='CONTRIBUTION', subcategory=savings,
        )
        before = list(Transaction.objects.order_by('pk').values())
        summary = monthly_report(2026, 1, BudgetTargetSettings.objects.get(pk=1))['summary']
        self.assertEqual(summary['savings_contributions']['amount'], '30.00')
        self.assertEqual(self.patch_account(account, is_savings_destination=True).status_code, 200)
        self.assertEqual(list(Transaction.objects.order_by('pk').values()), before)
        self.assertEqual(monthly_report(2026, 1, BudgetTargetSettings.objects.get(pk=1))['summary'], summary)
        ordinary.refresh_from_db()
        contribution.refresh_from_db()
        self.assertEqual(ordinary.savings_effect, 'NONE')
        self.assertEqual(contribution.savings_effect, 'CONTRIBUTION')

    def test_delete_is_not_allowed_even_without_history(self):
        account = Account.objects.create(name='Example Checking')
        self.assertEqual(self.client.delete(f'/api/accounts/{account.pk}/').status_code, 405)
        self.assertTrue(Account.objects.filter(pk=account.pk).exists())


class AccountCsrfTests(TestCase):
    def test_create_and_update_require_valid_csrf(self):
        client = Client(enforce_csrf_checks=True)
        account = Account.objects.create(name='Example Checking')
        body = '{"name": "Example Savings", "account_type": "SAVINGS"}'
        self.assertEqual(client.post('/api/accounts/', body, content_type='application/json').status_code, 403)
        self.assertEqual(client.patch(f'/api/accounts/{account.pk}/', '{"name": "Example Renamed"}', content_type='application/json').status_code, 403)
        token = client.get('/api/csrf/').json()['csrfToken']
        self.assertEqual(client.post('/api/accounts/', body, content_type='application/json', HTTP_X_CSRFTOKEN=token).status_code, 201)
        self.assertEqual(client.patch(f'/api/accounts/{account.pk}/', '{"name": "Example Renamed"}', content_type='application/json', HTTP_X_CSRFTOKEN=token).status_code, 200)
