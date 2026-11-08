from decimal import Decimal

from rest_framework import serializers
from .models import (
    Account,
    BudgetBucket,
    BudgetTargetSettings,
    CategorizationRule,
    ImportBatch,
    RuleMatchField,
    RuleMatchType,
    SavingsEffect,
    Subcategory,
    Transaction,
    TransactionClassification,
)
from .categorization_rules import normalize_rule_text, stored_rule_value


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


class BudgetTargetSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = BudgetTargetSettings
        fields = ('needs_percent', 'wants_percent', 'savings_percent')

    def validate(self, attrs):
        errors = {}
        for field in ('needs_percent', 'wants_percent', 'savings_percent'):
            value = attrs.get(field, getattr(self.instance, field, None) if self.instance else None)
            if value is not None and not Decimal('0') <= value <= Decimal('100'):
                errors[field] = 'Enter a percentage from 0 to 100.'
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class SubcategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Subcategory
        fields = ('id', 'name', 'budget_bucket', 'is_active')

    def validate_name(self, value):
        normalized_name = value.strip().casefold()
        duplicates = Subcategory.objects.exclude(pk=self.instance.pk) if self.instance else Subcategory.objects.all()
        existing = next(
            (subcategory for subcategory in duplicates if subcategory.name.strip().casefold() == normalized_name),
            None,
        )
        if existing:
            if not existing.is_active:
                message = (
                    f'The subcategory “{existing.name}” is archived. '
                    'Reactivate it from Categories instead of creating a duplicate.'
                )
            else:
                message = (
                    f'An active subcategory named “{existing.name}” already exists. '
                    'Select it from the subcategory list instead of creating a duplicate.'
                )
            raise serializers.ValidationError(message)
        return value.strip()


class CategorizationRuleSerializer(serializers.ModelSerializer):
    subcategory_name = serializers.CharField(source='subcategory.name', read_only=True)
    subcategory_bucket = serializers.CharField(source='subcategory.budget_bucket', read_only=True)

    class Meta:
        model = CategorizationRule
        fields = (
            'id',
            'name',
            'match_field',
            'match_type',
            'match_value',
            'classification',
            'subcategory',
            'subcategory_name',
            'subcategory_bucket',
            'subcategory_is_active',
            'is_active',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('created_at', 'updated_at')

    subcategory_is_active = serializers.BooleanField(source='subcategory.is_active', read_only=True)

    def validate_match_value(self, value):
        value = stored_rule_value(value)
        if not value:
            raise serializers.ValidationError('Enter a non-empty description fragment.')
        return value

    def validate_name(self, value):
        return value.strip()

    def validate(self, attrs):
        values = {
            field: attrs.get(field, getattr(self.instance, field, default))
            for field, default in (
                ('match_field', RuleMatchField.RAW_DESCRIPTION),
                ('match_type', RuleMatchType.CONTAINS),
                ('classification', TransactionClassification.EXPENSE),
                ('match_value', ''),
                ('subcategory', None),
                ('is_active', True),
            )
        }
        errors = {}
        if values['match_field'] != RuleMatchField.RAW_DESCRIPTION:
            errors['match_field'] = 'Only raw description matching is supported.'
        if values['match_type'] != RuleMatchType.CONTAINS:
            errors['match_type'] = 'Only contains matching is supported.'
        if values['classification'] != TransactionClassification.EXPENSE:
            errors['classification'] = 'Categorization rules currently require Expense classification.'
        subcategory = values['subcategory']
        if subcategory is None:
            errors['subcategory'] = 'Choose a subcategory.'
        else:
            target_unchanged = (
                self.instance is not None
                and self.instance.subcategory_id == subcategory.pk
            )
            target_is_usable = (
                subcategory.is_active
                and subcategory.budget_bucket in (BudgetBucket.NEEDS, BudgetBucket.WANTS)
            )
            if not target_unchanged and not subcategory.is_active:
                errors['subcategory'] = 'Rules must target an active subcategory.'
            elif not target_unchanged and not target_is_usable:
                errors['subcategory'] = 'Expense rules must target a Needs or Wants subcategory.'
            elif attrs.get('is_active') is True and not target_is_usable:
                errors['is_active'] = 'Reactivate a valid Needs/Wants target before enabling this rule.'

        match_value = normalize_rule_text(values['match_value'])
        if values['is_active'] and match_value and not errors:
            duplicate_rules = CategorizationRule.objects.filter(
                is_active=True,
                classification=values['classification'],
                match_field=values['match_field'],
                match_type=values['match_type'],
            ).exclude(pk=self.instance.pk if self.instance else None)
            if any(normalize_rule_text(rule.match_value) == match_value for rule in duplicate_rules):
                errors['match_value'] = 'An active rule already uses this matching condition.'
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


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
            'categorized_by_rule',
            'categorization_rule_conflict',
            'date',
            'posted_date',
            'raw_description',
            'merchant',
            'amount',
            'source_type',
            'import_batch',
            'source_row',
            'classification',
            'savings_effect',
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
            'categorized_by_rule',
            'categorization_rule_conflict',
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
            'RULE_CONFLICT': 'Conflicting categorization rules need resolution',
            'TRANSFER_REQUIRES_REVIEW': 'Confirm the transfer',
            'READY_TO_MARK_REVIEWED': 'Ready to mark reviewed',
        }
        return [
            {'code': reason, 'label': labels[reason]}
            for reason in transaction.review_reasons()
        ]

    def update(self, instance, validated_data):
        if (
            instance.categorized_by_rule_id
            and 'subcategory' in validated_data
        ):
            validated_data['categorized_by_rule'] = None
        return super().update(instance, validated_data)

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
        savings_effect = attrs.get(
            'savings_effect',
            self.instance.savings_effect if self.instance else SavingsEffect.NONE,
        )
        if savings_effect != SavingsEffect.NONE:
            saved_bucket = (
                self.instance.budget_bucket
                if self.instance and not subcategory_was_supplied
                else (subcategory.budget_bucket if subcategory else '')
            )
            if classification != TransactionClassification.TRANSFER:
                raise serializers.ValidationError({
                    'savings_effect': 'Savings effects can only be assigned to Transfers.'
                })
            if not subcategory or saved_bucket != BudgetBucket.SAVINGS:
                raise serializers.ValidationError({
                    'savings_effect': 'Choose a Savings subcategory before marking a contribution or withdrawal.'
                })
        return attrs
