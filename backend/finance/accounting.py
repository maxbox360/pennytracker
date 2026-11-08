from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from datetime import date

from .models import (
    BudgetBucket,
    DuplicateResolution,
    ReviewStatus,
    SavingsEffect,
    Transaction,
    TransactionClassification,
)

ZERO = Decimal('0.00')
CENT = Decimal('0.01')
HUNDRED = Decimal('100')


def money(value):
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def percentage(value):
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def monthly_bounds(year, month):
    start = date(year, month, 1)
    end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return start, end


def _effective_record(transaction):
    reasons = []
    if transaction.classification == TransactionClassification.EXCLUDED:
        return None, reasons, 'excluded'
    if transaction.duplicate_resolution == DuplicateResolution.CONFIRMED_DUPLICATE:
        return None, reasons, 'confirmed_duplicate'
    if transaction.duplicate_resolution == DuplicateResolution.UNRESOLVED:
        reasons.append('UNRESOLVED_DUPLICATE')
    if transaction.categorization_rule_conflict:
        reasons.append('RULE_CONFLICT')
    if transaction.classification == TransactionClassification.UNKNOWN:
        reasons.append('UNKNOWN')
    elif transaction.classification in (
        TransactionClassification.EXPENSE,
        TransactionClassification.REFUND,
    ):
        if (
            transaction.subcategory_id is None
            or transaction.budget_bucket not in (BudgetBucket.NEEDS, BudgetBucket.WANTS)
        ):
            reasons.append('UNCATEGORIZED')
    elif transaction.classification == TransactionClassification.TRANSFER:
        if transaction.savings_effect == SavingsEffect.NONE:
            if transaction.review_status == ReviewStatus.PENDING:
                reasons.append('TRANSFER_SAVINGS_UNREVIEWED')
        elif (
            transaction.subcategory_id is None
            or transaction.budget_bucket != BudgetBucket.SAVINGS
        ):
            reasons.append('SAVINGS_EFFECT_INVALID')

    if reasons:
        return None, reasons, None

    if transaction.classification == TransactionClassification.INCOME:
        return ('INCOME', transaction.amount), [], None
    if transaction.classification in (
        TransactionClassification.EXPENSE,
        TransactionClassification.REFUND,
    ):
        return (transaction.budget_bucket, -transaction.amount), [], None
    if transaction.classification == TransactionClassification.TRANSFER:
        if transaction.savings_effect == SavingsEffect.CONTRIBUTION:
            return ('SAVINGS_CONTRIBUTION', abs(transaction.amount)), [], None
        if transaction.savings_effect == SavingsEffect.WITHDRAWAL:
            return ('SAVINGS_WITHDRAWAL', abs(transaction.amount)), [], None
    return None, [], None


def monthly_report(year, month, targets):
    start, end = monthly_bounds(year, month)
    records = list(
        Transaction.objects.filter(date__gte=start, date__lt=end)
        .select_related('subcategory', 'account')
        .order_by('date', 'id')
    )
    totals = defaultdict(lambda: ZERO)
    counts = defaultdict(int)
    breakdowns = {bucket: defaultdict(lambda: {'amount': ZERO, 'count': 0}) for bucket in BudgetBucket.values}
    eligible_ids = set()
    contributions = {}
    blocker_ids = defaultdict(set)
    excluded_counts = defaultdict(int)

    for record in records:
        kind, reasons, excluded = _effective_record(record)
        if excluded:
            excluded_counts[excluded] += 1
            continue
        for reason in reasons:
            blocker_ids[reason].add(record.pk)
        if reasons:
            continue
        if kind is None:
            continue
        amount = kind[1]
        bucket = kind[0]
        totals[bucket] += amount
        counts[bucket] += 1
        eligible_ids.add(record.pk)
        contributions[record.pk] = (bucket, amount)
        breakdown_bucket = (
            BudgetBucket.SAVINGS
            if bucket in ('SAVINGS_CONTRIBUTION', 'SAVINGS_WITHDRAWAL')
            else bucket
        )
        if breakdown_bucket in BudgetBucket.values:
            entry = breakdowns[breakdown_bucket][record.subcategory_id]
            entry['amount'] += -amount if bucket == 'SAVINGS_WITHDRAWAL' else amount
            entry['count'] += 1

    income = totals['INCOME']
    needs = totals[BudgetBucket.NEEDS]
    wants = totals[BudgetBucket.WANTS]
    savings_contributions = totals['SAVINGS_CONTRIBUTION']
    savings_withdrawals = totals['SAVINGS_WITHDRAWAL']
    net_savings = savings_contributions - savings_withdrawals
    remaining = income - needs - wants - savings_contributions
    target_values = {
        BudgetBucket.NEEDS: targets.needs_percent,
        BudgetBucket.WANTS: targets.wants_percent,
        BudgetBucket.SAVINGS: targets.savings_percent,
    }

    def allocation(bucket, actual):
        target = target_values[bucket]
        if income <= 0:
            return {
                'target_percent': percentage(target),
                'actual_percent': None,
                'variance_points': None,
                'target_amount': None,
            }
        actual_percent = actual / income * HUNDRED
        return {
            'target_percent': percentage(target),
            'actual_percent': percentage(actual_percent),
            'variance_points': percentage(actual_percent - target),
            'target_amount': money(income * target / HUNDRED),
        }

    subcategory_ids = {
        subcategory_id
        for groups in breakdowns.values()
        for subcategory_id in groups
    }
    subcategory_names = dict(
        Transaction.objects.filter(subcategory_id__in=subcategory_ids)
        .values_list('subcategory_id', 'subcategory__name')
    )
    breakdown = {}
    for bucket, groups in breakdowns.items():
        breakdown[bucket] = [
            {
                'subcategory': subcategory_id,
                'subcategory_name': subcategory_names[subcategory_id],
                'amount': money(group['amount']),
                'transaction_count': group['count'],
            }
            for subcategory_id, group in sorted(
                groups.items(),
                key=lambda item: (subcategory_names[item[0]].casefold(), item[0]),
            )
        ]

    blockers = {
        reason: {'transaction_count': len(ids)}
        for reason, ids in sorted(blocker_ids.items())
    }
    affected_ids = set().union(*blocker_ids.values()) if blocker_ids else set()
    return {
        'period': {'year': year, 'month': month, 'start_date': start.isoformat(), 'end_date': (end - date.resolution).isoformat()},
        'summary': {
            'income': {'amount': money(income), 'transaction_count': counts['INCOME']},
            'needs': {
                'amount': money(needs),
                'transaction_count': counts[BudgetBucket.NEEDS],
                **allocation(BudgetBucket.NEEDS, needs),
            },
            'wants': {
                'amount': money(wants),
                'transaction_count': counts[BudgetBucket.WANTS],
                **allocation(BudgetBucket.WANTS, wants),
            },
            'savings_contributions': {
                'amount': money(savings_contributions),
                'transaction_count': counts['SAVINGS_CONTRIBUTION'],
                **allocation(BudgetBucket.SAVINGS, savings_contributions),
            },
            'savings_withdrawals': {
                'amount': money(savings_withdrawals),
                'transaction_count': counts['SAVINGS_WITHDRAWAL'],
            },
            'net_savings_activity': {
                'amount': money(net_savings),
                'transaction_count': counts['SAVINGS_CONTRIBUTION'] + counts['SAVINGS_WITHDRAWAL'],
            },
            'remaining': {'amount': money(remaining)},
        },
        'breakdown': breakdown,
        'data_completeness': {
            'is_incomplete': bool(affected_ids),
            'affected_transaction_count': len(affected_ids),
            'blockers': blockers,
            'excluded': excluded_counts,
        },
        '_eligible_ids': eligible_ids,
        '_contributions': contributions,
        '_records': {record.pk: record for record in records},
    }


def drilldown_rows(report, metric, subcategory_id=None):
    contribution_buckets = {
        'income': {'INCOME'},
        'needs': {BudgetBucket.NEEDS},
        'wants': {BudgetBucket.WANTS},
        'savings_contributions': {'SAVINGS_CONTRIBUTION'},
        'savings_withdrawals': {'SAVINGS_WITHDRAWAL'},
        'net_savings_activity': {'SAVINGS_CONTRIBUTION', 'SAVINGS_WITHDRAWAL'},
        'remaining': {'INCOME', BudgetBucket.NEEDS, BudgetBucket.WANTS, 'SAVINGS_CONTRIBUTION'},
    }
    if metric not in contribution_buckets:
        raise ValueError('Choose a valid report metric.')
    rows = []
    for transaction_id, (bucket, amount) in report['_contributions'].items():
        if bucket not in contribution_buckets[metric]:
            continue
        transaction_record = report['_records'][transaction_id]
        if subcategory_id is not None and transaction_record.subcategory_id != subcategory_id:
            continue
        if metric == 'remaining':
            amount = -amount if bucket == 'SAVINGS_CONTRIBUTION' else amount
            amount = -amount if bucket in (BudgetBucket.NEEDS, BudgetBucket.WANTS) else amount
        elif metric == 'net_savings_activity' and bucket == 'SAVINGS_WITHDRAWAL':
            amount = -amount
        rows.append({
            'id': transaction_record.pk,
            'date': transaction_record.date.isoformat(),
            'description': transaction_record.merchant or transaction_record.raw_description,
            'raw_description': transaction_record.raw_description,
            'account': transaction_record.account.name,
            'classification': transaction_record.classification,
            'savings_effect': transaction_record.savings_effect,
            'subcategory': transaction_record.subcategory.name if transaction_record.subcategory_id else None,
            'amount': money(transaction_record.amount),
            'contribution': money(amount),
        })
    return rows
