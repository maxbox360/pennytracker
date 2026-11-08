import re

from .models import (
    BudgetBucket,
    CategorizationRule,
    DuplicateResolution,
    ReviewStatus,
    RuleMatchField,
    RuleMatchType,
    Transaction,
    TransactionClassification,
)


def normalize_rule_text(value):
    return re.sub(r'\s+', ' ', value).strip().casefold()


def stored_rule_value(value):
    return re.sub(r'\s+', ' ', value).strip()


def rule_matches(rule, classification, raw_description):
    if (
        not rule.is_active
        or not rule.subcategory.is_active
        or rule.subcategory.budget_bucket not in (BudgetBucket.NEEDS, BudgetBucket.WANTS)
        or classification != rule.classification
        or rule.match_field != RuleMatchField.RAW_DESCRIPTION
        or rule.match_type != RuleMatchType.CONTAINS
    ):
        return False
    return normalize_rule_text(rule.match_value) in normalize_rule_text(raw_description)


def evaluate_rules(classification, raw_description, rules):
    matches = [
        rule for rule in rules
        if rule_matches(rule, classification, raw_description)
    ]
    if not matches:
        return {'rules': [], 'rule': None, 'subcategory': None, 'conflict': False}

    subcategory_ids = {rule.subcategory_id for rule in matches}
    if len(subcategory_ids) > 1:
        return {'rules': matches, 'rule': None, 'subcategory': None, 'conflict': True}

    return {
        'rules': matches,
        'rule': matches[0],
        'subcategory': matches[0].subcategory,
        'conflict': False,
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


def pending_matches_for_rule(rule, exclude_ids=(), candidates=None, rules=None):
    rules = rules if rules is not None else rules_for_evaluation()
    safe_matches = []
    conflicts = []
    if candidates is None:
        # ponytail: scan pending expenses and rules in Python; narrow in SQL only if this local ledger grows large.
        candidates = Transaction.objects.filter(
            review_status=ReviewStatus.PENDING,
            classification=TransactionClassification.EXPENSE,
            subcategory__isnull=True,
        )
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
        elif result['subcategory'].pk == rule.subcategory_id:
            safe_matches.append(candidate)
    return safe_matches, conflicts


def apply_rule_to_transaction(transaction_record, rule):
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
        'subcategory',
        'budget_bucket',
        'categorized_by_rule',
        'categorization_rule_conflict',
        'review_status',
        'updated_at',
    ))
