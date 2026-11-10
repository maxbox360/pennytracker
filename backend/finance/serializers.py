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
    SubcategoryScope,
    Transaction,
    TransactionClassification,
)
from .categorization_rules import (
    MATCH_TYPES,
    eligibility_scope,
    normalize_rule_text,
    stored_rule_value,
    target_is_valid,
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
        fields = ('id', 'name', 'scope', 'budget_bucket', 'is_active')

    def validate_name(self, value):
        return value.strip()

    def validate(self, attrs):
        scope = attrs.get(
            'scope',
            self.instance.scope if self.instance else SubcategoryScope.BUDGET,
        )
        bucket = attrs.get(
            'budget_bucket',
            self.instance.budget_bucket if self.instance else None,
        )
        if scope == SubcategoryScope.BUDGET and bucket not in BudgetBucket.values:
            raise serializers.ValidationError({
                'budget_bucket': 'Choose Needs, Wants, or Savings for a budget subcategory.'
            })
        if scope == SubcategoryScope.OTHER_INFLOW and bucket is not None:
            raise serializers.ValidationError({
                'budget_bucket': 'Other Inflow subcategories cannot have a budget bucket.'
            })
        if scope not in SubcategoryScope.values:
            raise serializers.ValidationError({'scope': 'Choose a valid subcategory scope.'})

        name = attrs.get('name', self.instance.name if self.instance else '').strip().casefold()
        duplicates = Subcategory.objects.filter(scope=scope)
        if self.instance:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        existing = next(
            (item for item in duplicates if item.name.strip().casefold() == name),
            None,
        )
        if existing:
            message = (
                f'The subcategory “{existing.name}” is archived. Reactivate it instead of creating a duplicate.'
                if not existing.is_active
                else f'An active subcategory named “{existing.name}” already exists.'
            )
            raise serializers.ValidationError({'name': message})
        return attrs


class CategorizationRuleSerializer(serializers.ModelSerializer):
    subcategory_name = serializers.CharField(source='subcategory.name', read_only=True, allow_null=True)
    subcategory_bucket = serializers.CharField(source='subcategory.budget_bucket', read_only=True, allow_null=True)

    class Meta:
        model = CategorizationRule
        fields = (
            'id',
            'name',
            'match_field',
            'match_type',
            'match_value',
            'classification',
            'allow_unknown',
            'savings_effect',
            'subcategory',
            'subcategory_name',
            'subcategory_bucket',
            'subcategory_is_active',
            'is_active',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('created_at', 'updated_at')

    subcategory_is_active = serializers.BooleanField(source='subcategory.is_active', read_only=True, allow_null=True)

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
                ('classification', TransactionClassification.EXPENSE),
                ('match_field', RuleMatchField.RAW_DESCRIPTION),
                ('match_type', RuleMatchType.CONTAINS),
                ('match_value', ''),
                ('savings_effect', SavingsEffect.NONE),
                ('subcategory', None),
                ('is_active', True),
                ('allow_unknown', True),
            )
        }
        classification = values['classification']
        errors = {}
        if classification != TransactionClassification.EXPENSE:
            if attrs.get('allow_unknown', False):
                errors['allow_unknown'] = 'This scope option is available only for Expense rules.'
            values['allow_unknown'] = False
            attrs['allow_unknown'] = False
        elif self.instance is None and 'allow_unknown' not in attrs:
            attrs['allow_unknown'] = True
        if values['match_field'] != RuleMatchField.RAW_DESCRIPTION:
            errors['match_field'] = 'Only raw description matching is supported.'
        if values['match_type'] not in MATCH_TYPES:
            errors['match_type'] = 'Only contains and starts-with matching are supported.'
        if classification != TransactionClassification.TRANSFER and values['savings_effect'] != SavingsEffect.NONE:
            errors['savings_effect'] = 'Only Transfers can have a Savings effect.'
        subcategory = values['subcategory']
        target_valid = target_is_valid(classification, values['savings_effect'], subcategory)
        if not target_valid:
            errors['subcategory'] = 'Choose a subcategory compatible with this outcome and Savings effect.'
        if subcategory is not None:
            target_unchanged = (
                self.instance is not None
                and self.instance.subcategory_id == subcategory.pk
                and self.instance.classification == classification
                and self.instance.savings_effect == values['savings_effect']
            )
            if not target_unchanged and not subcategory.is_active:
                errors['subcategory'] = 'Rules must target an active subcategory.'
            elif target_unchanged and not target_valid and not attrs.get('is_active', False):
                errors.pop('subcategory', None)
            if attrs.get('is_active') is True and (not subcategory.is_active or not target_valid):
                errors['is_active'] = 'Reactivate a valid target before enabling this rule.'

        match_value = normalize_rule_text(values['match_value'])
        if values['is_active'] and match_value and not errors:
            duplicate_rules = CategorizationRule.objects.filter(
                is_active=True,
                match_field=values['match_field'],
                match_type=values['match_type'],
            ).exclude(pk=self.instance.pk if self.instance else None)
            proposed = CategorizationRule(
                classification=classification, savings_effect=values['savings_effect'],
                allow_unknown=values['allow_unknown'], subcategory=subcategory,
            )
            if any(
                normalize_rule_text(rule.match_value) == match_value
                and eligibility_scope(rule) == eligibility_scope(proposed)
                for rule in duplicate_rules
            ):
                errors['match_value'] = 'An active rule already uses this matching condition and eligibility scope.'
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
            'manually_resolved',
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
            'manually_resolved',
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
            'SAVINGS_EFFECT_INVALID': 'Resolve the invalid Savings effect',
            'TRANSFER_REQUIRES_REVIEW': 'Confirm the transfer',
            'READY_TO_MARK_REVIEWED': 'Ready to mark reviewed',
        }
        return [
            {'code': reason, 'label': labels[reason]}
            for reason in transaction.review_reasons()
        ]

    def update(self, instance, validated_data):
        before = (instance.classification, instance.subcategory_id, instance.savings_effect)
        if (
            instance.categorized_by_rule_id
            and 'subcategory' in validated_data
        ):
            validated_data['categorized_by_rule'] = None
        instance = super().update(instance, validated_data)
        if before != (instance.classification, instance.subcategory_id, instance.savings_effect):
            complete = (
                instance.classification != TransactionClassification.UNKNOWN
                and not (instance.classification in (
                    TransactionClassification.EXPENSE, TransactionClassification.REFUND,
                ) and (not instance.subcategory_id or instance.budget_bucket not in
                       Transaction.eligible_buckets(instance.classification)))
            )
            instance.manually_resolved = complete
            if complete:
                instance.categorization_rule_conflict = False
            instance.save(update_fields=('manually_resolved', 'categorization_rule_conflict', 'updated_at'))
        return instance

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
        if classification_changed and 'savings_effect' not in attrs:
            attrs['savings_effect'] = SavingsEffect.NONE
        if (
            self.instance is not None
            and classification == TransactionClassification.TRANSFER
            and attrs.get('savings_effect') == SavingsEffect.NONE
            and self.instance.savings_effect != SavingsEffect.NONE
            and 'subcategory' not in attrs
        ):
            attrs['subcategory'] = None
        subcategory_was_supplied = 'subcategory' in attrs
        subcategory = attrs.get(
            'subcategory',
            self.instance.subcategory if self.instance else None,
        )
        assignment_unchanged = (
            self.instance is not None
            and not classification_changed
            and subcategory is not None
            and subcategory.pk == self.instance.subcategory_id
        )
        if subcategory:
            if assignment_unchanged:
                eligible = (
                    subcategory.scope == Subcategory.Scope.OTHER_INFLOW
                    and self.instance.budget_bucket == ''
                    if classification == TransactionClassification.OTHER_INFLOW
                    else subcategory.scope == Subcategory.Scope.BUDGET
                    and self.instance.budget_bucket in Transaction.eligible_buckets(classification)
                )
            else:
                eligible = Transaction.subcategory_matches_classification(subcategory, classification)
            if not eligible and not (
                classification_changed and not subcategory_was_supplied
            ):
                raise serializers.ValidationError({
                    'subcategory': 'Choose a subcategory compatible with the transaction classification.'
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
        if (
            classification == TransactionClassification.TRANSFER
            and savings_effect == SavingsEffect.NONE
            and subcategory is not None
            and (subcategory_was_supplied or not classification_changed)
            and not (assignment_unchanged and self.instance.savings_effect == SavingsEffect.NONE)
        ):
            raise serializers.ValidationError({
                'subcategory': 'Ordinary Transfers cannot be assigned a subcategory.'
            })
        if savings_effect != SavingsEffect.NONE:
            if (
                classification == TransactionClassification.TRANSFER
                and subcategory and self.instance is not None
                and self.instance.savings_effect == SavingsEffect.NONE
                and assignment_unchanged
                and (not subcategory.is_active or not Transaction.subcategory_matches_classification(
                    subcategory, TransactionClassification.TRANSFER,
                ))
            ):
                raise serializers.ValidationError({
                    'subcategory': 'Choose an active Savings subcategory for new Savings activity.'
                })
            saved_bucket = (
                self.instance.budget_bucket
                if assignment_unchanged
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
