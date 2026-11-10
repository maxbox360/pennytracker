"""Seed ONLY a disposable, empty database for review-browser.mjs."""
import os
from datetime import date
from decimal import Decimal

from finance.categorization_rules import apply_rule_to_transaction
from finance.models import Account, CategorizationRule, Subcategory, Transaction

if os.environ.get('PENNYTRACKER_BROWSER_FIXTURES') != '1' or any(
    model.objects.exists() for model in (Account, Subcategory, Transaction, CategorizationRule)
):
    raise RuntimeError('Browser fixtures require explicit opt-in and an empty disposable database.')

account = Account.objects.create(name='Synthetic account')
needs = Subcategory.objects.create(name='Synthetic needs', budget_bucket='NEEDS')
fund = Subcategory.objects.create(name='Synthetic savings', budget_bucket='SAVINGS')
archived = Subcategory.objects.create(name='Synthetic archived', budget_bucket='SAVINGS')


def transaction(name, classification='UNKNOWN', **kwargs):
    return Transaction.objects.create(
        account=account, date=date(2026, 3, 5), raw_description=name,
        amount=Decimal('-10.00'), classification=classification, **kwargs,
    )


ordinary = CategorizationRule.objects.create(match_value='EXAMPLE MOVE', classification='TRANSFER')
rule_transfer = transaction('EXAMPLE MOVE 123', duplicate_resolution='UNRESOLVED')
apply_rule_to_transaction(rule_transfer, ordinary)
transaction('EXAMPLE MOVE MANUAL', classification='TRANSFER',
            duplicate_resolution='UNRESOLVED', manually_resolved=True)
CategorizationRule.objects.create(match_value='EXAMPLE MOVE', match_type='STARTS_WITH',
                                  classification='TRANSFER', savings_effect='CONTRIBUTION',
                                  subcategory=fund)
transaction('EXAMPLE BULK CLASS', categorization_rule_conflict=True, duplicate_resolution='UNRESOLVED')
transaction('EXAMPLE BULK CATEGORY', classification='EXPENSE', categorization_rule_conflict=True)
transaction('EXAMPLE INLINE', classification='EXPENSE', subcategory=needs, categorization_rule_conflict=True)
transaction('EXAMPLE REMEMBER')
reviewed = transaction('EXAMPLE REVIEWED', classification='INCOME')
reviewed.review_status = 'REVIEWED'
reviewed.save(update_fields=['review_status'])
transaction('EXAMPLE NOT REQUIRED', classification='INCOME')
transaction('EXAMPLE ARCHIVED', classification='TRANSFER', subcategory=archived,
            savings_effect='CONTRIBUTION', duplicate_resolution='UNRESOLVED')
archived.is_active = False
archived.save()
