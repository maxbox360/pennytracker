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
    parse_csv,
)
from .models import Account, ImportBatch, Subcategory, Transaction
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
    queryset = Transaction.objects.select_related('account', 'subcategory')
    serializer_class = TransactionSerializer


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
    return Response({
        'account': {'id': account.id, 'name': account.name},
        'filename': filename,
        'format_identifier': parsed.format_identifier,
        'source_row_count': parsed.source_row_count,
        'valid_row_count': len(parsed.rows),
        'invalid_row_count': len(parsed.errors),
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
        } for row in parsed.rows],
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
        'earliest_transaction_date': batch.earliest_transaction_date.isoformat(),
        'latest_transaction_date': batch.latest_transaction_date.isoformat(),
    }, status=status.HTTP_201_CREATED)
