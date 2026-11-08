from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_date
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
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
from .models import (
    Account,
    DuplicateResolution,
    ImportBatch,
    ReviewStatus,
    Subcategory,
    Transaction,
    TransactionClassification,
)
from .serializers import (
    AccountSerializer,
    ImportBatchSerializer,
    SubcategorySerializer,
    TransactionSerializer,
)


class AccountViewSet(ModelViewSet):
    queryset = Account.objects.all()
    serializer_class = AccountSerializer


class SubcategoryViewSet(ModelViewSet):
    queryset = Subcategory.objects.all()
    serializer_class = SubcategorySerializer


class TransactionViewSet(ModelViewSet):
    serializer_class = TransactionSerializer

    def destroy(self, request, *args, **kwargs):
        return Response(
            {'detail': 'Transactions are retained for audit; mark one EXCLUDED instead.'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def get_queryset(self):
        queryset = (
            Transaction.objects.select_related('account', 'subcategory', 'duplicate_of')
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
            else:
                raise ValidationError({'review_reason': 'Choose a valid review reason.'})

        search = params.get('search')
        if search:
            queryset = queryset.filter(
                Q(raw_description__icontains=search)
                | Q(merchant__icontains=search)
                | Q(notes__icontains=search)
            )
        return queryset

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
                        record.save(update_fields=(
                            'classification',
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
                        if subcategory.budget_bucket not in Transaction.eligible_buckets(record.classification):
                            raise ValidationError({
                                'subcategory': f'The subcategory is not valid for transaction {record.pk}.'
                            })
                    for record in records:
                        record.subcategory = subcategory
                        record.save(update_fields=(
                            'subcategory',
                            'budget_bucket',
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
    rows = overlap_results(account, parsed.rows)
    new_count = sum(row['overlap_status'] == 'NEW' for row in rows)
    represented_count = sum(row['overlap_status'] == 'ALREADY_REPRESENTED' for row in rows)
    possible_count = sum(row['overlap_status'] == 'POSSIBLE_DUPLICATE' for row in rows)
    review_count = sum(
        row['overlap_status'] != 'ALREADY_REPRESENTED'
        and (
            row['overlap_status'] == 'POSSIBLE_DUPLICATE'
            or row['classification'] in (
                TransactionClassification.UNKNOWN,
                TransactionClassification.EXPENSE,
                TransactionClassification.REFUND,
                TransactionClassification.TRANSFER,
            )
        )
        for row in rows
    )
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
