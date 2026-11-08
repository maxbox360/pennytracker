import re
from datetime import date
from django.core.paginator import Paginator
from django.db.models import Max

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q, TextField, Value
from django.db.models.functions import Coalesce, NullIf
from django.utils.dateparse import parse_date
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet
from .csv_imports import (
    DuplicateImportError,
    ImportFileError,
    MAX_UPLOAD_BYTES,
    commit_import,
    file_hash,
    overlap_results,
    parse_csv,
)
from .categorization_rules import (
    apply_rule_to_transaction,
    evaluate_import_rows,
    evaluate_rules,
    pending_matches_for_rule,
    rules_for_evaluation,
)
from .models import (
    Account,
    BudgetBucket,
    BudgetTargetSettings,
    CategorizationRule,
    DuplicateResolution,
    ImportBatch,
    ReviewStatus,
    Subcategory,
    SubcategoryScope,
    Transaction,
    TransactionClassification,
)
from .serializers import (
    AccountSerializer,
    BudgetTargetSettingsSerializer,
    CategorizationRuleSerializer,
    ImportBatchSerializer,
    SubcategorySerializer,
    TransactionSerializer,
)
from .accounting import (
    all_time_report,
    category_trend,
    drilldown_queryset,
    monthly_bounds,
    monthly_report,
    serialize_drilldown_row,
    year_bounds,
    year_report,
)


class AccountViewSet(ModelViewSet):
    queryset = Account.objects.all()
    serializer_class = AccountSerializer


@api_view(['GET', 'PUT'])
def budget_target_settings(request):
    settings_record = BudgetTargetSettings.objects.get(pk=1)
    if request.method == 'GET':
        return Response(BudgetTargetSettingsSerializer(settings_record).data)
    serializer = BudgetTargetSettingsSerializer(
        settings_record,
        data=request.data,
        partial=True,
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)


def report_period(request):
    year = request.query_params.get('year')
    month = request.query_params.get('month')
    if bool(year) != bool(month):
        raise ValidationError({'period': 'Provide both year and month, or neither.'})
    if year and month:
        if not year.isdecimal() or not month.isdecimal():
            raise ValidationError({'period': 'Use numeric year and month values.'})
        year_value, month_value = int(year), int(month)
        if not 1 <= month_value <= 12 or not 1 <= year_value <= 9999:
            raise ValidationError({'period': 'Choose a valid calendar month.'})
        return year_value, month_value
    latest = Transaction.objects.aggregate(latest=Max('date'))['latest']
    selected = latest or date.today()
    return selected.year, selected.month


@api_view(['GET'])
def monthly_report_view(request):
    year, month = report_period(request)
    targets = BudgetTargetSettings.objects.get(pk=1)
    result = monthly_report(year, month, targets)
    result.pop('_start', None)
    result.pop('_end', None)
    return Response(result)


@api_view(['GET'])
def all_time_report_view(request):
    result = all_time_report(BudgetTargetSettings.objects.get(pk=1))
    for key in ('_start', '_end'):
        result.pop(key, None)
    return Response(result)


@api_view(['GET'])
def yearly_report_view(request):
    year_value = request.query_params.get('year')
    if not year_value or not year_value.isdecimal() or not 1 <= int(year_value) <= 9998:
        raise ValidationError({'year': 'Choose a valid calendar year.'})
    result = year_report(int(year_value), BudgetTargetSettings.objects.get(pk=1))
    for key in ('_start', '_end'):
        result.pop(key, None)
    return Response(result)


def historical_drilldown(request, scope):
    if scope == 'month':
        year, month = report_period(request)
        start = date(year, month, 1)
        end = monthly_bounds(year, month)[1]
    elif scope == 'year':
        year_value = request.query_params.get('year')
        if not year_value or not year_value.isdecimal() or not 1 <= int(year_value) <= 9998:
            raise ValidationError({'year': 'Choose a valid calendar year.'})
        start, end = year_bounds(int(year_value))
    else:
        start = end = None

    metric = request.query_params.get('metric', '')
    subcategory_value = request.query_params.get('subcategory')
    subcategory_id = None
    if subcategory_value is not None:
        if subcategory_value == 'uncategorized':
            subcategory_id = 'uncategorized'
        elif subcategory_value.isdecimal():
            subcategory_id = int(subcategory_value)
        else:
            raise ValidationError({'subcategory': 'Use a subcategory ID or "uncategorized".'})
    try:
        rows = drilldown_queryset(start, end, metric, subcategory_id).values(
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
    except ValueError as error:
        raise ValidationError({'metric': str(error)}) from error
    page = Paginator(rows, 100).get_page(request.query_params.get('page', 1))
    return Response({
        'count': rows.count(),
        'next': page.next_page_number() if page.has_next() else None,
        'previous': page.previous_page_number() if page.has_previous() else None,
        'results': [
            serialize_drilldown_row(row)
            for row in page.object_list
        ],
    })


@api_view(['GET'])
def historical_report_drilldown(request):
    scope = request.query_params.get('scope', '')
    if scope not in ('all', 'year', 'month'):
        raise ValidationError({'scope': 'Choose all, year, or month.'})
    return historical_drilldown(request, scope)


@api_view(['GET'])
def monthly_report_drilldown(request):
    return historical_drilldown(request, 'month')


@api_view(['GET'])
def category_trend_view(request):
    value = request.query_params.get('subcategory', '')
    if not value.isdecimal():
        raise ValidationError({'subcategory': 'Use a subcategory ID.'})
    scope = request.query_params.get('scope', 'all')
    if scope not in ('all', 'year', 'month'):
        raise ValidationError({'scope': 'Choose all, year, or month.'})
    year = None
    month = None
    if scope in ('year', 'month'):
        year_value = request.query_params.get('year', '')
        if not year_value.isdecimal() or not 1 <= int(year_value) <= 9998:
            raise ValidationError({'year': 'Choose a valid calendar year.'})
        year = int(year_value)
    if scope == 'month':
        month_value = request.query_params.get('month', '')
        if not month_value.isdecimal() or not 1 <= int(month_value) <= 12:
            raise ValidationError({'month': 'Choose a valid calendar month.'})
        month = int(month_value)
    result = category_trend(
        int(value),
        scope,
        BudgetTargetSettings.objects.get(pk=1),
        year,
        month,
    )
    if result is None:
        raise ValidationError({'subcategory': 'Subcategory not found.'})
    return Response(result)


class SubcategoryViewSet(ModelViewSet):
    queryset = Subcategory.objects.all()
    serializer_class = SubcategorySerializer

    @action(detail=False, methods=['post'], url_path='bulk-create')
    def bulk_create(self, request):
        raw_names = request.data.get('names')
        if not isinstance(raw_names, str):
            raise ValidationError({'names': 'Enter subcategory names as text.'})

        names = [name.strip() for name in re.split(r'[,\n]', raw_names) if name.strip()]
        if not names:
            raise ValidationError({'names': 'Enter at least one subcategory name.'})

        scope = request.data.get('scope', SubcategoryScope.BUDGET)
        existing = list(Subcategory.objects.filter(scope=scope))
        active = {item.name.strip().casefold(): item for item in existing if item.is_active}
        archived = {item.name.strip().casefold(): item for item in existing if not item.is_active}
        seen = set()
        serializers = []
        invalid = []
        already_exists = []
        archived_matches = []
        duplicate_count = 0
        for name in names:
            normalized_name = name.casefold()
            if normalized_name in seen:
                duplicate_count += 1
                continue
            seen.add(normalized_name)
            if normalized_name in active:
                already_exists.append(active[normalized_name].name)
                continue
            if normalized_name in archived:
                archived_matches.append(archived[normalized_name].name)
                continue

            serializer = SubcategorySerializer(data={
                'name': name,
                'scope': scope,
                'budget_bucket': request.data.get('budget_bucket') if scope == SubcategoryScope.BUDGET else None,
            })
            if not serializer.is_valid():
                messages = [
                    str(message)
                    for field_errors in serializer.errors.values()
                    for message in field_errors
                ]
                invalid.append({'name': name, 'message': ' '.join(messages)})
                continue
            serializers.append(serializer)

        if invalid:
            raise ValidationError({'invalid': invalid})

        with transaction.atomic():
            categories = [serializer.save() for serializer in serializers]

        return Response(
            {
                'count': len(categories),
                'created': SubcategorySerializer(categories, many=True).data,
                'already_exists': already_exists,
                'archived': archived_matches,
                'duplicate_count': duplicate_count,
                'submitted_count': len(names),
            },
            status=status.HTTP_201_CREATED,
        )

    def get_queryset(self):
        queryset = super().get_queryset()
        active = self.request.query_params.get('is_active')
        if active in ('true', 'false'):
            queryset = queryset.filter(is_active=active == 'true')
        return queryset


class CategorizationRuleViewSet(ModelViewSet):
    serializer_class = CategorizationRuleSerializer
    queryset = CategorizationRule.objects.select_related('subcategory')

    def get_queryset(self):
        queryset = super().get_queryset()
        active = self.request.query_params.get('is_active')
        if active in ('true', 'false'):
            queryset = queryset.filter(is_active=active == 'true')
        return queryset

    def destroy(self, request, *args, **kwargs):
        rule = self.get_object()
        if rule.categorized_transactions.exists():
            return Response(
                {'detail': 'This rule has categorized transactions; disable it to preserve provenance.'},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['get'], url_path='pending-matches')
    def pending_matches(self, request, pk=None):
        rule = self.get_object()
        if not rule.is_active or not rule.subcategory.is_active:
            return Response({
                'matching_pending_count': 0,
                'conflicting_pending_count': 0,
            })
        matches, conflicts = pending_matches_for_rule(rule)
        return Response({
            'matching_pending_count': len(matches),
            'conflicting_pending_count': len(conflicts),
        })

    @action(detail=True, methods=['post'], url_path='apply-to-pending')
    def apply_to_pending(self, request, pk=None):
        try:
            with transaction.atomic():
                rule = (
                    CategorizationRule.objects.select_for_update()
                    .select_related('subcategory')
                    .get(pk=pk)
                )
                if not rule.is_active:
                    raise ValidationError({'detail': 'Enable this rule before applying it.'})
                if not rule.subcategory.is_active:
                    raise ValidationError({'detail': 'Reactivate the target subcategory before applying this rule.'})
                if rule.subcategory.budget_bucket not in (BudgetBucket.NEEDS, BudgetBucket.WANTS):
                    raise ValidationError({'detail': 'Expense rules require a Needs or Wants target subcategory.'})
                if rule.classification != TransactionClassification.EXPENSE:
                    raise ValidationError({'detail': 'Only Expense rules can be applied.'})
                rules = rules_for_evaluation()
                candidates = list(
                    Transaction.objects.select_for_update().filter(
                        review_status=ReviewStatus.PENDING,
                        classification=TransactionClassification.EXPENSE,
                        subcategory__isnull=True,
                    ).order_by('pk')
                )
                matches, conflicts = pending_matches_for_rule(rule, candidates=candidates, rules=rules)
                for transaction_record in matches:
                    result = evaluate_rules(
                        transaction_record.classification,
                        transaction_record.raw_description,
                        rules,
                    )
                    apply_rule_to_transaction(transaction_record, result['rule'])
        except CategorizationRule.DoesNotExist:
            return Response({'detail': 'Categorization rule not found.'}, status=status.HTTP_404_NOT_FOUND)
        except DjangoValidationError as error:
            return Response({'detail': error.message_dict}, status=status.HTTP_400_BAD_REQUEST)
        except ValidationError as error:
            return Response(error.detail, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            'updated_count': len(matches),
            'conflicting_count': len(conflicts),
        })


class TransactionPagination(PageNumberPagination):
    page_size = 100


class TransactionViewSet(ModelViewSet):
    serializer_class = TransactionSerializer
    pagination_class = TransactionPagination

    ordering_fields = {
        'date': 'date',
        'description': 'description_sort',
        'account': 'account__name',
        'source_type': 'source_type',
        'classification': 'classification',
        'subcategory': 'subcategory__name',
        'review_status': 'review_status',
        'amount': 'amount',
    }

    def destroy(self, request, *args, **kwargs):
        return Response(
            {'detail': 'Transactions are retained for audit; mark one EXCLUDED instead.'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @action(detail=True, methods=['post'], url_path='create-subcategory-and-assign')
    def create_subcategory_and_assign(self, request, pk=None):
        subcategory_serializer = SubcategorySerializer(data={
            'name': request.data.get('name', ''),
            'scope': request.data.get('scope', SubcategoryScope.BUDGET),
            'budget_bucket': request.data.get('budget_bucket') if request.data.get(
                'scope', SubcategoryScope.BUDGET
            ) == SubcategoryScope.BUDGET else None,
        })
        if not subcategory_serializer.is_valid():
            return Response(subcategory_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                transaction_record = self.get_object()
                subcategory = subcategory_serializer.save()
                transaction_data = {'subcategory': subcategory.pk}
                if 'classification' in request.data:
                    transaction_data['classification'] = request.data['classification']
                    if request.data['classification'] != TransactionClassification.TRANSFER:
                        transaction_data['savings_effect'] = Transaction.SavingsEffect.NONE
                transaction_serializer = TransactionSerializer(
                    transaction_record,
                    data=transaction_data,
                    partial=True,
                )
                transaction_serializer.is_valid(raise_exception=True)
                transaction_serializer.save()
        except DjangoValidationError as error:
            return Response({'detail': error.message_dict}, status=status.HTTP_400_BAD_REQUEST)
        except ValidationError as error:
            return Response(error.detail, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'transaction': transaction_serializer.data,
            'subcategory': SubcategorySerializer(subcategory).data,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='save-with-rule')
    def save_with_rule(self, request, pk=None):
        try:
            with transaction.atomic():
                transaction_record = self.get_object()
                transaction_serializer = TransactionSerializer(
                    transaction_record,
                    data={
                        key: request.data[key]
                        for key in ('classification', 'savings_effect', 'subcategory', 'merchant', 'notes')
                        if key in request.data
                    },
                    partial=True,
                )
                transaction_serializer.is_valid(raise_exception=True)
                transaction_record = transaction_serializer.save()
                if (
                    transaction_record.classification != TransactionClassification.EXPENSE
                    or transaction_record.subcategory_id is None
                ):
                    raise ValidationError({
                        'subcategory': 'Assign an Expense subcategory before creating a rule.'
                    })
                rule_serializer = CategorizationRuleSerializer(data={
                    'name': request.data.get('name', ''),
                    'match_field': request.data.get('match_field', 'RAW_DESCRIPTION'),
                    'match_type': request.data.get('match_type', 'CONTAINS'),
                    'match_value': request.data.get('match_value', ''),
                    'classification': TransactionClassification.EXPENSE,
                    'subcategory': transaction_record.subcategory_id,
                    'is_active': True,
                })
                rule_serializer.is_valid(raise_exception=True)
                rule = rule_serializer.save()
                matches, conflicts = pending_matches_for_rule(
                    rule,
                    exclude_ids=(transaction_record.pk,),
                )
        except DjangoValidationError as error:
            return Response({'detail': error.message_dict}, status=status.HTTP_400_BAD_REQUEST)
        except ValidationError as error:
            return Response(error.detail, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            'transaction': TransactionSerializer(transaction_record).data,
            'rule': CategorizationRuleSerializer(rule).data,
            'matching_pending_count': len(matches),
            'conflicting_pending_count': len(conflicts),
        }, status=status.HTTP_201_CREATED)

    def get_queryset(self):
        queryset = (
            Transaction.objects.select_related(
                'account',
                'subcategory',
                'duplicate_of',
            )
            .prefetch_related('duplicate_candidates')
        )
        params = self.request.query_params
        review_status = params.get('review_status')
        if review_status:
            if review_status not in ReviewStatus.values:
                raise ValidationError({'review_status': 'Choose a valid review status.'})
            queryset = queryset.filter(review_status=review_status)

        classification = params.get('classification')
        if classification:
            if classification not in TransactionClassification.values:
                raise ValidationError({'classification': 'Choose a valid classification.'})
            queryset = queryset.filter(classification=classification)

        source_type = params.get('source_type')
        if source_type:
            queryset = queryset.filter(source_type__icontains=source_type.strip())

        subcategory = params.get('subcategory')
        if subcategory == 'uncategorized':
            queryset = queryset.filter(subcategory__isnull=True)
        elif subcategory:
            if not subcategory.isdecimal():
                raise ValidationError({'subcategory': 'Use a subcategory ID or "uncategorized".'})
            queryset = queryset.filter(subcategory_id=subcategory)

        budget_bucket = params.get('budget_bucket')
        if budget_bucket:
            if budget_bucket not in ('NEEDS', 'WANTS', 'SAVINGS'):
                raise ValidationError({'budget_bucket': 'Choose Needs, Wants, or Savings.'})
            queryset = queryset.filter(budget_bucket=budget_bucket)

        account_id = params.get('account')
        if account_id:
            if not account_id.isdecimal():
                raise ValidationError({'account': 'Account must be an integer ID.'})
            queryset = queryset.filter(account_id=account_id)

        for param, lookup in (('start_date', 'date__gte'), ('end_date', 'date__lte')):
            value = params.get(param)
            if value:
                parsed = parse_date(value)
                if parsed is None:
                    raise ValidationError({param: 'Use YYYY-MM-DD format.'})
                queryset = queryset.filter(**{lookup: parsed})

        review_reason = params.get('review_reason')
        if review_reason:
            if review_reason == 'UNKNOWN':
                queryset = queryset.filter(classification=TransactionClassification.UNKNOWN)
            elif review_reason == 'UNCATEGORIZED':
                queryset = queryset.filter(
                    classification__in=(
                        TransactionClassification.EXPENSE,
                        TransactionClassification.REFUND,
                    ),
                    subcategory__isnull=True,
                )
            elif review_reason == 'POSSIBLE_DUPLICATE':
                queryset = queryset.filter(duplicate_resolution=DuplicateResolution.UNRESOLVED)
            elif review_reason == 'TRANSFER':
                queryset = queryset.filter(classification=TransactionClassification.TRANSFER)
            elif review_reason == 'RULE_CONFLICT':
                queryset = queryset.filter(categorization_rule_conflict=True)
            else:
                raise ValidationError({'review_reason': 'Choose a valid review reason.'})

        search = params.get('search')
        if search:
            queryset = queryset.filter(
                Q(raw_description__icontains=search)
                | Q(merchant__icontains=search)
                | Q(notes__icontains=search)
            )

        ordering = params.get('ordering', '-date')
        descending = ordering.startswith('-')
        field = ordering[1:] if descending else ordering
        if field not in self.ordering_fields:
            raise ValidationError({
                'ordering': f'Choose one of: {", ".join(self.ordering_fields)}; prefix with - for descending.'
            })
        if field == 'description':
            queryset = queryset.annotate(
                description_sort=Coalesce(
                    NullIf('merchant', Value('')),
                    'raw_description',
                    output_field=TextField(),
                )
            )
        order_expression = self.ordering_fields[field]
        if descending:
            order_expression = f'-{order_expression}'
        tie_breaker = '-id' if descending else 'id'
        return queryset.order_by(order_expression, tie_breaker)

    @action(detail=True, methods=['post'], url_path='mark-reviewed')
    def mark_reviewed(self, request, pk=None):
        transaction_record = self.get_object()
        try:
            transaction_record.mark_reviewed()
        except DjangoValidationError as error:
            return Response({'detail': error.message_dict}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(transaction_record).data)

    @action(detail=True, methods=['post'], url_path='resolve-duplicate')
    def resolve_duplicate(self, request, pk=None):
        transaction_record = self.get_object()
        resolution = request.data.get('resolution')
        if resolution not in (DuplicateResolution.DISTINCT, DuplicateResolution.CONFIRMED_DUPLICATE):
            return Response(
                {'detail': 'Resolution must be DISTINCT or CONFIRMED_DUPLICATE.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if transaction_record.duplicate_resolution != DuplicateResolution.UNRESOLVED:
            return Response(
                {'detail': 'This transaction does not have an unresolved possible duplicate.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        transaction_record.duplicate_resolution = resolution
        fields = {'duplicate_resolution', 'review_status', 'updated_at'}
        if resolution == DuplicateResolution.DISTINCT:
            transaction_record.duplicate_of = None
            fields.add('duplicate_of')
        else:
            candidate_id = request.data.get('duplicate_of')
            if not str(candidate_id or '').isdecimal():
                return Response(
                    {'detail': 'Select the existing transaction that this duplicates.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not transaction_record.duplicate_candidates.filter(pk=candidate_id).exists():
                return Response(
                    {'detail': 'The selected transaction is not one of the listed candidates.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            transaction_record.duplicate_of_id = int(candidate_id)
            fields.add('duplicate_of')
        try:
            transaction_record.save(update_fields=fields)
        except DjangoValidationError as error:
            return Response({'detail': error.message_dict}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(transaction_record).data)

    @action(detail=False, methods=['post'], url_path='bulk')
    def bulk(self, request):
        ids = request.data.get('transaction_ids')
        operation = request.data.get('operation')
        if not isinstance(ids, list) or not ids or any(not str(value).isdecimal() for value in ids):
            return Response(
                {'detail': 'Provide a non-empty list of transaction_ids.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ids = list(dict.fromkeys(int(value) for value in ids))
        try:
            with transaction.atomic():
                records = list(
                    Transaction.objects.select_for_update()
                    .filter(pk__in=ids)
                    .select_related('subcategory')
                    .order_by('pk')
                )
                if len(records) != len(ids):
                    raise ValidationError({'transaction_ids': 'One or more selected transactions do not exist.'})

                if operation == 'classification':
                    classification = request.data.get('classification')
                    if classification not in TransactionClassification.values:
                        raise ValidationError({'classification': 'Choose a valid classification.'})
                    for record in records:
                        record.classification = classification
                        if classification != TransactionClassification.TRANSFER:
                            record.savings_effect = Transaction.SavingsEffect.NONE
                        record.save(update_fields=(
                            'classification',
                            'savings_effect',
                            'subcategory',
                            'budget_bucket',
                            'review_status',
                            'updated_at',
                        ))
                elif operation == 'subcategory':
                    subcategory_id = request.data.get('subcategory')
                    if not str(subcategory_id or '').isdecimal():
                        raise ValidationError({'subcategory': 'Select an active subcategory.'})
                    try:
                        subcategory = Subcategory.objects.get(pk=subcategory_id, is_active=True)
                    except Subcategory.DoesNotExist as error:
                        raise ValidationError({'subcategory': 'Select an active subcategory.'}) from error
                    for record in records:
                        if not Transaction.subcategory_matches_classification(
                            subcategory,
                            record.classification,
                        ):
                            raise ValidationError({
                                'subcategory': f'The subcategory is not valid for transaction {record.pk}.'
                            })
                    for record in records:
                        record.subcategory = subcategory
                        record.categorized_by_rule = None
                        record.save(update_fields=(
                            'subcategory',
                            'budget_bucket',
                            'categorized_by_rule',
                            'categorization_rule_conflict',
                            'review_status',
                            'updated_at',
                        ))
                elif operation == 'mark_reviewed':
                    blocked = {
                        record.pk: record.review_blockers()
                        for record in records
                        if record.review_blockers()
                    }
                    if blocked:
                        raise ValidationError({
                            'transaction_ids': f'These transactions still need attention: {blocked}.'
                        })
                    for record in records:
                        record.mark_reviewed()
                else:
                    raise ValidationError({
                        'operation': 'Use classification, subcategory, or mark_reviewed.'
                    })
        except DjangoValidationError as error:
            return Response({'detail': error.message_dict}, status=status.HTTP_400_BAD_REQUEST)
        except ValidationError as error:
            return Response(error.detail, status=status.HTTP_400_BAD_REQUEST)

        return Response({'updated_count': len(records)})


class ImportBatchViewSet(ReadOnlyModelViewSet):
    queryset = ImportBatch.objects.select_related('account')
    serializer_class = ImportBatchSerializer


def read_import_request(request):
    account_id = request.data.get('account')
    uploaded_file = request.FILES.get('file')
    if not account_id or uploaded_file is None:
        return None, None, None, Response(
            {'detail': 'Select an account and a CSV file.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        account = Account.objects.get(pk=int(account_id), is_active=True)
    except (TypeError, ValueError, Account.DoesNotExist):
        return None, None, None, Response(
            {'detail': 'Select an existing active account.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if uploaded_file.size > MAX_UPLOAD_BYTES:
        return None, None, None, Response(
            {'detail': 'CSV files must be 20 MiB or smaller.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if len(uploaded_file.name) > 255:
        return None, None, None, Response(
            {'detail': 'The filename must be 255 characters or fewer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return account, uploaded_file.name, uploaded_file.read(), None


@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
def preview_import(request):
    account, filename, content, error_response = read_import_request(request)
    if error_response is not None:
        return error_response
    try:
        parsed = parse_csv(content)
    except ImportFileError as error:
        return Response({'detail': str(error)}, status=status.HTTP_400_BAD_REQUEST)

    digest = file_hash(content)
    already_imported = ImportBatch.objects.filter(account=account, file_sha256=digest).exists()
    rows = evaluate_import_rows(overlap_results(account, parsed.rows))
    new_count = sum(row['overlap_status'] == 'NEW' for row in rows)
    represented_count = sum(row['overlap_status'] == 'ALREADY_REPRESENTED' for row in rows)
    possible_count = sum(row['overlap_status'] == 'POSSIBLE_DUPLICATE' for row in rows)
    review_count = sum(_import_row_needs_review(row) for row in rows)
    return Response({
        'account': {'id': account.id, 'name': account.name},
        'filename': filename,
        'format_identifier': parsed.format_identifier,
        'source_row_count': parsed.source_row_count,
        'valid_row_count': len(parsed.rows),
        'invalid_row_count': len(parsed.errors),
        'new_row_count': new_count,
        'already_represented_row_count': represented_count,
        'possible_duplicate_row_count': possible_count,
        'review_required_row_count': review_count,
        'earliest_transaction_date': min(row['date'] for row in parsed.rows).isoformat()
        if parsed.rows else None,
        'latest_transaction_date': max(row['date'] for row in parsed.rows).isoformat()
        if parsed.rows else None,
        'valid_rows': [{
            'source_row': row['source_row'],
            'date': row['date'].isoformat(),
            'posted_date': None,
            'raw_description': row['raw_description'],
            'amount': format(row['amount'], '.2f'),
            'source_type': row['source_type'],
            'classification': row['classification'],
            'overlap_status': row['overlap_status'],
            'overlap_reason': row['overlap_reason'],
            'categorization': _preview_categorization(row),
            'duplicate_candidates': [{
                'id': candidate.id,
                'date': candidate.date.isoformat(),
                'raw_description': candidate.raw_description,
                'merchant': candidate.merchant,
                'amount': format(candidate.amount, '.2f'),
                'source_type': candidate.source_type,
                'account': candidate.account.name,
            } for candidate in row['duplicate_candidates']],
        } for row in rows],
        'invalid_rows': parsed.errors,
        'already_imported': already_imported,
        'can_import': not parsed.errors and not already_imported,
    })


def _import_row_needs_review(row):
    if row['overlap_status'] == 'ALREADY_REPRESENTED':
        return False
    if row['overlap_status'] == 'POSSIBLE_DUPLICATE':
        return True
    classification = row['classification']
    if classification in (
        TransactionClassification.UNKNOWN,
        TransactionClassification.REFUND,
        TransactionClassification.TRANSFER,
    ):
        return True
    return (
        classification == TransactionClassification.EXPENSE
        and (
            row['_categorization']['subcategory'] is None
            or row['_categorization']['conflict']
        )
    )


def _preview_categorization(row):
    result = row['_categorization']
    if result['conflict']:
        status_value = 'RULE_CONFLICT'
    elif result['subcategory']:
        status_value = 'AUTO_CATEGORIZED'
    else:
        status_value = 'NOT_CATEGORIZED'
    return {
        'status': status_value,
        'subcategory': (
            {
                'id': result['subcategory'].id,
                'name': result['subcategory'].name,
                'budget_bucket': result['subcategory'].budget_bucket,
            }
            if result['subcategory'] else None
        ),
        'rules': [{
            'id': rule.id,
            'match_value': rule.match_value,
            'subcategory_name': rule.subcategory.name,
        } for rule in result['rules']],
    }


@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
def confirm_import(request):
    account, filename, content, error_response = read_import_request(request)
    if error_response is not None:
        return error_response
    try:
        parsed = parse_csv(content)
        batch = commit_import(account, filename, content, parsed)
    except DuplicateImportError as error:
        return Response({'detail': str(error)}, status=status.HTTP_409_CONFLICT)
    except ImportFileError as error:
        return Response({'detail': str(error)}, status=status.HTTP_400_BAD_REQUEST)

    return Response({
        'batch_id': batch.id,
        'filename': batch.original_filename,
        'account': account.name,
        'source_row_count': batch.source_row_count,
        'imported_row_count': batch.imported_row_count,
        'already_represented_row_count': batch.already_represented_row_count,
        'possible_duplicate_row_count': batch.possible_duplicate_row_count,
        'earliest_transaction_date': batch.earliest_transaction_date.isoformat(),
        'latest_transaction_date': batch.latest_transaction_date.isoformat(),
    }, status=status.HTTP_201_CREATED)
