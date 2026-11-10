import re

from django.db.models import Q

from .models import (
    BudgetBucket,
    CategorizationRule,
    DuplicateResolution,
    ReviewStatus,
    RuleMatchField,
    RuleMatchType,
    SavingsEffect,
    SubcategoryScope,
    Transaction,
    TransactionClassification,
)


def normalize_rule_text(value):
    return re.sub(r'\s+', ' ', value).strip().casefold()


def stored_rule_value(value):
    return re.sub(r'\s+', ' ', value).strip()


MATCH_TYPES = (RuleMatchType.CONTAINS, RuleMatchType.STARTS_WITH)


def target_is_valid(classification, savings_effect, subcategory):
    if classification in (TransactionClassification.EXPENSE, TransactionClassification.REFUND):
        return bool(subcategory and subcategory.scope == SubcategoryScope.BUDGET
                    and subcategory.budget_bucket in (BudgetBucket.NEEDS, BudgetBucket.WANTS))
    if classification == TransactionClassification.OTHER_INFLOW:
        return subcategory is None or (
            subcategory.scope == SubcategoryScope.OTHER_INFLOW and subcategory.budget_bucket is None
        )
    if classification == TransactionClassification.TRANSFER and savings_effect != SavingsEffect.NONE:
        return bool(subcategory and subcategory.scope == SubcategoryScope.BUDGET
                    and subcategory.budget_bucket == BudgetBucket.SAVINGS)
    return subcategory is None


def rule_is_usable(rule):
    return (
        rule.is_active
        and (rule.subcategory is None or rule.subcategory.is_active)
        and target_is_valid(rule.classification, rule.savings_effect, rule.subcategory)
    )


def eligibility_scope(rule):
    if rule.classification == TransactionClassification.EXPENSE:
        return ('UNKNOWN', 'EXPENSE') if rule.allow_unknown else ('EXPENSE',)
    if rule.classification == TransactionClassification.TRANSFER and rule.savings_effect != SavingsEffect.NONE:
        return ('UNKNOWN', 'TRANSFER')
    return ('UNKNOWN',)


def rule_is_eligible(rule, classification, transfer_unresolved=True, subcategory_id=None,
                     manually_resolved=False):
    if classification == TransactionClassification.UNKNOWN:
        return (
            subcategory_id is None and not manually_resolved
            and (rule.classification != TransactionClassification.EXPENSE or rule.allow_unknown)
        )
    if classification == TransactionClassification.EXPENSE:
        return rule.classification == TransactionClassification.EXPENSE and subcategory_id is None
    if classification == TransactionClassification.TRANSFER:
        return (
            rule.classification == TransactionClassification.TRANSFER
            and rule.savings_effect != SavingsEffect.NONE
            and transfer_unresolved and subcategory_id is None and not manually_resolved
        )
    return False


def rule_matches(rule, classification, raw_description, transfer_unresolved=True,
                 subcategory_id=None, manually_resolved=False):
    if (
        not rule_is_usable(rule)
        or rule.match_field != RuleMatchField.RAW_DESCRIPTION
        or rule.match_type not in MATCH_TYPES
        or not rule_is_eligible(rule, classification, transfer_unresolved, subcategory_id, manually_resolved)
    ):
        return False
    value = normalize_rule_text(rule.match_value)
    description = normalize_rule_text(raw_description)
    if not value:
        return False
    if rule.match_type == RuleMatchType.STARTS_WITH:
        return description.startswith(value)
    return value in description


def rule_outcome(rule):
    return (rule.classification, rule.savings_effect, rule.subcategory_id)


def evaluate_rules(classification, raw_description, rules, transfer_unresolved=True,
                   subcategory_id=None, manually_resolved=False):
    empty = {
        'rules': [], 'rule': None, 'subcategory': None, 'conflict': False,
        'classification': None, 'savings_effect': SavingsEffect.NONE,
    }
    matches = [
        rule for rule in rules
        if rule_matches(rule, classification, raw_description, transfer_unresolved,
                        subcategory_id, manually_resolved)
    ]
    if not matches:
        return empty
    if len({rule_outcome(rule) for rule in matches}) > 1:
        return {**empty, 'rules': matches, 'conflict': True}
    representative = min(matches, key=lambda rule: rule.pk)
    return {
        'rules': matches, 'rule': representative, 'subcategory': representative.subcategory,
        'conflict': False, 'classification': representative.classification,
        'savings_effect': representative.savings_effect,
    }


def evaluate_transaction(record, rules):
    return evaluate_rules(
        record.classification, record.raw_description, rules,
        transfer_unresolved=record.savings_effect == SavingsEffect.NONE,
        subcategory_id=record.subcategory_id, manually_resolved=record.manually_resolved,
    )


def rules_for_evaluation():
    return list(
        CategorizationRule.objects.filter(is_active=True).select_related('subcategory').order_by('id')
    )


def evaluate_import_rows(rows):
    rules = rules_for_evaluation()
    for row in rows:
        row['_categorization'] = (
            evaluate_rules('', '', ()) if row['overlap_status'] == 'ALREADY_REPRESENTED'
            else evaluate_rules(row['classification'], row['raw_description'], rules)
        )
    return rows


def pending_candidates(rule):
    condition = Q(classification=TransactionClassification.UNKNOWN, subcategory__isnull=True,
                  savings_effect=SavingsEffect.NONE, manually_resolved=False)
    if rule.classification == TransactionClassification.EXPENSE:
        condition = Q(classification=TransactionClassification.EXPENSE, subcategory__isnull=True)
        if rule.allow_unknown:
            condition |= Q(classification=TransactionClassification.UNKNOWN, subcategory__isnull=True,
                           savings_effect=SavingsEffect.NONE, manually_resolved=False)
    elif rule.classification == TransactionClassification.TRANSFER and rule.savings_effect != SavingsEffect.NONE:
        condition |= Q(classification=TransactionClassification.TRANSFER, subcategory__isnull=True,
                       savings_effect=SavingsEffect.NONE, manually_resolved=False)
    return Transaction.objects.filter(condition, review_status=ReviewStatus.PENDING)


def pending_matches_for_rule(rule, exclude_ids=(), candidates=None, rules=None):
    rules = rules if rules is not None else rules_for_evaluation()
    safe_matches, conflicts = [], []
    if candidates is None:
        # ponytail: scan pending candidates and rules in Python; narrow in SQL only if this local ledger grows large.
        candidates = pending_candidates(rule)
    excluded = set(exclude_ids)
    for candidate in candidates:
        if candidate.duplicate_resolution == DuplicateResolution.CONFIRMED_DUPLICATE or candidate.pk in excluded:
            continue
        result = evaluate_transaction(candidate, rules)
        if rule not in result['rules']:
            continue
        (conflicts if result['conflict'] else safe_matches).append(candidate)
    return safe_matches, conflicts


def apply_rule_to_transaction(transaction_record, rule):
    transaction_record.classification = rule.classification
    transaction_record.savings_effect = rule.savings_effect
    transaction_record.subcategory = rule.subcategory
    transaction_record.budget_bucket = (rule.subcategory.budget_bucket or '') if rule.subcategory else ''
    transaction_record.categorized_by_rule = rule
    transaction_record.categorization_rule_conflict = False
    transaction_record.manually_resolved = False
    transaction_record.review_status = (
        ReviewStatus.PENDING if transaction_record.review_blockers() else ReviewStatus.NOT_REQUIRED
    )
    transaction_record.save(update_fields=(
        'classification', 'savings_effect', 'subcategory', 'budget_bucket', 'categorized_by_rule',
        'categorization_rule_conflict', 'manually_resolved', 'review_status', 'updated_at',
    ))


def import_row_needs_review(row):
    if row['overlap_status'] == 'ALREADY_REPRESENTED':
        return False
    if row['overlap_status'] == 'POSSIBLE_DUPLICATE' or row['_categorization']['conflict']:
        return True
    result = row['_categorization']
    if result['rule']:
        return False
    classification = row['classification']
    return classification in (
        TransactionClassification.UNKNOWN, TransactionClassification.REFUND,
        TransactionClassification.TRANSFER, TransactionClassification.EXPENSE,
    )
