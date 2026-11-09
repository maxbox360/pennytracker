import re

from .models import (
    BudgetBucket,
    CategorizationRule,
    DuplicateResolution,
    ReviewStatus,
    RuleKind,
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


KIND_CLASSIFICATION = {
    RuleKind.EXPENSE_CATEGORY: TransactionClassification.EXPENSE,
    RuleKind.SAVINGS_TRANSFER: TransactionClassification.TRANSFER,
}
KIND_BUCKETS = {
    RuleKind.EXPENSE_CATEGORY: (BudgetBucket.NEEDS, BudgetBucket.WANTS),
    RuleKind.SAVINGS_TRANSFER: (BudgetBucket.SAVINGS,),
}
MATCH_TYPES = (RuleMatchType.CONTAINS, RuleMatchType.STARTS_WITH)


def target_is_valid(rule_kind, subcategory):
    return (
        subcategory.scope == SubcategoryScope.BUDGET
        and subcategory.budget_bucket in KIND_BUCKETS[rule_kind]
    )


def rule_is_usable(rule):
    return (
        rule.is_active
        and rule.subcategory.is_active
        and target_is_valid(rule.rule_kind, rule.subcategory)
    )


def rule_is_eligible(rule, classification, transfer_unresolved):
    if rule.rule_kind == RuleKind.EXPENSE_CATEGORY:
        return classification == TransactionClassification.EXPENSE
    return classification == TransactionClassification.UNKNOWN or (
        classification == TransactionClassification.TRANSFER and transfer_unresolved
    )


def rule_matches(rule, classification, raw_description, transfer_unresolved=True):
    if (
        not rule_is_usable(rule)
        or rule.match_field != RuleMatchField.RAW_DESCRIPTION
        or rule.match_type not in MATCH_TYPES
        or not rule_is_eligible(rule, classification, transfer_unresolved)
    ):
        return False
    value = normalize_rule_text(rule.match_value)
    description = normalize_rule_text(raw_description)
    if rule.match_type == RuleMatchType.STARTS_WITH:
        return description.startswith(value)
    return value in description


def rule_outcome(rule):
    return (rule.savings_effect, rule.subcategory_id)


def evaluate_rules(classification, raw_description, rules, transfer_unresolved=True):
    """One shared evaluator for import preview/confirm and apply-to-pending.

    The transaction's classification selects which rule kind is eligible, so Expense and
    Savings rules never compete. The outcome never depends on rule ID.
    """
    empty = {
        'rules': [], 'rule': None, 'subcategory': None, 'conflict': False,
        'classification': None, 'savings_effect': SavingsEffect.NONE,
    }
    matches = [
        rule for rule in rules
        if rule_matches(rule, classification, raw_description, transfer_unresolved)
    ]
    if not matches:
        return empty
    if len({rule_outcome(rule) for rule in matches}) > 1:
        return {**empty, 'rules': matches, 'conflict': True}
    representative = matches[0]
    return {
        'rules': matches,
        'rule': representative,
        'subcategory': representative.subcategory,
        'conflict': False,
        'classification': (
            representative.classification
            if representative.rule_kind == RuleKind.SAVINGS_TRANSFER else None
        ),
        'savings_effect': representative.savings_effect,
    }


def rules_for_evaluation():
    return list(
        CategorizationRule.objects.filter(is_active=True)
        .select_related('subcategory')
        .order_by('id')
    )


def evaluate_import_rows(rows):
    rules = rules_for_evaluation()
    for row in rows:
        if row['overlap_status'] == 'ALREADY_REPRESENTED':
            row['_categorization'] = evaluate_rules('', '', ())
        else:
            row['_categorization'] = evaluate_rules(
                row['classification'],
                row['raw_description'],
                rules,
            )
    return rows


def pending_candidates(rule):
    if rule.rule_kind == RuleKind.SAVINGS_TRANSFER:
        # Unresolved Savings candidates only; manually resolved transfers are never overwritten.
        return Transaction.objects.filter(
            review_status=ReviewStatus.PENDING,
            classification__in=(TransactionClassification.UNKNOWN, TransactionClassification.TRANSFER),
            subcategory__isnull=True,
            savings_effect=SavingsEffect.NONE,
        )
    return Transaction.objects.filter(
        review_status=ReviewStatus.PENDING,
        classification=TransactionClassification.EXPENSE,
        subcategory__isnull=True,
    )


def pending_matches_for_rule(rule, exclude_ids=(), candidates=None, rules=None):
    rules = rules if rules is not None else rules_for_evaluation()
    safe_matches = []
    conflicts = []
    if candidates is None:
        # ponytail: scan pending candidates and rules in Python; narrow in SQL only if this local ledger grows large.
        candidates = pending_candidates(rule)
    if hasattr(candidates, 'exclude'):
        candidates = (
            candidates.exclude(duplicate_resolution=DuplicateResolution.CONFIRMED_DUPLICATE)
            .exclude(pk__in=exclude_ids)
            .order_by('pk')
        )
    else:
        exclude_ids = set(exclude_ids)
        candidates = [
            candidate for candidate in candidates
            if candidate.duplicate_resolution != DuplicateResolution.CONFIRMED_DUPLICATE
            and candidate.pk not in exclude_ids
        ]
    for candidate in candidates:
        result = evaluate_rules(
            candidate.classification,
            candidate.raw_description,
            rules,
        )
        if rule not in result['rules']:
            continue
        if result['conflict']:
            conflicts.append(candidate)
        else:
            safe_matches.append(candidate)
    return safe_matches, conflicts


def apply_rule_to_transaction(transaction_record, rule):
    transaction_record.classification = rule.classification
    transaction_record.savings_effect = rule.savings_effect
    transaction_record.subcategory = rule.subcategory
    transaction_record.budget_bucket = rule.subcategory.budget_bucket
    transaction_record.categorized_by_rule = rule
    transaction_record.categorization_rule_conflict = False
    transaction_record.review_status = (
        ReviewStatus.PENDING
        if transaction_record.review_blockers()
        else ReviewStatus.NOT_REQUIRED
    )
    transaction_record.save(update_fields=(
        'classification',
        'savings_effect',
        'subcategory',
        'budget_bucket',
        'categorized_by_rule',
        'categorization_rule_conflict',
        'review_status',
        'updated_at',
    ))


def import_row_needs_review(row):
    """Single Review-status decision for import preview and confirmation."""
    if row['overlap_status'] == 'ALREADY_REPRESENTED':
        return False
    if row['overlap_status'] == 'POSSIBLE_DUPLICATE':
        return True
    result = row['_categorization']
    if result['conflict']:
        return True
    if result['savings_effect'] != SavingsEffect.NONE:
        return False
    classification = row['classification']
    if classification in (
        TransactionClassification.UNKNOWN,
        TransactionClassification.REFUND,
        TransactionClassification.TRANSFER,
    ):
        return True
    return classification == TransactionClassification.EXPENSE and result['subcategory'] is None
