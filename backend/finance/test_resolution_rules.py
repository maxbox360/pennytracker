"""Regression tests for generalized categorization-rule outcomes and Review resolution."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient, APITestCase

from .categorization_rules import evaluate_transaction
from .models import (
    Account,
    BudgetBucket,
    CategorizationRule,
    DuplicateResolution,
    ReviewStatus,
    SavingsEffect,
    Subcategory,
    SubcategoryScope,
    Transaction,
)

BASE = '/api/categorization-rules/'


class ResolutionTestCase(APITestCase):
    def setUp(self):
        self.account = Account.objects.create(name='Synthetic Checking')
        self.needs = Subcategory.objects.create(name='Utilities', budget_bucket=BudgetBucket.NEEDS)
        self.wants = Subcategory.objects.create(name='Dining', budget_bucket=BudgetBucket.WANTS)
        self.fund = Subcategory.objects.create(name='Rainy Day', budget_bucket=BudgetBucket.SAVINGS)
        self.goal = Subcategory.objects.create(name='Trip Goal', budget_bucket=BudgetBucket.SAVINGS)
        self.gift = Subcategory.objects.create(
            name='Gifts', scope=SubcategoryScope.OTHER_INFLOW, budget_bucket=None,
        )
        self.archived = Subcategory.objects.create(
            name='Old Needs', budget_bucket=BudgetBucket.NEEDS, is_active=False,
        )

    def txn(self, description='ACME WIDGETS 1', classification='UNKNOWN', amount='-10.00',
            day=5, month=3, **extra):
        return Transaction.objects.create(
            account=self.account, date=date(2026, month, day), raw_description=description,
            amount=Decimal(amount), classification=classification, **extra,
        )

    def rule_payload(self, **overrides):
        return {
            'match_field': 'RAW_DESCRIPTION',
            'match_type': 'CONTAINS',
            'match_value': 'ACME',
            'classification': 'EXPENSE',
            'subcategory': self.needs.id,
            **overrides,
        }

    def make_rule(self, value='ACME', classification='EXPENSE', subcategory='default',
                  savings_effect='NONE', match_type='CONTAINS', **extra):
        response = self.client.post(BASE, {
            'match_field': 'RAW_DESCRIPTION',
            'match_type': match_type,
            'match_value': value,
            'classification': classification,
            'savings_effect': savings_effect,
            'subcategory': self.needs.id if subcategory == 'default' else subcategory,
            **extra,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        return CategorizationRule.objects.get(pk=response.json()['id'])

    def review_save(self, record, **payload):
        return self.client.post(f'/api/transactions/{record.pk}/review-save/', payload, format='json')

    def refreshed(self, record):
        record.refresh_from_db()
        return record


class RuleOutcomeApiTests(ResolutionTestCase):
    def test_every_outcome_can_be_created_with_nullable_subcategory(self):
        cases = (
            ('INCOME', 'NONE', None),
            ('EXCLUDED', 'NONE', None),
            ('TRANSFER', 'NONE', None),
            ('EXPENSE', 'NONE', self.needs.id),
            ('REFUND', 'NONE', self.wants.id),
            ('OTHER_INFLOW', 'NONE', self.gift.id),
            ('OTHER_INFLOW', 'NONE', None),
            ('TRANSFER', 'CONTRIBUTION', self.fund.id),
            ('TRANSFER', 'WITHDRAWAL', self.fund.id),
        )
        for number, (classification, effect, subcategory) in enumerate(cases):
            with self.subTest(classification=classification, effect=effect, subcategory=subcategory):
                response = self.client.post(BASE, self.rule_payload(
                    match_value=f'OUTCOME {number}',
                    classification=classification,
                    savings_effect=effect,
                    subcategory=subcategory,
                ), format='json')
                self.assertEqual(response.status_code, 201, response.content)
                body = response.json()
                self.assertEqual(body['classification'], classification)
                self.assertEqual(body['savings_effect'], effect)
                self.assertEqual(body['subcategory'], subcategory)

    def test_allow_unknown_defaults_true_for_expense_and_round_trips(self):
        default = self.client.post(BASE, self.rule_payload(match_value='DEFAULT'), format='json')
        self.assertEqual(default.status_code, 201, default.content)
        self.assertTrue(default.json()['allow_unknown'])
        strict = self.client.post(
            BASE, self.rule_payload(match_value='STRICT', allow_unknown=False), format='json',
        )
        self.assertEqual(strict.status_code, 201, strict.content)
        self.assertFalse(strict.json()['allow_unknown'])
        self.assertFalse(self.client.get(f"{BASE}{strict.json()['id']}/").json()['allow_unknown'])
        self.assertTrue(self.client.get(f"{BASE}{default.json()['id']}/").json()['allow_unknown'])

    def test_match_type_is_limited_to_contains_and_starts_with(self):
        for match_type in ('CONTAINS', 'STARTS_WITH'):
            response = self.client.post(BASE, self.rule_payload(
                match_type=match_type, match_value=f'MT {match_type}',
            ), format='json')
            self.assertEqual(response.status_code, 201, response.content)
        for bad in ('EQUALS', 'REGEX', ''):
            with self.subTest(match_type=bad):
                response = self.client.post(BASE, self.rule_payload(
                    match_type=bad, match_value=f'BAD {bad}',
                ), format='json')
                self.assertEqual(response.status_code, 400)

    def test_validation_rejects_inconsistent_outcomes(self):
        invalid = (
            ('blank value', {'match_value': '   '}),
            ('expense without subcategory', {'subcategory': None}),
            ('expense without subcategory, strict', {'subcategory': None, 'allow_unknown': False}),
            ('allow_unknown on non-expense', {
                'classification': 'INCOME', 'subcategory': None, 'allow_unknown': True,
            }),
            ('unknown classification', {'classification': 'UNKNOWN'}),
            ('bogus classification', {'classification': 'BOGUS'}),
            ('savings effect on expense', {'savings_effect': 'CONTRIBUTION'}),
            ('savings effect on income', {
                'classification': 'INCOME', 'subcategory': None, 'savings_effect': 'CONTRIBUTION',
            }),
            ('savings effect without subcategory', {
                'classification': 'TRANSFER', 'subcategory': None, 'savings_effect': 'CONTRIBUTION',
            }),
            ('savings effect with budget subcategory', {
                'classification': 'TRANSFER', 'subcategory': self.wants.id, 'savings_effect': 'CONTRIBUTION',
            }),
            ('expense with savings subcategory', {'subcategory': self.fund.id}),
            ('expense with other-inflow subcategory', {'subcategory': self.gift.id}),
            ('refund with savings subcategory', {'classification': 'REFUND', 'subcategory': self.fund.id}),
            ('other inflow with budget subcategory', {
                'classification': 'OTHER_INFLOW', 'subcategory': self.needs.id,
            }),
            ('archived subcategory', {'subcategory': self.archived.id}),
            ('missing subcategory id', {'subcategory': 999999}),
        )
        for label, overrides in invalid:
            with self.subTest(label):
                response = self.client.post(BASE, self.rule_payload(**overrides), format='json')
                self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(CategorizationRule.objects.count(), 0)

    def test_same_condition_and_eligibility_scope_is_rejected_even_with_a_different_outcome(self):
        self.make_rule('DUPE CO')
        same_outcome = self.client.post(BASE, self.rule_payload(match_value='  dupe   co '), format='json')
        self.assertEqual(same_outcome.status_code, 400, same_outcome.content)
        same_scope_other_outcome = self.client.post(BASE, self.rule_payload(
            match_value='DUPE CO', subcategory=self.wants.id,
        ), format='json')
        self.assertEqual(same_scope_other_outcome.status_code, 400)
        self.make_rule('NON EXPENSE', 'INCOME', None)
        other_non_expense = self.client.post(BASE, self.rule_payload(
            match_value='NON EXPENSE', classification='EXCLUDED', subcategory=None,
        ), format='json')
        self.assertEqual(other_non_expense.status_code, 400, other_non_expense.content)
        self.assertEqual(CategorizationRule.objects.count(), 2)

    def test_same_condition_with_different_eligibility_scopes_is_permitted(self):
        self.make_rule('SCOPES', allow_unknown=False)
        income = self.client.post(BASE, self.rule_payload(
            match_value='SCOPES', classification='INCOME', subcategory=None,
        ), format='json')
        self.assertEqual(income.status_code, 201, income.content)
        permissive = self.client.post(BASE, self.rule_payload(
            match_value='SCOPES', subcategory=self.wants.id, allow_unknown=True,
        ), format='json')
        self.assertEqual(permissive.status_code, 201, permissive.content)
        savings = self.client.post(BASE, self.rule_payload(
            match_value='SCOPES', classification='TRANSFER', savings_effect='CONTRIBUTION',
            subcategory=self.fund.id,
        ), format='json')
        self.assertEqual(savings.status_code, 201, savings.content)

    def test_same_value_with_different_match_type_is_not_an_exact_duplicate(self):
        self.make_rule('SCOPED')
        prefix = self.client.post(BASE, self.rule_payload(
            match_value='SCOPED', match_type='STARTS_WITH', subcategory=self.wants.id,
        ), format='json')
        self.assertEqual(prefix.status_code, 201, prefix.content)

    def test_updating_a_rule_into_an_exact_duplicate_is_rejected(self):
        self.make_rule('FIRST')
        second = self.make_rule('SECOND', subcategory=self.wants.id)
        response = self.client.patch(f'{BASE}{second.pk}/', {'match_value': 'first'}, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        second.refresh_from_db()
        self.assertEqual(second.match_value, 'SECOND')

    def test_legacy_rules_are_strict_expense_and_savings_rules_still_work(self):
        legacy = CategorizationRule.objects.create(
            match_value='LEGACY', subcategory=self.needs, allow_unknown=False,
        )
        savings = CategorizationRule.objects.create(
            classification='TRANSFER',
            savings_effect=SavingsEffect.CONTRIBUTION, match_type='STARTS_WITH',
            match_value='SAVE ME', subcategory=self.fund,
        )
        body = self.client.get(f'{BASE}{legacy.pk}/').json()
        self.assertEqual((body['classification'], body['savings_effect']), ('EXPENSE', 'NONE'))
        self.assertFalse(body['allow_unknown'])
        body = self.client.get(f'{BASE}{savings.pk}/').json()
        self.assertEqual((body['classification'], body['savings_effect']), ('TRANSFER', 'CONTRIBUTION'))
        self.assertEqual(body['subcategory'], self.fund.id)

        unknown = self.txn('LEGACY 1')
        known = self.txn('LEGACY 2', 'EXPENSE')
        applied = self.client.post(f'{BASE}{legacy.pk}/apply-to-pending/')
        self.assertEqual(applied.status_code, 200)
        self.assertEqual(self.refreshed(unknown).classification, 'UNKNOWN')
        self.assertEqual(self.refreshed(known).subcategory, self.needs)


class ApplyToPendingEligibilityTests(ResolutionTestCase):
    def apply(self, rule):
        response = self.client.post(f'{BASE}{rule.pk}/apply-to-pending/')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_non_expense_rules_resolve_unknown_transactions_with_provenance(self):
        cases = (
            ('INCOME', 'NONE', None, '25.00'),
            ('EXCLUDED', 'NONE', None, '-3.00'),
            ('REFUND', 'NONE', self.wants.id, '8.00'),
            ('OTHER_INFLOW', 'NONE', self.gift.id, '40.00'),
            ('EXPENSE', 'NONE', self.needs.id, '-9.00'),
            ('TRANSFER', 'CONTRIBUTION', self.fund.id, '-50.00'),
            ('TRANSFER', 'WITHDRAWAL', self.fund.id, '50.00'),
        )
        for number, (classification, effect, subcategory, amount) in enumerate(cases):
            with self.subTest(classification=classification, effect=effect):
                value = f'PROV {number} {classification}'
                rule = self.make_rule(value, classification, subcategory, effect)
                record = self.txn(f'{value} X', amount=amount)
                result = self.apply(rule)
                self.assertEqual(result['updated_count'], 1)
                record = self.refreshed(record)
                self.assertEqual(record.classification, classification)
                self.assertEqual(record.savings_effect, effect)
                self.assertEqual(record.subcategory_id, subcategory)
                self.assertEqual(record.categorized_by_rule, rule)
                self.assertFalse(record.categorization_rule_conflict)

    def test_unknown_with_savings_rule_snapshots_savings_bucket(self):
        rule = self.make_rule('AUTO SAVE', 'TRANSFER', self.fund.id, 'CONTRIBUTION', match_type='STARTS_WITH')
        record = self.txn('AUTO SAVE 1')
        self.apply(rule)
        record = self.refreshed(record)
        self.assertEqual(record.budget_bucket, BudgetBucket.SAVINGS)
        self.assertEqual(record.categorized_by_rule, rule)

    def test_expense_rule_eligibility_matrix(self):
        permissive = self.make_rule('PERMIT', allow_unknown=True)
        strict = self.make_rule('STRICT', allow_unknown=False)
        eligible = {
            'permit-unknown': self.txn('PERMIT 1'),
            'permit-bare-expense': self.txn('PERMIT 2', 'EXPENSE'),
            'strict-bare-expense': self.txn('STRICT 1', 'EXPENSE'),
        }
        ineligible = {
            'strict-unknown': self.txn('STRICT 2'),
            'permit-categorized-expense': self.txn('PERMIT 3', 'EXPENSE', subcategory=self.wants),
            'permit-income': self.txn('PERMIT 4', 'INCOME', amount='5.00'),
            'permit-refund': self.txn('PERMIT 5', 'REFUND', subcategory=self.wants, amount='5.00'),
            'permit-excluded': self.txn('PERMIT 6', 'EXCLUDED'),
            'permit-other': self.txn('PERMIT 7', 'OTHER_INFLOW', subcategory=self.gift, amount='5.00'),
            'permit-transfer': self.txn('PERMIT 8', 'TRANSFER'),
        }
        before = {key: self.refreshed(record).classification for key, record in ineligible.items()}
        self.apply(permissive)
        self.apply(strict)
        for key, record in eligible.items():
            with self.subTest(key):
                record = self.refreshed(record)
                self.assertEqual(record.classification, 'EXPENSE')
                self.assertEqual(record.subcategory, self.needs)
                self.assertIsNotNone(record.categorized_by_rule)
        for key, record in ineligible.items():
            with self.subTest(key):
                record = self.refreshed(record)
                self.assertEqual(record.classification, before[key])
                self.assertIsNone(record.categorized_by_rule)
        self.assertEqual(self.refreshed(ineligible['permit-categorized-expense']).subcategory, self.wants)
        self.assertIsNone(self.refreshed(ineligible['strict-unknown']).subcategory)

    def test_savings_rule_eligibility_matrix(self):
        rule = self.make_rule('AUTO SAVE', 'TRANSFER', self.fund.id, 'CONTRIBUTION', match_type='STARTS_WITH')
        legacy = self.txn('AUTO SAVE 5', 'TRANSFER')
        Transaction.objects.filter(pk=legacy.pk).update(subcategory=self.goal, budget_bucket=BudgetBucket.SAVINGS)
        legacy.refresh_from_db()
        eligible = {
            'unknown': self.txn('AUTO SAVE 1'),
            'pending-bare-transfer': self.txn('AUTO SAVE 2', 'TRANSFER'),
        }
        ineligible = {
            'manually-resolved-transfer': self.txn('AUTO SAVE 3', 'TRANSFER', manually_resolved=True),
            'transfer-with-category': self.txn(
                'AUTO SAVE 4', 'TRANSFER', subcategory=self.goal, savings_effect=SavingsEffect.WITHDRAWAL,
            ),
            'legacy-transfer-with-category': legacy,
            'expense': self.txn('AUTO SAVE 7', 'EXPENSE', subcategory=self.wants),
            'income': self.txn('AUTO SAVE 8', 'INCOME', amount='5.00'),
            'refund': self.txn('AUTO SAVE 9', 'REFUND', subcategory=self.wants, amount='5.00'),
            'excluded': self.txn('AUTO SAVE 10', 'EXCLUDED'),
            'other': self.txn('AUTO SAVE 11', 'OTHER_INFLOW', subcategory=self.gift, amount='5.00'),
        }
        snapshot = {
            key: (r.classification, r.subcategory_id, r.savings_effect, r.review_status)
            for key, r in ineligible.items()
        }
        self.apply(rule)
        for key, record in eligible.items():
            with self.subTest(key):
                record = self.refreshed(record)
                self.assertEqual(record.savings_effect, SavingsEffect.CONTRIBUTION)
                self.assertEqual(record.subcategory, self.fund)
                self.assertEqual(record.categorized_by_rule, rule)
        for key, record in ineligible.items():
            with self.subTest(key):
                record = self.refreshed(record)
                self.assertEqual(
                    (record.classification, record.subcategory_id, record.savings_effect, record.review_status),
                    snapshot[key],
                )
                self.assertIsNone(record.categorized_by_rule)

    def test_rule_resolved_pending_ordinary_transfer_is_not_unresolved(self):
        ordinary = self.make_rule('EXAMPLE MOVE', 'TRANSFER', None, 'NONE')
        record = self.txn('EXAMPLE MOVE 123', duplicate_resolution=DuplicateResolution.UNRESOLVED)
        self.assertEqual(self.apply(ordinary)['updated_count'], 1)
        record.refresh_from_db()
        self.assertEqual((record.classification, record.savings_effect, record.subcategory_id,
                          record.categorized_by_rule_id, record.review_status, record.manually_resolved),
                         ('TRANSFER', 'NONE', None, ordinary.pk, ReviewStatus.PENDING, False))
        savings = self.make_rule('EXAMPLE MOVE 123', 'TRANSFER', self.fund.pk, 'CONTRIBUTION',
                                 match_type='STARTS_WITH')
        self.assertEqual(evaluate_transaction(record, [savings])['rules'], [])
        self.assertEqual(self.apply(savings)['updated_count'], 0)
        record.refresh_from_db()
        self.assertEqual((record.savings_effect, record.subcategory_id, record.categorized_by_rule_id,
                          record.review_status), ('NONE', None, ordinary.pk, ReviewStatus.PENDING))

    def test_identical_outcomes_from_overlapping_rules_do_not_conflict(self):
        broad = self.make_rule('ACME')
        self.make_rule('ACME WIDGETS', match_type='STARTS_WITH')
        record = self.txn('ACME WIDGETS 1')
        self.assertEqual(self.apply(broad)['updated_count'], 1)
        self.assertFalse(self.refreshed(record).categorization_rule_conflict)

    def test_overlapping_nonidentical_rules_conflict_at_runtime_and_stay_pending(self):
        broad = self.make_rule('ACME')
        self.make_rule('ACME WIDGETS', 'INCOME', None, match_type='STARTS_WITH')
        record = self.txn('ACME WIDGETS 1')
        result = self.apply(broad)
        self.assertEqual(result['updated_count'], 0)
        self.assertEqual(result['conflicting_count'], 1)
        record = self.refreshed(record)
        self.assertEqual(record.classification, 'UNKNOWN')
        self.assertIsNone(record.categorized_by_rule)
        self.assertEqual(record.review_status, ReviewStatus.PENDING)

    def test_ordinary_and_savings_transfers_conflict(self):
        ordinary = self.make_rule('EXAMPLE MOVE', 'TRANSFER', None, 'NONE')
        self.make_rule('EXAMPLE MOVE 123', 'TRANSFER', self.fund.pk, 'CONTRIBUTION',
                       match_type='STARTS_WITH')
        record = self.txn('EXAMPLE MOVE 123')
        response = self.client.post(f'{BASE}{ordinary.pk}/apply-to-pending/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['conflicting_count'], 1)
        record.refresh_from_db()
        self.assertEqual((record.classification, record.categorization_rule_conflict, record.review_status),
                         ('UNKNOWN', True, ReviewStatus.PENDING))

    def test_apply_to_pending_is_atomic(self):
        rule = self.make_rule('BULK')
        records = [self.txn(f'BULK {number}') for number in range(3)]
        real_save = Transaction.save
        calls = {'count': 0}

        def flaky_save(instance, *args, **kwargs):
            calls['count'] += 1
            if calls['count'] == 2:
                raise RuntimeError('synthetic failure')
            return real_save(instance, *args, **kwargs)

        client = APIClient(raise_request_exception=False)
        with patch.object(Transaction, 'save', flaky_save):
            response = client.post(f'{BASE}{rule.pk}/apply-to-pending/')
        self.assertGreaterEqual(response.status_code, 400)
        for record in records:
            record = self.refreshed(record)
            self.assertEqual(record.classification, 'UNKNOWN')
            self.assertIsNone(record.categorized_by_rule)

    def test_creating_or_editing_a_rule_does_not_change_pending_transactions(self):
        record = self.txn('SILENT 1')
        rule = self.make_rule('SILENT')
        self.client.patch(f'{BASE}{rule.pk}/', {'subcategory': self.wants.id}, format='json')
        record = self.refreshed(record)
        self.assertEqual(record.classification, 'UNKNOWN')
        self.assertIsNone(record.categorized_by_rule)


class ReviewSaveTests(ResolutionTestCase):
    def test_transfer_defaults_and_classification_transitions(self):
        unknown = self.txn('EXAMPLE INTERNAL TRANSFER')
        response = self.review_save(unknown, classification='TRANSFER')
        self.assertEqual(response.status_code, 200, response.content)
        unknown.refresh_from_db()
        self.assertEqual((unknown.savings_effect, unknown.subcategory_id, unknown.review_status,
                          unknown.manually_resolved), ('NONE', None, ReviewStatus.REVIEWED, True))

        expense = self.txn('EXAMPLE MOVEMENT', 'EXPENSE', subcategory=self.needs,
                           duplicate_resolution=DuplicateResolution.UNRESOLVED)
        response = self.review_save(expense, classification='TRANSFER')
        self.assertEqual(response.status_code, 200, response.content)
        expense.refresh_from_db()
        self.assertEqual((expense.savings_effect, expense.subcategory_id, expense.budget_bucket,
                          expense.review_status, expense.manually_resolved),
                         ('NONE', None, '', ReviewStatus.PENDING, True))

        savings = self.txn('EXAMPLE SAVINGS', 'TRANSFER', subcategory=self.fund,
                           savings_effect='CONTRIBUTION', duplicate_resolution=DuplicateResolution.UNRESOLVED)
        response = self.review_save(savings, savings_effect='NONE')
        self.assertEqual(response.status_code, 200, response.content)
        savings.refresh_from_db()
        self.assertEqual((savings.savings_effect, savings.subcategory_id, savings.budget_bucket),
                         ('NONE', None, ''))
        response = self.review_save(savings, classification='EXPENSE', subcategory=self.needs.pk)
        self.assertEqual(response.status_code, 200, response.content)
        savings.refresh_from_db()
        self.assertEqual((savings.classification, savings.savings_effect, savings.subcategory_id),
                         ('EXPENSE', 'NONE', self.needs.pk))

        savings2 = self.txn('EXAMPLE SAVINGS 2', 'TRANSFER', subcategory=self.fund,
                            savings_effect='WITHDRAWAL', duplicate_resolution=DuplicateResolution.UNRESOLVED)
        response = self.review_save(savings2, classification='INCOME')
        self.assertEqual(response.status_code, 200, response.content)
        savings2.refresh_from_db()
        self.assertEqual((savings2.savings_effect, savings2.subcategory_id, savings2.budget_bucket),
                         ('NONE', None, ''))

    def test_savings_requires_active_savings_category(self):
        archived_savings = Subcategory.objects.create(
            name='Archived synthetic savings', budget_bucket=BudgetBucket.SAVINGS, is_active=False,
        )
        for effect in ('CONTRIBUTION', 'WITHDRAWAL'):
            for subcategory in (None, self.needs.pk, self.gift.pk, self.archived.pk, archived_savings.pk):
                with self.subTest(effect=effect, subcategory=subcategory):
                    record = self.txn()
                    response = self.review_save(record, classification='TRANSFER',
                                                savings_effect=effect, subcategory=subcategory)
                    self.assertEqual(response.status_code, 400, response.content)
                    self.assertEqual(self.refreshed(record).classification, 'UNKNOWN')

    def test_create_and_assign_savings_category_with_effect(self):
        record = self.txn()
        response = self.client.post(
            f'/api/transactions/{record.pk}/create-subcategory-and-assign/',
            {'name': 'Synthetic goal', 'budget_bucket': 'SAVINGS', 'classification': 'TRANSFER',
             'savings_effect': 'CONTRIBUTION'}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        record.refresh_from_db()
        self.assertEqual((record.classification, record.savings_effect, record.subcategory_id),
                         ('TRANSFER', 'CONTRIBUTION', response.json()['subcategory']['id']))

    def test_new_ordinary_transfer_category_is_rejected_but_legacy_is_preserved(self):
        for category in (self.fund, self.gift):
            record = self.txn()
            response = self.review_save(record, classification='TRANSFER', subcategory=category.pk)
            self.assertEqual(response.status_code, 400, response.content)
        record = self.txn('EXAMPLE LEGACY', 'TRANSFER')
        Transaction.objects.filter(pk=record.pk).update(subcategory=self.fund, budget_bucket='SAVINGS')
        response = self.review_save(record, notes='Keep historical assignment')
        self.assertEqual(response.status_code, 200, response.content)
        record.refresh_from_db()
        self.assertEqual((record.subcategory_id, record.budget_bucket, record.review_status),
                         (self.fund.pk, 'SAVINGS', ReviewStatus.PENDING))
        response = self.review_save(record, subcategory=None)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(self.refreshed(record).subcategory)
        self.assertEqual(record.review_status, ReviewStatus.REVIEWED)

    def test_legacy_archived_savings_category_cannot_receive_new_activity(self):
        record = self.txn('EXAMPLE LEGACY SAVINGS', 'TRANSFER')
        Transaction.objects.filter(pk=record.pk).update(subcategory=self.fund, budget_bucket='SAVINGS')
        self.fund.is_active = False
        self.fund.save()
        response = self.review_save(record, savings_effect='CONTRIBUTION')
        self.assertEqual(response.status_code, 400, response.content)
        record.refresh_from_db()
        self.assertEqual((record.savings_effect, record.subcategory_id), ('NONE', self.fund.pk))

    def test_pending_manual_ordinary_transfer_cannot_be_reinterpreted_as_savings(self):
        record = self.txn('EXAMPLE TRANSFER', duplicate_resolution=DuplicateResolution.UNRESOLVED)
        self.assertEqual(self.review_save(record, classification='TRANSFER').status_code, 200)
        rule = self.make_rule('EXAMPLE TRANSFER', 'TRANSFER', self.fund.pk, 'CONTRIBUTION')
        record.refresh_from_db()
        self.assertEqual(evaluate_transaction(record, [rule])['rules'], [])
        self.assertEqual(self.client.post(f'{BASE}{rule.pk}/apply-to-pending/').status_code, 200)
        record.refresh_from_db()
        self.assertEqual((record.manually_resolved, record.savings_effect, record.subcategory_id,
                          record.review_status), (True, 'NONE', None, ReviewStatus.PENDING))

    def test_archived_savings_assignment_is_retained_only_for_unrelated_edits(self):
        record = self.txn('EXAMPLE HISTORICAL', 'TRANSFER', subcategory=self.fund,
                           savings_effect='CONTRIBUTION', duplicate_resolution=DuplicateResolution.UNRESOLVED)
        self.fund.is_active = False
        self.fund.save()
        for field, value in (('notes', 'Synthetic note'), ('merchant', 'Synthetic merchant')):
            response = self.review_save(record, **{field: value})
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(self.refreshed(record).subcategory_id, self.fund.pk)
        self.assertEqual(self.review_save(record, savings_effect='WITHDRAWAL').status_code, 400)
        other = self.txn('EXAMPLE NEW')
        self.assertEqual(self.review_save(other, classification='TRANSFER', savings_effect='CONTRIBUTION',
                                           subcategory=self.fund.pk).status_code, 400)
        self.assertEqual(self.review_save(record, savings_effect='WITHDRAWAL', subcategory=self.goal.pk).status_code, 200)
        self.assertEqual(self.refreshed(record).subcategory_id, self.goal.pk)

    def test_unknown_can_be_resolved_to_every_outcome(self):
        cases = (
            ('INCOME', 'NONE', None, '30.00'),
            ('EXCLUDED', 'NONE', None, '-1.00'),
            ('EXPENSE', 'NONE', self.needs.id, '-12.00'),
            ('REFUND', 'NONE', self.wants.id, '12.00'),
            ('OTHER_INFLOW', 'NONE', self.gift.id, '20.00'),
            ('TRANSFER', 'NONE', None, '-100.00'),
            ('TRANSFER', 'CONTRIBUTION', self.fund.id, '-100.00'),
            ('TRANSFER', 'WITHDRAWAL', self.fund.id, '100.00'),
        )
        for classification, effect, subcategory, amount in cases:
            with self.subTest(classification=classification, effect=effect):
                record = self.txn(f'MANUAL {classification} {effect}', amount=amount)
                response = self.review_save(
                    record, classification=classification, savings_effect=effect, subcategory=subcategory,
                )
                self.assertEqual(response.status_code, 200, response.content)
                body = response.json()
                self.assertEqual(body['id'], record.pk)
                record = self.refreshed(record)
                self.assertEqual(record.classification, classification)
                self.assertEqual(record.savings_effect, effect)
                self.assertEqual(record.subcategory_id, subcategory)
                self.assertEqual(record.review_status, ReviewStatus.REVIEWED)
                self.assertIsNone(record.categorized_by_rule)

    def test_expense_without_subcategory_is_not_a_resolution(self):
        record = self.txn()
        response = self.review_save(record, classification='EXPENSE')
        self.assertEqual(response.status_code, 200, response.content)
        record = self.refreshed(record)
        self.assertEqual(record.classification, 'EXPENSE')
        self.assertIsNone(record.subcategory)
        self.assertEqual(record.review_status, ReviewStatus.PENDING)
        self.assertFalse(record.manually_resolved)
        confirm = self.review_save(record, confirm_financial_outcome=True)
        self.assertEqual(confirm.status_code, 400, confirm.content)
        self.assertEqual(self.refreshed(record).review_status, ReviewStatus.PENDING)

    def test_unknown_save_may_succeed_and_remains_pending(self):
        record = self.txn()
        response = self.review_save(record, classification='UNKNOWN', notes='still unsure')
        self.assertEqual(response.status_code, 200, response.content)
        record = self.refreshed(record)
        self.assertEqual(record.classification, 'UNKNOWN')
        self.assertEqual(record.review_status, ReviewStatus.PENDING)
        self.assertFalse(record.manually_resolved)
        self.assertEqual(self.review_save(record, confirm_financial_outcome=True).status_code, 400)

    def test_review_save_is_restricted_to_pending_rows(self):
        record = self.txn('DONE 1', 'EXPENSE', subcategory=self.needs)
        self.assertEqual(record.review_status, ReviewStatus.NOT_REQUIRED)
        response = self.review_save(record, classification='EXPENSE', subcategory=self.wants.id)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(self.refreshed(record).subcategory, self.needs)

    def test_invalid_combinations_are_rejected_without_partial_changes(self):
        invalid = (
            {'classification': 'EXPENSE', 'subcategory': self.fund.id},
            {'classification': 'INCOME', 'savings_effect': 'CONTRIBUTION'},
            {'classification': 'TRANSFER', 'savings_effect': 'CONTRIBUTION', 'subcategory': self.wants.id},
            {'classification': 'EXPENSE', 'subcategory': self.archived.id},
        )
        for payload in invalid:
            with self.subTest(payload):
                record = self.txn()
                response = self.review_save(record, notes='should not persist', **payload)
                self.assertEqual(response.status_code, 400, response.content)
                record = self.refreshed(record)
                self.assertEqual(record.classification, 'UNKNOWN')
                self.assertEqual(record.notes, '')
                self.assertEqual(record.review_status, ReviewStatus.PENDING)

    def test_note_and_merchant_only_saves_do_not_review_or_resolve(self):
        record = self.txn()
        response = self.review_save(record, notes='looked at it', merchant='Acme Widgets')
        self.assertEqual(response.status_code, 200, response.content)
        record = self.refreshed(record)
        self.assertEqual((record.notes, record.merchant), ('looked at it', 'Acme Widgets'))
        self.assertEqual(record.classification, 'UNKNOWN')
        self.assertEqual(record.review_status, ReviewStatus.PENDING)

        pending_transfer = self.txn('XFER', 'TRANSFER')
        self.assertEqual(pending_transfer.review_status, ReviewStatus.PENDING)
        self.review_save(pending_transfer, notes='note only')
        self.assertEqual(self.refreshed(pending_transfer).review_status, ReviewStatus.PENDING)

    def test_unchanged_financial_values_are_a_no_op_for_review(self):
        record = self.txn('XFER', 'TRANSFER')
        response = self.review_save(record, classification='TRANSFER', savings_effect='NONE', merchant='Bank')
        self.assertEqual(response.status_code, 200, response.content)
        record = self.refreshed(record)
        self.assertEqual(record.review_status, ReviewStatus.PENDING)
        self.assertEqual(record.merchant, 'Bank')

    def test_confirming_unchanged_pending_transfer_requires_explicit_confirmation(self):
        record = self.txn('XFER 2', 'TRANSFER')
        self.review_save(record, classification='TRANSFER', savings_effect='NONE')
        self.assertEqual(self.refreshed(record).review_status, ReviewStatus.PENDING)
        confirmed = self.review_save(record, confirm_financial_outcome=True)
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        record = self.refreshed(record)
        self.assertEqual(record.review_status, ReviewStatus.REVIEWED)
        self.assertTrue(record.manually_resolved)
        self.assertEqual(self.review_save(record, confirm_financial_outcome=True).status_code, 400)

    def test_confirm_financial_outcome_must_be_boolean(self):
        record = self.txn('XFER 3', 'TRANSFER')
        self.assertEqual(self.review_save(record, confirm_financial_outcome='yes').status_code, 400)
        self.assertEqual(self.refreshed(record).review_status, ReviewStatus.PENDING)

    def test_manually_resolved_pending_transfer_is_protected_from_savings_rules(self):
        record = self.txn('AUTO SAVE 1', 'TRANSFER')
        Transaction.objects.filter(pk=record.pk).update(manually_resolved=True)
        rule = self.make_rule('AUTO SAVE', 'TRANSFER', self.fund.id, 'CONTRIBUTION', match_type='STARTS_WITH')
        response = self.client.post(f'{BASE}{rule.pk}/apply-to-pending/')
        self.assertEqual(response.status_code, 200)
        record = self.refreshed(record)
        self.assertEqual(record.savings_effect, SavingsEffect.NONE)
        self.assertIsNone(record.subcategory)
        self.assertIsNone(record.categorized_by_rule)

    def test_financial_change_clears_rule_provenance_on_a_pending_duplicate(self):
        rule = self.make_rule('ACME')
        original = self.txn('ACME 0', 'EXPENSE', subcategory=self.needs)
        record = self.txn(
            'ACME 1', 'EXPENSE', subcategory=self.needs, categorized_by_rule=rule,
            duplicate_resolution=DuplicateResolution.UNRESOLVED,
        )
        record.duplicate_candidates.add(original)
        self.assertEqual(record.review_status, ReviewStatus.PENDING)
        self.assertEqual(self.refreshed(record).categorized_by_rule, rule)
        response = self.review_save(record, classification='EXPENSE', subcategory=self.wants.id)
        self.assertEqual(response.status_code, 200, response.content)
        record = self.refreshed(record)
        self.assertEqual(record.subcategory, self.wants)
        self.assertIsNone(record.categorized_by_rule)
        self.assertTrue(record.manually_resolved)

    def test_unresolved_duplicate_still_blocks_review(self):
        original = self.txn('DUP 1')
        duplicate = self.txn('DUP 1', duplicate_resolution=DuplicateResolution.UNRESOLVED)
        duplicate.duplicate_candidates.add(original)
        response = self.review_save(duplicate, classification='EXPENSE', subcategory=self.needs.id)
        record = self.refreshed(duplicate)
        self.assertEqual(record.review_status, ReviewStatus.PENDING)
        if response.status_code == 200:
            self.assertEqual(record.duplicate_resolution, DuplicateResolution.UNRESOLVED)
        else:
            self.assertEqual(response.status_code, 400)

    def conflicted(self, description='CONF 1'):
        record = self.txn(description, 'EXPENSE', subcategory=self.needs, categorization_rule_conflict=True)
        self.assertEqual(record.review_status, ReviewStatus.PENDING)
        return record

    def test_note_only_save_does_not_resolve_a_rule_conflict(self):
        record = self.conflicted()
        response = self.review_save(record, notes='only a note')
        self.assertEqual(response.status_code, 200, response.content)
        record = self.refreshed(record)
        self.assertTrue(record.categorization_rule_conflict)
        self.assertEqual(record.review_status, ReviewStatus.PENDING)

    def test_financial_change_resolves_conflict_without_checkbox(self):
        record = self.conflicted()
        response = self.review_save(record, classification='EXPENSE', subcategory=self.wants.id)
        self.assertEqual(response.status_code, 200, response.content)
        record = self.refreshed(record)
        self.assertFalse(record.categorization_rule_conflict)
        self.assertEqual(record.review_status, ReviewStatus.REVIEWED)
        self.assertIsNone(record.categorized_by_rule)

    def test_unchanged_financial_state_needs_explicit_conflict_resolution(self):
        record = self.conflicted()
        refused = self.review_save(record, confirm_financial_outcome=True)
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertTrue(self.refreshed(record).categorization_rule_conflict)
        resolved = self.review_save(
            record, classification='EXPENSE', subcategory=self.needs.id, resolve_rule_conflict=True,
        )
        self.assertEqual(resolved.status_code, 200, resolved.content)
        record = self.refreshed(record)
        self.assertFalse(record.categorization_rule_conflict)
        self.assertEqual(record.review_status, ReviewStatus.REVIEWED)

    def test_resolve_rule_conflict_requires_an_existing_conflict_and_boolean(self):
        pending_transfer = self.txn('NOCONF 2', 'TRANSFER')
        self.assertEqual(self.review_save(pending_transfer, resolve_rule_conflict=True).status_code, 400)
        self.assertEqual(self.review_save(pending_transfer, resolve_rule_conflict='true').status_code, 400)

    def test_known_transactions_are_not_pushed_back_into_review_by_saves(self):
        record = self.txn('KNOWN 1', 'EXPENSE', subcategory=self.needs)
        self.assertEqual(record.review_status, ReviewStatus.NOT_REQUIRED)
        self.review_save(record, notes='annotated')
        self.assertEqual(self.refreshed(record).review_status, ReviewStatus.NOT_REQUIRED)

    def test_bulk_financial_changes_reconcile_conflicts_without_reviewing(self):
        income = self.txn('BULK INCOME', categorization_rule_conflict=True,
                          duplicate_resolution=DuplicateResolution.UNRESOLVED)
        incomplete = self.txn('BULK INCOMPLETE', categorization_rule_conflict=True)
        expense = self.txn('BULK EXPENSE', 'EXPENSE', categorization_rule_conflict=True)
        for record, payload in (
            (income, {'operation': 'classification', 'classification': 'INCOME'}),
            (incomplete, {'operation': 'classification', 'classification': 'EXPENSE'}),
            (expense, {'operation': 'subcategory', 'subcategory': self.needs.pk}),
        ):
            response = self.client.post('/api/transactions/bulk/', {
                **payload, 'transaction_ids': [record.pk],
            }, format='json')
            self.assertEqual(response.status_code, 200, response.content)
        income.refresh_from_db()
        incomplete.refresh_from_db()
        expense.refresh_from_db()
        self.assertEqual(income.review_blockers(), ['UNRESOLVED_DUPLICATE'])
        self.assertTrue(income.manually_resolved)
        self.assertEqual(incomplete.review_blockers(), ['RULE_CONFLICT', 'SUBCATEGORY_REQUIRED'])
        self.assertFalse(incomplete.manually_resolved)
        self.assertFalse(expense.categorization_rule_conflict)
        self.assertTrue(expense.manually_resolved)
        self.assertTrue(all(record.review_status == ReviewStatus.PENDING for record in (income, incomplete, expense)))

    def test_inline_category_assignment_clears_complete_financial_conflict(self):
        record = self.conflicted()
        response = self.client.post(f'/api/transactions/{record.pk}/create-subcategory-and-assign/', {
            'name': 'Synthetic inline', 'budget_bucket': 'NEEDS', 'classification': 'EXPENSE',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertFalse(response.json()['transaction']['categorization_rule_conflict'])
        self.assertEqual(self.review_save(record, notes='Synthetic note').status_code, 200)


class SaveWithRuleTests(ResolutionTestCase):
    def save_with_rule(self, record, **payload):
        return self.client.post(f'/api/transactions/{record.pk}/save-with-rule/', payload, format='json')

    def test_saves_transaction_and_creates_rule_for_non_expense_outcomes(self):
        cases = (
            ('INCOME', 'NONE', None, 'CONTAINS'),
            ('EXCLUDED', 'NONE', None, 'STARTS_WITH'),
            ('REFUND', 'NONE', self.wants.id, 'CONTAINS'),
            ('OTHER_INFLOW', 'NONE', self.gift.id, 'CONTAINS'),
            ('TRANSFER', 'CONTRIBUTION', self.fund.id, 'STARTS_WITH'),
            ('TRANSFER', 'WITHDRAWAL', self.fund.id, 'CONTAINS'),
        )
        for number, (classification, effect, subcategory, match_type) in enumerate(cases):
            with self.subTest(classification=classification, effect=effect):
                value = f'RULE SAVE {number}'
                other = self.txn(f'{value} second')
                record = self.txn(f'{value} first', amount='15.00')
                response = self.save_with_rule(
                    record, classification=classification, savings_effect=effect,
                    subcategory=subcategory, match_type=match_type, match_value=value,
                )
                self.assertEqual(response.status_code, 201, response.content)
                body = response.json()
                self.assertEqual(body['transaction']['classification'], classification)
                self.assertEqual(body['rule']['classification'], classification)
                self.assertEqual(body['rule']['savings_effect'], effect)
                self.assertEqual(body['rule']['subcategory'], subcategory)
                self.assertEqual(body['rule']['match_type'], match_type)
                self.assertIn('matching_pending_count', body)
                self.assertEqual(body['matching_pending_count'], 1)
                self.assertEqual(body['conflicting_pending_count'], 0)
                record = self.refreshed(record)
                self.assertEqual(record.review_status, ReviewStatus.REVIEWED)
                self.assertIsNone(record.categorized_by_rule)
                self.assertEqual(self.refreshed(other).classification, 'UNKNOWN')

    def test_failed_rule_rolls_back_the_transaction_save(self):
        self.make_rule('TAKEN')
        record = self.txn('TAKEN 1')
        response = self.save_with_rule(
            record, classification='EXPENSE', subcategory=self.needs.id, match_value='TAKEN',
        )
        self.assertEqual(response.status_code, 400, response.content)
        record = self.refreshed(record)
        self.assertEqual(record.classification, 'UNKNOWN')
        self.assertEqual(CategorizationRule.objects.count(), 1)

    def test_duplicate_blocker_does_not_leave_partial_state_for_other_outcomes(self):
        self.make_rule('BLOCKED', 'INCOME', None)
        record = self.txn('BLOCKED 1', amount='9.00')
        response = self.save_with_rule(
            record, classification='REFUND', subcategory=self.wants.id, match_value='BLOCKED',
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(self.refreshed(record).classification, 'UNKNOWN')
        self.assertEqual(CategorizationRule.objects.count(), 1)

    def test_invalid_match_type_rolls_back(self):
        record = self.txn('MT 1')
        response = self.save_with_rule(
            record, classification='EXPENSE', subcategory=self.needs.id,
            match_type='EQUALS', match_value='MT',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.refreshed(record).classification, 'UNKNOWN')
        self.assertEqual(CategorizationRule.objects.count(), 0)

    def test_resolve_rule_conflict_is_accepted_and_clears_conflict(self):
        record = self.txn('CONF 3', 'EXPENSE', subcategory=self.needs, categorization_rule_conflict=True)
        response = self.save_with_rule(
            record, classification='EXPENSE', subcategory=self.needs.id,
            resolve_rule_conflict=True, match_value='CONF 3',
        )
        self.assertEqual(response.status_code, 201, response.content)
        record = self.refreshed(record)
        self.assertFalse(record.categorization_rule_conflict)
        self.assertEqual(record.review_status, ReviewStatus.REVIEWED)


class ReportParityTests(ResolutionTestCase):
    PERIODS = (
        ('/api/reports/all-time/', 'scope=all'),
        ('/api/reports/yearly/?year=2026', 'scope=year&year=2026'),
        ('/api/reports/monthly/?year=2026&month=3', 'scope=month&year=2026&month=3'),
        (
            '/api/reports/range/?start_date=2026-03-01&end_date=2026-03-31',
            'scope=range&start_date=2026-03-01&end_date=2026-03-31',
        ),
    )

    def drill_ids(self, query, metric):
        response = self.client.get(f'/api/reports/drilldown/?{query}&metric={metric}')
        self.assertEqual(response.status_code, 200, response.content)
        return {row['id'] for row in response.json()['results']}

    def test_manual_and_rule_ordinary_transfers_do_not_affect_reports(self):
        manual = self.txn('EXAMPLE MANUAL TRANSFER', amount='100.00')
        automatic = self.txn('EXAMPLE AUTO TRANSFER', amount='100.00')
        rule = self.make_rule('EXAMPLE AUTO TRANSFER', 'TRANSFER', None, 'NONE')
        self.assertEqual(self.review_save(manual, classification='TRANSFER').status_code, 200)
        self.assertEqual(self.client.post(f'{BASE}{rule.pk}/apply-to-pending/').status_code, 200)
        automatic.refresh_from_db()
        self.assertEqual((automatic.review_status, automatic.savings_effect, automatic.subcategory_id),
                         (ReviewStatus.NOT_REQUIRED, 'NONE', None))
        metrics = ('income', 'other_inflows', 'needs', 'wants', 'savings_contributions',
                   'savings_withdrawals', 'remaining')
        for url, query in self.PERIODS:
            with self.subTest(url=url):
                summary = self.client.get(url).json()['summary']
                for metric in metrics:
                    self.assertEqual(Decimal(summary[metric]['amount']), Decimal('0.00'), metric)
                    self.assertNotIn(manual.pk, self.drill_ids(query, metric))
                    self.assertNotIn(automatic.pk, self.drill_ids(query, metric))

    def test_resolved_outcomes_are_reported_consistently_with_drilldowns(self):
        income = self.txn('PAYROLL', amount='1000.00')
        spend = self.txn('ACME 1', amount='-100.00')
        saved = self.txn('AUTO SAVE', amount='-200.00')
        bad_deposit = self.txn('MYSTERY', amount='50.00')
        self.assertEqual(self.client.get('/api/reports/monthly/?year=2026&month=3').json()
                         ['data_completeness']['affected_transaction_count'], 4)

        self.assertEqual(self.review_save(income, classification='INCOME').status_code, 200)
        self.assertEqual(self.review_save(
            spend, classification='EXPENSE', subcategory=self.needs.id).status_code, 200)
        self.assertEqual(self.review_save(
            saved, classification='TRANSFER', savings_effect='CONTRIBUTION',
            subcategory=self.fund.id).status_code, 200)
        self.assertEqual(self.review_save(bad_deposit, classification='EXCLUDED').status_code, 200)

        for url, query in self.PERIODS:
            with self.subTest(url):
                report = self.client.get(url).json()
                self.assertEqual(report['data_completeness']['affected_transaction_count'], 0)
                summary = report.get('summary', report)
                self.assertEqual(Decimal(str(summary['income']['amount'])), Decimal('1000.00'))
                self.assertEqual(Decimal(str(summary['needs']['amount'])), Decimal('100.00'))
                self.assertEqual(Decimal(str(summary['savings_contributions']['amount'])), Decimal('200.00'))
                self.assertEqual(Decimal(str(summary['remaining']['amount'])), Decimal('700.00'))
                self.assertEqual(self.drill_ids(query, 'income'), {income.pk})
                self.assertEqual(self.drill_ids(query, 'needs'), {spend.pk})
                self.assertEqual(self.drill_ids(query, 'savings_contributions'), {saved.pk})

    def test_other_months_are_reported_by_transaction_date_across_periods(self):
        march = self.txn('ACME 1', 'EXPENSE', amount='-40.00', subcategory=self.needs)
        april = self.txn('ACME 2', 'UNKNOWN', amount='-60.00', month=4)
        self.review_save(april, classification='EXPENSE', subcategory=self.needs.id)

        def needs(url):
            body = self.client.get(url).json()
            return Decimal(str(body.get('summary', body)['needs']['amount']))

        self.assertEqual(needs('/api/reports/monthly/?year=2026&month=3'), Decimal('40.00'))
        self.assertEqual(needs('/api/reports/monthly/?year=2026&month=4'), Decimal('60.00'))
        self.assertEqual(needs('/api/reports/yearly/?year=2026'), Decimal('100.00'))
        self.assertEqual(needs('/api/reports/all-time/'), Decimal('100.00'))
        self.assertEqual(self.drill_ids('scope=month&year=2026&month=4', 'needs'), {april.pk})
        self.assertEqual(self.drill_ids('scope=all', 'needs'), {march.pk, april.pk})

    def test_unresolved_rule_conflict_and_unknown_are_counted_as_incomplete_in_every_period(self):
        self.txn('ACME 1')
        self.txn('ACME 2', 'EXPENSE', subcategory=self.needs, categorization_rule_conflict=True)
        for url, _ in self.PERIODS:
            with self.subTest(url):
                completeness = self.client.get(url).json()['data_completeness']
                self.assertEqual(completeness['affected_transaction_count'], 2)


class ImportResolutionTests(ResolutionTestCase):
    HEADER = b'Date,Description,Type,Amount,Status\n'

    def csv(self, *rows):
        lines = [f'{day},{desc},{kind},{amount},Posted' for day, desc, kind, amount in rows]
        return self.HEADER + '\n'.join(lines).encode() + b'\n'

    def post(self, url, content, name='synthetic.csv'):
        return self.client.post(
            url,
            {'account': self.account.id, 'file': SimpleUploadedFile(name, content, content_type='text/csv')},
            format='multipart',
        )

    def preview(self, content):
        response = self.post('/api/imports/preview/', content)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def confirm(self, content, name='synthetic.csv'):
        response = self.post('/api/imports/', content, name)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def imported(self, description):
        return Transaction.objects.get(raw_description=description)

    OUTCOME_ROWS = (
        # description, source type, amount, rule outcome kwargs, expected (classification, effect, subcategory attr, bucket)
        ('IMP INCOME 1', 'DEPOSIT', '500.00', ('INCOME', None, 'NONE'), ('INCOME', 'NONE', None, '')),
        ('IMP EXPENSE 1', 'ROUNDUP', '-7.00', ('EXPENSE', 'needs', 'NONE'), ('EXPENSE', 'NONE', 'needs', 'NEEDS')),
        ('IMP REFUND 1', 'DEPOSIT', '12.00', ('REFUND', 'wants', 'NONE'), ('REFUND', 'NONE', 'wants', 'WANTS')),
        ('IMP OTHER 1', 'DEPOSIT', '30.00', ('OTHER_INFLOW', 'gift', 'NONE'), ('OTHER_INFLOW', 'NONE', 'gift', '')),
        ('IMP OTHER 2', 'DEPOSIT', '31.00', ('OTHER_INFLOW', None, 'NONE'), ('OTHER_INFLOW', 'NONE', None, '')),
        ('IMP TRANSFER 1', 'WITHDRAWAL', '-60.00', ('TRANSFER', None, 'NONE'), ('TRANSFER', 'NONE', None, '')),
        ('IMP SAVE IN 1', 'WITHDRAWAL', '-70.00', ('TRANSFER', 'fund', 'CONTRIBUTION'),
         ('TRANSFER', 'CONTRIBUTION', 'fund', 'SAVINGS')),
        ('IMP SAVE OUT 1', 'DEPOSIT', '80.00', ('TRANSFER', 'fund', 'WITHDRAWAL'),
         ('TRANSFER', 'WITHDRAWAL', 'fund', 'SAVINGS')),
        ('IMP EXCLUDED 1', 'WITHDRAWAL', '-1.00', ('EXCLUDED', None, 'NONE'), ('EXCLUDED', 'NONE', None, '')),
    )

    def create_outcome_rules(self):
        rules = {}
        for description, _, _, (classification, target, effect), _ in self.OUTCOME_ROWS:
            subcategory = getattr(self, target).id if target else None
            rules[description] = self.make_rule(
                description, classification, subcategory, effect, match_type='STARTS_WITH',
            )
        return rules

    def outcome_csv(self):
        return self.csv(*(
            ('2026-03-10', description, kind, amount)
            for description, kind, amount, _, _ in self.OUTCOME_ROWS
        ))

    def test_preview_and_confirm_agree_for_every_supported_outcome(self):
        rules = self.create_outcome_rules()
        content = self.outcome_csv()
        preview = self.preview(content)
        self.assertEqual(Transaction.objects.count(), 0)
        for row, (description, _, _, outcome, _) in zip(preview['valid_rows'], self.OUTCOME_ROWS):
            with self.subTest(description):
                categorization = row['categorization']
                self.assertEqual(categorization['status'], 'AUTO_RESOLVED')
                self.assertEqual(categorization['classification'], outcome[0])
                self.assertEqual(categorization['savings_effect'], outcome[2])
                self.assertEqual(categorization['rules'][0]['id'], rules[description].id)
                if outcome[1]:
                    self.assertEqual(categorization['subcategory']['id'], getattr(self, outcome[1]).id)
                else:
                    self.assertIsNone(categorization['subcategory'])
        self.assertEqual(preview['review_required_row_count'], 0)

        self.confirm(content)
        for description, _, _, _, (classification, effect, target, bucket) in self.OUTCOME_ROWS:
            with self.subTest(description):
                record = self.imported(description)
                self.assertEqual(record.classification, classification)
                self.assertEqual(record.savings_effect, effect)
                self.assertEqual(record.subcategory, getattr(self, target) if target else None)
                self.assertEqual(record.budget_bucket, bucket)
                self.assertEqual(record.categorized_by_rule, rules[description])
                self.assertFalse(record.categorization_rule_conflict)
                self.assertEqual(record.duplicate_resolution, DuplicateResolution.NONE)
                self.assertEqual(record.raw_description, description)
                self.assertEqual(record.review_blockers(), [])
                self.assertEqual(record.review_status, ReviewStatus.NOT_REQUIRED)

    def test_confirmed_import_reports_match_effective_outcomes(self):
        self.create_outcome_rules()
        self.confirm(self.outcome_csv())
        for url in ('/api/reports/all-time/', '/api/reports/monthly/?year=2026&month=3'):
            with self.subTest(url):
                report = self.client.get(url).json()
                self.assertEqual(report['data_completeness']['affected_transaction_count'], 0)
                summary = report.get('summary', report)
                self.assertEqual(Decimal(str(summary['income']['amount'])), Decimal('500.00'))
                self.assertEqual(Decimal(str(summary['needs']['amount'])), Decimal('7.00'))
                self.assertEqual(Decimal(str(summary['savings_contributions']['amount'])), Decimal('70.00'))
                self.assertEqual(Decimal(str(summary['savings_withdrawals']['amount'])), Decimal('80.00'))

    def test_unmatched_rows_are_counted_for_review_and_imported_pending(self):
        content = self.csv(
            ('2026-03-10', 'NOMATCH PURCHASE', 'DEBIT_CARD', '-5.00'),
            ('2026-03-10', 'NOMATCH ROUNDUP', 'ROUNDUP', '-1.00'),
            ('2026-03-10', 'NOMATCH INTEREST', 'INTEREST_EARNED', '0.10'),
        )
        preview = self.preview(content)
        self.assertEqual(preview['review_required_row_count'], 2)
        self.confirm(content)
        self.assertEqual(self.imported('NOMATCH PURCHASE').review_status, ReviewStatus.PENDING)
        self.assertEqual(self.imported('NOMATCH ROUNDUP').review_status, ReviewStatus.PENDING)
        self.assertEqual(self.imported('NOMATCH INTEREST').review_status, ReviewStatus.NOT_REQUIRED)
        self.assertEqual(Transaction.objects.filter(review_status=ReviewStatus.PENDING).count(), 2)

    def test_expense_rule_scopes_apply_to_imported_unknown_and_expense_rows(self):
        self.make_rule('SCOPE ', 'EXPENSE', self.needs.id, allow_unknown=True, match_type='STARTS_WITH')
        self.make_rule('STRICT ', 'EXPENSE', self.wants.id, allow_unknown=False, match_type='STARTS_WITH')
        content = self.csv(
            ('2026-03-10', 'SCOPE UNKNOWN', 'ROUNDUP', '-2.00'),
            ('2026-03-10', 'SCOPE CARD', 'DEBIT_CARD', '-3.00'),
            ('2026-03-10', 'STRICT UNKNOWN', 'ROUNDUP', '-4.00'),
            ('2026-03-10', 'STRICT CARD', 'DEBIT_CARD', '-5.00'),
        )
        statuses = {
            row['raw_description']: row['categorization']['status']
            for row in self.preview(content)['valid_rows']
        }
        self.assertEqual(statuses, {
            'SCOPE UNKNOWN': 'AUTO_RESOLVED', 'SCOPE CARD': 'AUTO_RESOLVED',
            'STRICT UNKNOWN': 'NOT_CATEGORIZED', 'STRICT CARD': 'AUTO_RESOLVED',
        })
        self.confirm(content)
        self.assertEqual(self.imported('SCOPE UNKNOWN').classification, 'EXPENSE')
        self.assertEqual(self.imported('SCOPE UNKNOWN').subcategory, self.needs)
        self.assertEqual(self.imported('STRICT UNKNOWN').classification, 'UNKNOWN')
        self.assertIsNone(self.imported('STRICT UNKNOWN').subcategory)
        self.assertEqual(self.imported('STRICT UNKNOWN').budget_bucket, '')
        self.assertEqual(self.imported('STRICT CARD').subcategory, self.wants)

    def test_rules_changed_between_preview_and_confirm_are_recomputed(self):
        content = self.csv(('2026-03-10', 'LATE RULE 1', 'DEPOSIT', '20.00'))
        before = self.preview(content)
        self.assertEqual(before['review_required_row_count'], 1)
        self.assertEqual(before['valid_rows'][0]['categorization']['status'], 'NOT_CATEGORIZED')

        rule = self.make_rule('LATE RULE', 'INCOME', None)
        after = self.preview(content)
        self.assertEqual(after['valid_rows'][0]['categorization']['status'], 'AUTO_RESOLVED')
        self.confirm(content)
        record = self.imported('LATE RULE 1')
        self.assertEqual(record.classification, 'INCOME')
        self.assertEqual(record.categorized_by_rule, rule)

    def test_rule_deactivated_between_preview_and_confirm_is_not_applied(self):
        rule = self.make_rule('GONE RULE', 'INCOME', None)
        content = self.csv(('2026-03-10', 'GONE RULE 1', 'DEPOSIT', '20.00'))
        self.assertEqual(self.preview(content)['valid_rows'][0]['categorization']['status'], 'AUTO_RESOLVED')
        self.client.patch(f'{BASE}{rule.pk}/', {'is_active': False}, format='json')
        self.confirm(content)
        record = self.imported('GONE RULE 1')
        self.assertEqual(record.classification, 'UNKNOWN')
        self.assertIsNone(record.categorized_by_rule)
        self.assertEqual(record.review_status, ReviewStatus.PENDING)

    def test_conflicting_outcomes_are_previewed_and_imported_pending_with_no_assignment(self):
        self.make_rule('CLASH', 'INCOME', None)
        self.make_rule('CLASH ONE', 'EXCLUDED', None, match_type='STARTS_WITH')
        self.make_rule('CLASH', 'TRANSFER', self.fund.id, 'CONTRIBUTION')
        content = self.csv(('2026-03-10', 'CLASH ONE', 'DEPOSIT', '20.00'))
        preview = self.preview(content)
        categorization = preview['valid_rows'][0]['categorization']
        self.assertEqual(categorization['status'], 'RULE_CONFLICT')
        self.assertEqual(len(categorization['rules']), 3)
        self.assertEqual(preview['review_required_row_count'], 1)
        self.confirm(content)
        record = self.imported('CLASH ONE')
        self.assertEqual(record.classification, 'UNKNOWN')
        self.assertIsNone(record.subcategory)
        self.assertEqual(record.budget_bucket, '')
        self.assertEqual(record.savings_effect, SavingsEffect.NONE)
        self.assertIsNone(record.categorized_by_rule)
        self.assertTrue(record.categorization_rule_conflict)
        self.assertEqual(record.review_status, ReviewStatus.PENDING)

    def test_identical_outcomes_from_overlapping_rules_import_without_conflict(self):
        self.make_rule('SAME', 'INCOME', None)
        self.make_rule('SAME OUT', 'INCOME', None, match_type='STARTS_WITH')
        content = self.csv(('2026-03-10', 'SAME OUT 1', 'DEPOSIT', '20.00'))
        self.assertEqual(self.preview(content)['valid_rows'][0]['categorization']['status'], 'AUTO_RESOLVED')
        self.confirm(content)
        record = self.imported('SAME OUT 1')
        self.assertFalse(record.categorization_rule_conflict)
        self.assertEqual(record.classification, 'INCOME')

    def test_possible_duplicate_may_be_rule_resolved_but_stays_pending_and_counted(self):
        self.confirm(self.csv(('2026-03-10', 'DUP SHOP original', 'DEBIT_CARD', '-9.00')), 'first.csv')
        self.make_rule('DUP SHOP', 'EXPENSE', self.needs.id)
        content = self.csv(('2026-03-10', 'DUP SHOP changed', 'DEBIT_CARD', '-9.00'))
        preview = self.preview(content)
        row = preview['valid_rows'][0]
        self.assertEqual(row['overlap_status'], 'POSSIBLE_DUPLICATE')
        self.assertEqual(row['categorization']['status'], 'AUTO_RESOLVED')
        self.assertEqual(preview['possible_duplicate_row_count'], 1)
        self.assertEqual(preview['review_required_row_count'], 1)
        self.confirm(content, 'second.csv')
        record = self.imported('DUP SHOP changed')
        self.assertEqual(record.subcategory, self.needs)
        self.assertEqual(record.duplicate_resolution, DuplicateResolution.UNRESOLVED)
        self.assertEqual(record.review_status, ReviewStatus.PENDING)
        self.assertEqual(record.categorized_by_rule.match_value, 'DUP SHOP')

    def test_already_represented_rows_are_not_imported_or_counted_for_review(self):
        content = self.csv(('2026-03-10', 'REPEAT 1', 'ROUNDUP', '-3.00'))
        self.confirm(content, 'first.csv')
        bigger = content + b'2026-03-11,REPEAT 2,DEPOSIT,4.00,Posted\n'
        preview = self.preview(bigger)
        self.assertEqual(preview['already_represented_row_count'], 1)
        self.assertEqual(preview['review_required_row_count'], 1)
        self.confirm(bigger, 'second.csv')
        self.assertEqual(Transaction.objects.filter(raw_description='REPEAT 1').count(), 1)

    def test_rules_never_apply_outcomes_to_other_classifications_on_import(self):
        self.make_rule('PAYROLL', 'TRANSFER', self.fund.id, 'CONTRIBUTION')
        content = self.csv(('2026-03-10', 'PAYROLL 1', 'INTEREST_EARNED', '1.00'))
        preview = self.preview(content)
        # A known INCOME row is not eligible for a savings-transfer rule.
        self.assertEqual(preview['valid_rows'][0]['categorization']['status'], 'NOT_CATEGORIZED')
        self.confirm(content)
        record = self.imported('PAYROLL 1')
        self.assertEqual(record.classification, 'INCOME')
        self.assertEqual(record.savings_effect, SavingsEffect.NONE)
        self.assertIsNone(record.subcategory)
        self.assertEqual(record.budget_bucket, '')

    def test_null_subcategory_outcomes_never_store_a_bucket_snapshot(self):
        for number, classification in enumerate(('INCOME', 'EXCLUDED', 'OTHER_INFLOW', 'TRANSFER')):
            self.make_rule(f'NULLSUB {classification}', classification, None, match_type='STARTS_WITH')
        content = self.csv(*(
            ('2026-03-10', f'NULLSUB {classification} 1', 'DEPOSIT', '5.00')
            for classification in ('INCOME', 'EXCLUDED', 'OTHER_INFLOW', 'TRANSFER')
        ))
        self.confirm(content)
        for record in Transaction.objects.all():
            with self.subTest(record.raw_description):
                self.assertIsNone(record.subcategory)
                self.assertEqual(record.budget_bucket, '')
                self.assertEqual(record.savings_effect, SavingsEffect.NONE)

    def test_snapshot_survives_later_subcategory_bucket_change(self):
        self.make_rule('SNAP', 'EXPENSE', self.needs.id, match_type='STARTS_WITH')
        self.confirm(self.csv(('2026-03-10', 'SNAP 1', 'ROUNDUP', '-5.00')))
        Subcategory.objects.filter(pk=self.needs.pk).update(budget_bucket=BudgetBucket.WANTS)
        record = self.imported('SNAP 1')
        self.assertEqual(record.budget_bucket, BudgetBucket.NEEDS)
        report = self.client.get('/api/reports/monthly/?year=2026&month=3').json()
        self.assertEqual(Decimal(str(report.get('summary', report)['needs']['amount'])), Decimal('5.00'))
