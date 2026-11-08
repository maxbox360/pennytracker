from rest_framework import serializers
from .models import (
    Account,
    ImportBatch,
    Subcategory,
    Transaction,
    TransactionClassification,
)


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
            'already_represented_row_count',
            'possible_duplicate_row_count',
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


class DuplicateCandidateSerializer(serializers.ModelSerializer):
    account_name = serializers.CharField(source='account.name', read_only=True)

    class Meta:
        model = Transaction
        fields = (
            'id',
            'account',
            'account_name',
            'date',
            'posted_date',
            'raw_description',
            'merchant',
            'amount',
            'source_type',
        )


class TransactionSerializer(serializers.ModelSerializer):
    account_name = serializers.CharField(source='account.name', read_only=True)
    subcategory_name = serializers.CharField(
        source='subcategory.name',
        read_only=True,
        allow_null=True,
    )
    duplicate_candidates = DuplicateCandidateSerializer(many=True, read_only=True)
    review_reasons = serializers.SerializerMethodField()

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
            'review_status',
            'review_reasons',
            'duplicate_resolution',
            'duplicate_candidates',
            'duplicate_of',
            'notes',
            'created_at',
            'updated_at',
        )
        read_only_fields = (
            'budget_bucket',
            'review_status',
            'review_reasons',
            'duplicate_resolution',
            'duplicate_candidates',
            'duplicate_of',
            'created_at',
            'updated_at',
        )

    def get_review_reasons(self, transaction):
        labels = {
            'UNRESOLVED_DUPLICATE': 'Possible duplicate needs resolution',
            'UNKNOWN_CLASSIFICATION': 'Choose a transaction classification',
            'SUBCATEGORY_REQUIRED': 'Assign a Needs/Wants subcategory',
            'TRANSFER_REQUIRES_REVIEW': 'Confirm the transfer',
            'READY_TO_MARK_REVIEWED': 'Ready to mark reviewed',
        }
        return [
            {'code': reason, 'label': labels[reason]}
            for reason in transaction.review_reasons()
        ]

    def validate(self, attrs):
        if self.instance:
            immutable_fields = (
                'account',
                'date',
                'posted_date',
                'raw_description',
                'amount',
                'source_type',
                'import_batch',
                'source_row',
            )
            for field in immutable_fields:
                if field not in attrs:
                    continue
                current = getattr(self.instance, f'{field}_id', None) if field in ('account', 'import_batch') else getattr(self.instance, field)
                requested = attrs[field].pk if field in ('account', 'import_batch') and attrs[field] else attrs[field]
                if current != requested:
                    raise serializers.ValidationError({field: 'Imported source data cannot be edited.'})

        classification = attrs.get(
            'classification',
            self.instance.classification if self.instance else TransactionClassification.UNKNOWN,
        )
        classification_changed = (
            self.instance is not None
            and classification != self.instance.classification
        )
        subcategory_was_supplied = 'subcategory' in attrs
        subcategory = attrs.get(
            'subcategory',
            self.instance.subcategory if self.instance else None,
        )
        if subcategory:
            eligible = Transaction.eligible_buckets(classification)
            if subcategory.budget_bucket not in eligible and not (
                classification_changed and not subcategory_was_supplied
            ):
                raise serializers.ValidationError({
                    'subcategory': 'Subcategories must match the transaction classification: '
                    'Needs/Wants for expenses and refunds, Savings for transfers.'
                })
            if self.instance is None or (
                subcategory_was_supplied and self.instance.subcategory_id != subcategory.pk
            ):
                if not subcategory.is_active:
                    raise serializers.ValidationError({
                        'subcategory': 'New assignments must use an active subcategory.'
                    })
        return attrs
