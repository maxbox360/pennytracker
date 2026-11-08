from rest_framework import serializers
from .models import Account, ImportBatch, Subcategory, Transaction


class ImportBatchSerializer(serializers.ModelSerializer):
    account_name = serializers.CharField(source='account.name', read_only=True)

    class Meta:
        model = ImportBatch
        fields = (
            'id',
            'account',
            'account_name',
            'original_filename',
            'format_identifier',
            'source_row_count',
            'imported_row_count',
            'earliest_transaction_date',
            'latest_transaction_date',
            'imported_at',
        )


class AccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = Account
        fields = (
            'id',
            'name',
            'institution',
            'account_type',
            'is_savings_destination',
            'is_active',
        )


class SubcategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Subcategory
        fields = ('id', 'name', 'budget_bucket', 'is_active')


class TransactionSerializer(serializers.ModelSerializer):
    account_name = serializers.CharField(source='account.name', read_only=True)
    subcategory_name = serializers.CharField(
        source='subcategory.name',
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = Transaction
        fields = (
            'id',
            'account',
            'account_name',
            'subcategory',
            'subcategory_name',
            'date',
            'posted_date',
            'raw_description',
            'merchant',
            'amount',
            'source_type',
            'import_batch',
            'source_row',
            'classification',
            'budget_bucket',
            'notes',
            'created_at',
            'updated_at',
        )
        read_only_fields = (
            'budget_bucket',
            'source_type',
            'import_batch',
            'source_row',
            'created_at',
            'updated_at',
        )
