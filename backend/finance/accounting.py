from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import (
    Case,
    Count,
    DecimalField,
    F,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce, ExtractMonth, ExtractYear

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
MONEY_FIELD = DecimalField(max_digits=20, decimal_places=2)


def money(value):
    return str((value or ZERO).quantize(CENT, rounding=ROUND_HALF_UP))


def percentage(value):
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def monthly_bounds(year, month):
    start = date(year, month, 1)
    end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return start, end


def year_bounds(year):
    return date(year, 1, 1), date(year + 1, 1, 1)


def _absolute_amount():
    return Case(
        When(amount__gte=ZERO, then=F('amount')),
        default=-F('amount'),
        output_field=MONEY_FIELD,
    )


def _expense_condition(bucket=None):
    condition = Q(
        classification__in=(
            TransactionClassification.EXPENSE,
            TransactionClassification.REFUND,
        ),
        budget_bucket__in=(BudgetBucket.NEEDS, BudgetBucket.WANTS),
    )
    return condition & Q(budget_bucket=bucket) if bucket else condition


def _contribution_expression(metric):
    if metric == 'income':
        return Case(
            When(classification=TransactionClassification.INCOME, then=F('amount')),
            default=Value(ZERO),
            output_field=MONEY_FIELD,
        )
    if metric in ('needs', 'wants'):
        bucket = BudgetBucket.NEEDS if metric == 'needs' else BudgetBucket.WANTS
        return Case(
            When(_expense_condition(bucket), then=-F('amount')),
            default=Value(ZERO),
            output_field=MONEY_FIELD,
        )
    if metric == 'savings_contributions':
        return Case(
            When(
                classification=TransactionClassification.TRANSFER,
                savings_effect=SavingsEffect.CONTRIBUTION,
                then=_absolute_amount(),
            ),
            default=Value(ZERO),
            output_field=MONEY_FIELD,
        )
    if metric == 'savings_withdrawals':
        return Case(
            When(
                classification=TransactionClassification.TRANSFER,
                savings_effect=SavingsEffect.WITHDRAWAL,
                then=_absolute_amount(),
            ),
            default=Value(ZERO),
            output_field=MONEY_FIELD,
        )
    if metric == 'net_savings_activity':
        return Case(
            When(
                classification=TransactionClassification.TRANSFER,
                savings_effect=SavingsEffect.CONTRIBUTION,
                then=_absolute_amount(),
            ),
            When(
                classification=TransactionClassification.TRANSFER,
                savings_effect=SavingsEffect.WITHDRAWAL,
                then=-_absolute_amount(),
            ),
            default=Value(ZERO),
            output_field=MONEY_FIELD,
        )
    if metric == 'remaining':
        return Case(
            When(classification=TransactionClassification.INCOME, then=F('amount')),
            When(_expense_condition(), then=F('amount')),
            When(
                classification=TransactionClassification.TRANSFER,
                savings_effect=SavingsEffect.CONTRIBUTION,
                then=-_absolute_amount(),
            ),
            default=Value(ZERO),
            output_field=MONEY_FIELD,
        )
    raise ValueError('Choose a valid report metric.')


def _sum_metric(metric):
    return Coalesce(
        Sum(_contribution_expression(metric), output_field=MONEY_FIELD),
        Value(ZERO, output_field=MONEY_FIELD),
        output_field=MONEY_FIELD,
    )


def _count_metric(metric):
    if metric == 'income':
        condition = Q(classification=TransactionClassification.INCOME)
    elif metric in ('needs', 'wants'):
        condition = _expense_condition(BudgetBucket.NEEDS if metric == 'needs' else BudgetBucket.WANTS)
    elif metric == 'savings_contributions':
        condition = Q(
            classification=TransactionClassification.TRANSFER,
            savings_effect=SavingsEffect.CONTRIBUTION,
        )
    elif metric == 'savings_withdrawals':
        condition = Q(
            classification=TransactionClassification.TRANSFER,
            savings_effect=SavingsEffect.WITHDRAWAL,
        )
    elif metric == 'net_savings_activity':
        condition = Q(
            classification=TransactionClassification.TRANSFER,
            savings_effect__in=(SavingsEffect.CONTRIBUTION, SavingsEffect.WITHDRAWAL),
        )
    else:
        return None
    return Count('pk', filter=condition)


def _eligibility_blockers():
    return {
        'UNRESOLVED_DUPLICATE': Q(duplicate_resolution=DuplicateResolution.UNRESOLVED),
        'RULE_CONFLICT': Q(categorization_rule_conflict=True),
        'UNKNOWN': Q(classification=TransactionClassification.UNKNOWN),
        'UNCATEGORIZED': (
            Q(classification__in=(
                TransactionClassification.EXPENSE,
                TransactionClassification.REFUND,
            ))
            & (
                Q(subcategory__isnull=True)
                | Q(budget_bucket__in=('',))
                | Q(budget_bucket__isnull=True)
                | ~Q(budget_bucket__in=(BudgetBucket.NEEDS, BudgetBucket.WANTS))
            )
        ),
        'TRANSFER_SAVINGS_UNREVIEWED': Q(
            classification=TransactionClassification.TRANSFER,
            savings_effect=SavingsEffect.NONE,
            review_status=ReviewStatus.PENDING,
        ),
        'SAVINGS_EFFECT_INVALID': (
            Q(
                classification=TransactionClassification.TRANSFER,
                savings_effect__in=(SavingsEffect.CONTRIBUTION, SavingsEffect.WITHDRAWAL),
            )
            & (
                Q(subcategory__isnull=True)
                | ~Q(budget_bucket=BudgetBucket.SAVINGS)
            )
        ),
    }


def _blocker_union(blockers):
    combined = Q(pk__in=[])
    for condition in blockers.values():
        combined |= condition
    return combined


def _eligible_transactions(transactions):
    blockers = _eligibility_blockers()
    return (
        transactions
        .exclude(classification=TransactionClassification.EXCLUDED)
        .exclude(duplicate_resolution=DuplicateResolution.CONFIRMED_DUPLICATE)
        .exclude(_blocker_union(blockers))
    )


def _period_data(start=None, end=None):
    transactions = Transaction.objects.all()
    if start is not None:
        transactions = transactions.filter(date__gte=start)
    if end is not None:
        transactions = transactions.filter(date__lt=end)
    return transactions


def _aggregate_totals(transactions):
    metrics = (
        'income',
        'needs',
        'wants',
        'savings_contributions',
        'savings_withdrawals',
        'net_savings_activity',
    )
    aggregation = {metric: _sum_metric(metric) for metric in metrics}
    aggregation.update({
        f'{metric}_count': _count_metric(metric)
        for metric in metrics
        if _count_metric(metric) is not None
    })
    return _eligible_transactions(transactions).aggregate(**aggregation)


def _complete_summary(raw, targets):
    income = raw.get('income') or ZERO
    needs = raw.get('needs') or ZERO
    wants = raw.get('wants') or ZERO
    contributions = raw.get('savings_contributions') or ZERO
    withdrawals = raw.get('savings_withdrawals') or ZERO
    remaining = income - needs - wants - contributions
    target_values = {
        'needs': targets.needs_percent,
        'wants': targets.wants_percent,
        'savings_contributions': targets.savings_percent,
    }

    def allocation(metric, amount):
        target = target_values[metric]
        if income <= 0:
            return {
                'target_percent': percentage(target),
                'actual_percent': None,
                'variance_points': None,
                'target_amount': None,
            }
        actual = amount / income * HUNDRED
        return {
            'target_percent': percentage(target),
            'actual_percent': percentage(actual),
            'variance_points': percentage(actual - target),
            'target_amount': money(income * target / HUNDRED),
        }

    def value(metric, amount, with_allocation=False):
        result = {
            'amount': money(amount),
            'transaction_count': raw.get(f'{metric}_count') or 0,
        }
        return {**result, **allocation(metric, amount)} if with_allocation else result

    return {
        'income': value('income', income),
        'needs': value('needs', needs, True),
        'wants': value('wants', wants, True),
        'savings_contributions': value('savings_contributions', contributions, True),
        'savings_withdrawals': value('savings_withdrawals', withdrawals),
        'net_savings_activity': value(
            'net_savings_activity',
            contributions - withdrawals,
        ),
        'remaining': {'amount': money(remaining)},
    }


def _completeness(transactions):
    blockers = _eligibility_blockers()
    blocker_values = transactions.aggregate(**{
        reason: Count('pk', filter=condition)
        for reason, condition in blockers.items()
    })
    blocker_values['excluded'] = transactions.filter(
        classification=TransactionClassification.EXCLUDED
    ).count()
    blocker_values['confirmed_duplicate'] = transactions.filter(
        duplicate_resolution=DuplicateResolution.CONFIRMED_DUPLICATE
    ).count()
    affected = transactions.filter(_blocker_union(blockers)).count()
    return {
        'is_incomplete': bool(affected),
        'affected_transaction_count': affected,
        'blockers': {
            reason: {'transaction_count': count}
            for reason, count in sorted(
                (reason, blocker_values.get(reason) or 0)
                for reason in blockers
            )
            if count
        },
        'excluded': {
            key: blocker_values[key]
            for key in ('excluded', 'confirmed_duplicate')
            if blocker_values[key]
        },
    }


def _breakdown(transactions, ranked=False):
    eligible = _eligible_transactions(transactions).filter(
        Q(
            classification__in=(
                TransactionClassification.EXPENSE,
                TransactionClassification.REFUND,
            ),
            budget_bucket__in=(BudgetBucket.NEEDS, BudgetBucket.WANTS),
        )
        | Q(
            classification=TransactionClassification.TRANSFER,
            savings_effect__in=(SavingsEffect.CONTRIBUTION, SavingsEffect.WITHDRAWAL),
        )
    )
    savings_net = Case(
        When(
            classification=TransactionClassification.TRANSFER,
            savings_effect=SavingsEffect.WITHDRAWAL,
            then=-_absolute_amount(),
        ),
        When(
            classification=TransactionClassification.TRANSFER,
            savings_effect=SavingsEffect.CONTRIBUTION,
            then=_absolute_amount(),
        ),
        default=-F('amount'),
        output_field=MONEY_FIELD,
    )
    grouped = (
        eligible.values(
            'budget_bucket',
            'subcategory_id',
            'subcategory__name',
        )
        .annotate(
            total=Coalesce(
                Sum(savings_net, output_field=MONEY_FIELD),
                Value(ZERO, output_field=MONEY_FIELD),
                output_field=MONEY_FIELD,
            ),
            transaction_count=Count('pk'),
        )
    )
    rows = list(grouped)
    rows.sort(
        key=lambda row: (
            row['budget_bucket'],
            -row['total'] if ranked else Decimal('0'),
            (row['subcategory__name'] or '').casefold(),
            row['subcategory_id'],
        )
    )
    result = {bucket: [] for bucket in BudgetBucket.values}
    for row in rows:
        result[row['budget_bucket']].append({
            'subcategory': row['subcategory_id'],
            'subcategory_name': row['subcategory__name'],
            'amount': money(row['total']),
            'transaction_count': row['transaction_count'],
        })
    return result


def _report(start, end, targets, transactions=None, *, include_breakdown=True, ranked=False):
    transactions = transactions if transactions is not None else _period_data(start, end)
    raw = _aggregate_totals(transactions)
    report = {
        'summary': _complete_summary(raw, targets),
        'data_completeness': _completeness(transactions),
        'ledger_transaction_count': transactions.count(),
        '_start': start,
        '_end': end,
    }
    if include_breakdown:
        report['breakdown'] = _breakdown(transactions, ranked=ranked)
    return report


def monthly_report(year, month, targets):
    start, end = monthly_bounds(year, month)
    report = _report(start, end, targets)
    report['period'] = {
        'year': year,
        'month': month,
        'start_date': start.isoformat(),
        'end_date': (end - date.resolution).isoformat(),
    }
    report.pop('ledger_transaction_count')
    return report


def _grouped_values(transactions, grain):
    grouped = transactions.annotate(
        _year=ExtractYear('date'),
        _month=ExtractMonth('date'),
    )
    if grain == 'year':
        grouped = grouped.values('_year')
    else:
        grouped = grouped.values('_year', '_month')
    metric_names = (
        'income',
        'needs',
        'wants',
        'savings_contributions',
        'savings_withdrawals',
        'net_savings_activity',
    )
    metrics = {name: _sum_metric(name) for name in metric_names}
    metrics.update({
        f'{name}_count': _count_metric(name)
        for name in metric_names
        if _count_metric(name) is not None
    })
    eligible_groups = _eligible_transactions(grouped).annotate(
        **{f'_sum_{name}': expression for name, expression in metrics.items()}
    )
    # Aggregate expressions are attached to the grouped query, which emits one
    # row per calendar period without loading transactions into Python.
    return list(eligible_groups.values(
        '_year',
        *(['_month'] if grain != 'year' else []),
        *[f'_sum_{name}' for name in metrics],
    ).annotate(ledger_count=Count('pk')))


def _period_map_rows(transactions, grain):
    grouped = transactions.annotate(
        _year=ExtractYear('date'),
        _month=ExtractMonth('date'),
    )
    fields = ['_year'] if grain == 'year' else ['_year', '_month']
    return list(
        grouped.values(*fields).annotate(
            ledger_count=Count('pk'),
            **{
                f'{reason}_count': Count('pk', filter=condition)
                for reason, condition in _eligibility_blockers().items()
            },
            excluded_count=Count(
                'pk',
                filter=Q(classification=TransactionClassification.EXCLUDED),
            ),
            confirmed_duplicate_count=Count(
                'pk',
                filter=Q(duplicate_resolution=DuplicateResolution.CONFIRMED_DUPLICATE),
            ),
            affected_count=Count(
                'pk',
                filter=_blocker_union(_eligibility_blockers()),
            ),
        )
    )


def _raw_from_group(row):
    names = (
        'income',
        'needs',
        'wants',
        'savings_contributions',
        'savings_withdrawals',
        'net_savings_activity',
    )
    raw = {}
    for name in names:
        raw[name] = row.get(f'_sum_{name}', ZERO) or ZERO
        raw[f'{name}_count'] = row.get(f'_sum_{name}_count', 0) or 0
    return raw


def _completeness_from_group(row):
    blocker_names = _eligibility_blockers()
    affected = row.get('affected_count', 0) or 0
    return {
        'is_incomplete': bool(affected),
        'affected_transaction_count': affected,
        'blockers': {
            reason: {'transaction_count': row.get(f'{reason}_count') or 0}
            for reason in sorted(blocker_names)
            if row.get(f'{reason}_count')
        },
        'excluded': {
            key: row[field]
            for key, field in (
                ('excluded', 'excluded_count'),
                ('confirmed_duplicate', 'confirmed_duplicate_count'),
            )
            if row.get(field)
        },
    }


def _monthly_rows(transactions, year, targets):
    summaries = {
        row['_month']: row
        for row in _grouped_values(transactions, 'month')
    }
    completeness = {
        row['_month']: row
        for row in _period_map_rows(transactions, 'month')
    }
    rows = []
    for month in range(1, 13):
        month_start, month_end = monthly_bounds(year, month)
        summary_row = summaries.get(month, {})
        completeness_row = completeness.get(month, {})
        item = {
            'period': {
                'year': year,
                'month': month,
                'start_date': month_start.isoformat(),
                'end_date': (month_end - date.resolution).isoformat(),
            },
            'summary': _complete_summary(_raw_from_group(summary_row), targets),
            'data_completeness': _completeness_from_group(completeness_row),
            'ledger_transaction_count': completeness_row.get('ledger_count', 0) or 0,
        }
        rows.append(item)
    return rows


def year_report(year, targets):
    start, end = year_bounds(year)
    transactions = _period_data(start, end)
    report = _report(start, end, targets, transactions, ranked=True)
    report['period'] = {
        'year': year,
        'start_date': start.isoformat(),
        'end_date': date(year, 12, 31).isoformat(),
    }
    report['months'] = _monthly_rows(transactions, year, targets)
    report['monthly_series'] = _trend_series(report['months'], targets)
    return report


def _trend_series(months, targets):
    return [{
        'year': item['period']['year'],
        'month': item['period']['month'],
        'label': f"{item['period']['year']}-{item['period']['month']:02d}",
        'has_transactions': item['ledger_transaction_count'] > 0,
        'is_incomplete': item['data_completeness']['is_incomplete'],
        'affected_transaction_count': item['data_completeness']['affected_transaction_count'],
        'income': item['summary']['income']['amount'],
        'needs': item['summary']['needs']['amount'],
        'wants': item['summary']['wants']['amount'],
        'savings_contributions': item['summary']['savings_contributions']['amount'],
        'savings_withdrawals': item['summary']['savings_withdrawals']['amount'],
        'net_savings_activity': item['summary']['net_savings_activity']['amount'],
        'remaining': item['summary']['remaining']['amount'],
        'needs_percent': item['summary']['needs']['actual_percent'],
        'wants_percent': item['summary']['wants']['actual_percent'],
        'savings_percent': item['summary']['savings_contributions']['actual_percent'],
        'current_targets': {
            'needs_percent': percentage(targets.needs_percent),
            'wants_percent': percentage(targets.wants_percent),
            'savings_percent': percentage(targets.savings_percent),
        },
    } for item in months]


def all_time_report(targets):
    earliest = Transaction.objects.order_by('date').values_list('date', flat=True).first()
    latest = Transaction.objects.order_by('-date').values_list('date', flat=True).first()
    if earliest is None:
        report = _report(None, None, targets, Transaction.objects.none(), ranked=True)
        report['period'] = {'start_date': None, 'end_date': None}
        report['years'] = []
        report['months'] = []
        report['monthly_series'] = []
        return report

    start = date(earliest.year, earliest.month, 1)
    end = date(latest.year + (latest.month == 12), 1 if latest.month == 12 else latest.month + 1, 1)
    transactions = _period_data(start, end)
    report = _report(start, end, targets, transactions, ranked=True)
    report['period'] = {
        'start_date': earliest.isoformat(),
        'end_date': latest.isoformat(),
    }
    year_rows = {
        row['_year']: row
        for row in _grouped_values(transactions, 'year')
    }
    year_completeness = {
        row['_year']: row
        for row in _period_map_rows(transactions, 'year')
    }
    report['years'] = [{
        'year': year,
        'summary': _complete_summary(
            _raw_from_group(year_rows.get(year, {})),
            targets,
        ),
        'data_completeness': _completeness_from_group(
            year_completeness.get(year, {}),
        ),
        'ledger_transaction_count': year_completeness.get(year, {}).get('ledger_count', 0),
    } for year in range(earliest.year, latest.year + 1)]
    month_rows = _all_time_months(transactions, earliest, latest, targets)
    report['months'] = month_rows
    report['monthly_series'] = _trend_series(month_rows, targets)
    return report


def _all_time_months(transactions, earliest, latest, targets):
    summaries = {
        (row['_year'], row['_month']): row
        for row in _grouped_values(transactions, 'month')
    }
    completeness = {
        (row['_year'], row['_month']): row
        for row in _period_map_rows(transactions, 'month')
    }
    months = []
    year, month = earliest.year, earliest.month
    while (year, month) <= (latest.year, latest.month):
        summary_row = summaries.get((year, month), {})
        completeness_row = completeness.get((year, month), {})
        start, end = monthly_bounds(year, month)
        months.append({
            'period': {
                'year': year,
                'month': month,
                'start_date': start.isoformat(),
                'end_date': (end - date.resolution).isoformat(),
            },
            'summary': _complete_summary(_raw_from_group(summary_row), targets),
            'data_completeness': _completeness_from_group(completeness_row),
            'ledger_transaction_count': completeness_row.get('ledger_count', 0) or 0,
        })
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return months


def category_trend(subcategory_id, scope, targets, year=None, month=None):
    category = Transaction.objects.filter(subcategory_id=subcategory_id).select_related(
        'subcategory'
    ).first()
    if category is None:
        return None
    if scope == 'year':
        start, end = year_bounds(year)
        periods = [(year, month) for month in range(1, 13)]
    elif scope == 'month':
        start, end = monthly_bounds(year, month)
        periods = [(year, month)]
    else:
        earliest = Transaction.objects.order_by('date').values_list('date', flat=True).first()
        latest = Transaction.objects.order_by('-date').values_list('date', flat=True).first()
        if earliest is None:
            return {
                'subcategory': subcategory_id,
                'subcategory_name': category.subcategory.name,
                'scope': {'type': 'all'},
                'series': [],
            }
        start = date(earliest.year, earliest.month, 1)
        end = date(latest.year + (latest.month == 12), 1 if latest.month == 12 else latest.month + 1, 1)
        periods = []
        current = (earliest.year, earliest.month)
        while current <= (latest.year, latest.month):
            periods.append(current)
            current = (
                (current[0] + 1, 1)
                if current[1] == 12
                else (current[0], current[1] + 1)
            )
    transactions = _period_data(start, end).filter(subcategory_id=subcategory_id)
    rows = (
        _eligible_transactions(transactions)
        .filter(_expense_condition())
        .annotate(_year=ExtractYear('date'), _month=ExtractMonth('date'))
        .values('_year', '_month', 'budget_bucket')
        .annotate(amount=Sum(-F('amount'), output_field=MONEY_FIELD), count=Count('pk'))
    )
    grouped = {
        (row['_year'], row['_month'], row['budget_bucket']): row
        for row in rows
    }
    completeness = {
        (row['_year'], row['_month']): row
        for row in _period_map_rows(_period_data(start, end), 'month')
    }
    return {
        'subcategory': subcategory_id,
        'subcategory_name': category.subcategory.name,
        'scope': {
            'type': scope,
            **({'year': year} if scope in ('year', 'month') else {}),
            **({'month': month} if scope == 'month' else {}),
        },
        'series': [{
            'year': period_year,
            'month': period_month,
            'label': f'{period_year}-{period_month:02d}',
            'is_incomplete': bool(completeness.get((period_year, period_month), {}).get('affected_count')),
            'affected_transaction_count': completeness.get((period_year, period_month), {}).get('affected_count', 0),
            'buckets': {
                bucket: {
                    'amount': money(grouped.get((period_year, period_month, bucket), {}).get('amount', ZERO)),
                    'transaction_count': grouped.get((period_year, period_month, bucket), {}).get('count', 0),
                }
                for bucket in (BudgetBucket.NEEDS, BudgetBucket.WANTS)
            },
        } for period_year, period_month in periods],
    }


def drilldown_queryset(start, end, metric, subcategory_id=None):
    transactions = _eligible_transactions(_period_data(start, end))
    conditions = {
        'income': Q(classification=TransactionClassification.INCOME),
        'needs': _expense_condition(BudgetBucket.NEEDS),
        'wants': _expense_condition(BudgetBucket.WANTS),
        'savings_contributions': Q(
            classification=TransactionClassification.TRANSFER,
            savings_effect=SavingsEffect.CONTRIBUTION,
        ),
        'savings_withdrawals': Q(
            classification=TransactionClassification.TRANSFER,
            savings_effect=SavingsEffect.WITHDRAWAL,
        ),
        'net_savings_activity': Q(
            classification=TransactionClassification.TRANSFER,
            savings_effect__in=(SavingsEffect.CONTRIBUTION, SavingsEffect.WITHDRAWAL),
        ),
        'remaining': (
            Q(classification=TransactionClassification.INCOME)
            | _expense_condition()
            | Q(
                classification=TransactionClassification.TRANSFER,
                savings_effect=SavingsEffect.CONTRIBUTION,
            )
        ),
    }
    if metric not in conditions:
        raise ValueError('Choose a valid report metric.')
    transactions = transactions.filter(conditions[metric])
    if subcategory_id is not None:
        transactions = transactions.filter(subcategory_id=subcategory_id)
    return (
        transactions
        .annotate(_contribution=_contribution_expression(metric))
        .select_related('account', 'subcategory')
        .order_by('date', 'id')
    )


def serialize_drilldown_row(record):
    return {
        'id': record['id'],
        'date': record['date'].isoformat(),
        'description': record['merchant'] or record['raw_description'],
        'raw_description': record['raw_description'],
        'account': record['account__name'],
        'classification': record['classification'],
        'savings_effect': record['savings_effect'],
        'subcategory': record['subcategory__name'],
        'amount': money(record['amount']),
        'contribution': money(record['_contribution']),
    }


def drilldown_rows(report, metric, subcategory_id=None):
    rows = drilldown_queryset(report['_start'], report['_end'], metric, subcategory_id).values(
        'id',
        'date',
        'merchant',
        'raw_description',
        'account__name',
        'classification',
        'savings_effect',
        'subcategory__name',
        'amount',
        '_contribution',
    )
    return [
        serialize_drilldown_row(row)
        for row in rows
    ]
