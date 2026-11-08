from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower, Trim


class BudgetBucket(models.TextChoices):
    NEEDS = 'NEEDS', 'Needs'
    WANTS = 'WANTS', 'Wants'
    SAVINGS = 'SAVINGS', 'Savings'


class SubcategoryScope(models.TextChoices):
    BUDGET = 'BUDGET', 'Budget'
    OTHER_INFLOW = 'OTHER_INFLOW', 'Other inflows'


class TransactionClassification(models.TextChoices):
    UNKNOWN = 'UNKNOWN', 'Unknown'
    EXPENSE = 'EXPENSE', 'Expense'
    INCOME = 'INCOME', 'Income'
    REFUND = 'REFUND', 'Refund'
    TRANSFER = 'TRANSFER', 'Transfer'
    EXCLUDED = 'EXCLUDED', 'Excluded'
    OTHER_INFLOW = 'OTHER_INFLOW', 'Other inflow'


class ReviewStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    REVIEWED = 'REVIEWED', 'Reviewed'
    NOT_REQUIRED = 'NOT_REQUIRED', 'Not required'


class DuplicateResolution(models.TextChoices):
    NONE = 'NONE', 'Not a possible duplicate'
    UNRESOLVED = 'UNRESOLVED', 'Unresolved'
    DISTINCT = 'DISTINCT', 'Distinct transaction'
    CONFIRMED_DUPLICATE = 'CONFIRMED_DUPLICATE', 'Confirmed duplicate'


class SavingsEffect(models.TextChoices):
    NONE = 'NONE', 'None'
    CONTRIBUTION = 'CONTRIBUTION', 'Contribution'
    WITHDRAWAL = 'WITHDRAWAL', 'Withdrawal'


class RuleMatchField(models.TextChoices):
    RAW_DESCRIPTION = 'RAW_DESCRIPTION', 'Raw description'
    MERCHANT = 'MERCHANT', 'Merchant'


class RuleMatchType(models.TextChoices):
    CONTAINS = 'CONTAINS', 'Contains'
    EQUALS = 'EQUALS', 'Equals'


class RuleClassification(models.TextChoices):
    EXPENSE = TransactionClassification.EXPENSE, 'Expense'


class Account(models.Model):
    class Type(models.TextChoices):
        CHECKING = 'CHECKING', 'Checking'
        SAVINGS = 'SAVINGS', 'Savings'
        CREDIT_CARD = 'CREDIT_CARD', 'Credit card'
        LOAN = 'LOAN', 'Loan'
        INVESTMENT = 'INVESTMENT', 'Investment'
        OTHER = 'OTHER', 'Other'

    name = models.CharField(max_length=100)
    institution = models.CharField(max_length=100, blank=True)
    account_type = models.CharField(max_length=20, choices=Type.choices, default=Type.CHECKING)
    is_savings_destination = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'id']

    def __str__(self):
        return self.name


class ImportBatch(models.Model):
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='import_batches')
    original_filename = models.CharField(max_length=255)
    file_sha256 = models.CharField(max_length=64)
    format_identifier = models.CharField(max_length=50)
    source_row_count = models.PositiveIntegerField()
    imported_row_count = models.PositiveIntegerField()
    already_represented_row_count = models.PositiveIntegerField(default=0)
    possible_duplicate_row_count = models.PositiveIntegerField(default=0)
    earliest_transaction_date = models.DateField(null=True, blank=True)
    latest_transaction_date = models.DateField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-imported_at', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=('account', 'file_sha256'),
                name='unique_import_file_per_account',
            ),
        ]

    def __str__(self):
        return f'{self.original_filename} → {self.account}'


class Subcategory(models.Model):
    Scope = SubcategoryScope

    name = models.CharField(max_length=100)
    scope = models.CharField(max_length=12, choices=Scope.choices, default=Scope.BUDGET)
    budget_bucket = models.CharField(max_length=10, choices=BudgetBucket.choices, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['budget_bucket', 'name', 'id']
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(scope=SubcategoryScope.BUDGET, budget_bucket__in=BudgetBucket.values)
                    | Q(scope=SubcategoryScope.OTHER_INFLOW, budget_bucket__isnull=True)
                ),
                name='valid_subcategory_scope_bucket',
            ),
            models.UniqueConstraint(
                Lower(Trim('name')),
                'scope',
                condition=Q(is_active=True),
                name='unique_active_subcategory_name_ci',
            ),
        ]

    def __str__(self):
        return self.name


class CategorizationRule(models.Model):
    name = models.CharField(max_length=100, blank=True)
    match_field = models.CharField(
        max_length=30,
        choices=RuleMatchField.choices,
        default=RuleMatchField.RAW_DESCRIPTION,
    )
    match_type = models.CharField(
        max_length=10,
        choices=RuleMatchType.choices,
        default=RuleMatchType.CONTAINS,
    )
    match_value = models.CharField(max_length=200)
    classification = models.CharField(
        max_length=10,
        choices=RuleClassification.choices,
        default=RuleClassification.EXPENSE,
    )
    subcategory = models.ForeignKey(
        Subcategory,
        on_delete=models.PROTECT,
        related_name='categorization_rules',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
        constraints = [
            models.CheckConstraint(
                condition=Q(match_field__in=RuleMatchField.values),
                name='valid_rule_match_field',
            ),
            models.CheckConstraint(
                condition=Q(match_type__in=RuleMatchType.values),
                name='valid_rule_match_type',
            ),
            models.CheckConstraint(
                condition=Q(classification__in=RuleClassification.values),
                name='valid_rule_classification',
            ),
        ]

    def __str__(self):
        return self.name.strip() or f'{self.classification} description contains "{self.match_value}"'


class BudgetTargetSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    needs_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('50.00'))
    wants_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('30.00'))
    savings_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('20.00'))

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(id=1),
                name='single_budget_target_settings',
            ),
            models.CheckConstraint(
                condition=Q(needs_percent__gte=0, needs_percent__lte=100),
                name='valid_needs_target_percent',
            ),
            models.CheckConstraint(
                condition=Q(wants_percent__gte=0, wants_percent__lte=100),
                name='valid_wants_target_percent',
            ),
            models.CheckConstraint(
                condition=Q(savings_percent__gte=0, savings_percent__lte=100),
                name='valid_savings_target_percent',
            ),
        ]


class Transaction(models.Model):
    Classification = TransactionClassification
    ReviewStatus = ReviewStatus
    DuplicateResolution = DuplicateResolution
    SavingsEffect = SavingsEffect

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='transactions')
    subcategory = models.ForeignKey(
        Subcategory,
        on_delete=models.PROTECT,
        related_name='transactions',
        null=True,
        blank=True,
    )
    categorized_by_rule = models.ForeignKey(
        CategorizationRule,
        on_delete=models.PROTECT,
        related_name='categorized_transactions',
        null=True,
        blank=True,
    )
    categorization_rule_conflict = models.BooleanField(default=False)
    date = models.DateField()
    posted_date = models.DateField(null=True, blank=True)
    raw_description = models.TextField()
    merchant = models.CharField(max_length=200, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    source_type = models.CharField(max_length=50, blank=True)
    import_batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.PROTECT,
        related_name='transactions',
        null=True,
        blank=True,
    )
    source_row = models.PositiveIntegerField(null=True, blank=True)
    classification = models.CharField(
        max_length=20,
        choices=Classification.choices,
        default=Classification.UNKNOWN,
    )
    savings_effect = models.CharField(
        max_length=12,
        choices=SavingsEffect.choices,
        default=SavingsEffect.NONE,
    )
    budget_bucket = models.CharField(max_length=10, choices=BudgetBucket.choices, blank=True)
    review_status = models.CharField(
        max_length=15,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
    )
    duplicate_resolution = models.CharField(
        max_length=20,
        choices=DuplicateResolution.choices,
        default=DuplicateResolution.NONE,
    )
    duplicate_candidates = models.ManyToManyField(
        'self',
        symmetrical=False,
        related_name='possible_duplicate_for',
        blank=True,
    )
    duplicate_of = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        related_name='confirmed_duplicate_transactions',
        null=True,
        blank=True,
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-id']
        constraints = [
            models.CheckConstraint(
                condition=Q(budget_bucket='') | Q(budget_bucket__in=BudgetBucket.values),
                name='valid_transaction_budget_bucket',
            ),
            models.CheckConstraint(
                condition=Q(classification__in=TransactionClassification.values),
                name='valid_transaction_classification',
            ),
            models.CheckConstraint(
                condition=~Q(classification=TransactionClassification.OTHER_INFLOW)
                | Q(budget_bucket=''),
                name='other_inflow_has_no_budget_bucket',
            ),
            models.CheckConstraint(
                condition=Q(review_status__in=ReviewStatus.values),
                name='valid_transaction_review_status',
            ),
            models.CheckConstraint(
                condition=Q(duplicate_resolution__in=DuplicateResolution.values),
                name='valid_transaction_duplicate_resolution',
            ),
            models.CheckConstraint(
                condition=Q(savings_effect__in=SavingsEffect.values),
                name='valid_transaction_savings_effect',
            ),
            models.CheckConstraint(
                condition=Q(savings_effect=SavingsEffect.NONE)
                | Q(
                    classification=TransactionClassification.TRANSFER,
                    budget_bucket=BudgetBucket.SAVINGS,
                ),
                name='savings_effect_requires_transfer_snapshot',
            ),
            models.UniqueConstraint(
                fields=('import_batch', 'source_row'),
                condition=Q(import_batch__isnull=False),
                name='unique_import_batch_source_row',
            ),
        ]

    @staticmethod
    def eligible_buckets(classification):
        if classification in (TransactionClassification.EXPENSE, TransactionClassification.REFUND):
            return {BudgetBucket.NEEDS, BudgetBucket.WANTS}
        if classification == TransactionClassification.TRANSFER:
            return {BudgetBucket.SAVINGS}
        return set()

    @staticmethod
    def subcategory_matches_classification(subcategory, classification):
        if classification == TransactionClassification.OTHER_INFLOW:
            return subcategory.scope == Subcategory.Scope.OTHER_INFLOW and subcategory.budget_bucket is None
        return (
            subcategory.scope == Subcategory.Scope.BUDGET
            and subcategory.budget_bucket in Transaction.eligible_buckets(classification)
        )

    def review_blockers(self):
        if self.duplicate_resolution == DuplicateResolution.CONFIRMED_DUPLICATE:
            return []
        blockers = []
        if self.duplicate_resolution == DuplicateResolution.UNRESOLVED:
            blockers.append('UNRESOLVED_DUPLICATE')
        if self.categorization_rule_conflict:
            blockers.append('RULE_CONFLICT')
        if self.classification == TransactionClassification.UNKNOWN:
            blockers.append('UNKNOWN_CLASSIFICATION')
        if self.classification in (
            TransactionClassification.EXPENSE,
            TransactionClassification.REFUND,
        ):
            if not self.subcategory_id or self.budget_bucket not in self.eligible_buckets(self.classification):
                blockers.append('SUBCATEGORY_REQUIRED')
        return blockers

    def review_reasons(self):
        reasons = self.review_blockers()
        if self.review_status == ReviewStatus.PENDING:
            if self.classification == TransactionClassification.TRANSFER:
                reasons.append('TRANSFER_REQUIRES_REVIEW')
            elif not reasons:
                reasons.append('READY_TO_MARK_REVIEWED')
        return reasons

    def mark_reviewed(self):
        blockers = self.review_blockers()
        if blockers:
            raise ValidationError({
                'review_status': f'This transaction cannot be marked reviewed: {", ".join(blockers)}.'
            })
        self.review_status = ReviewStatus.REVIEWED
        self.save(update_fields=('review_status', 'updated_at'))

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        changed_fields = set()
        previous = None
        if self.pk and not self._state.adding:
            previous = type(self).objects.filter(pk=self.pk).values(
                'classification',
                'subcategory_id',
                'categorized_by_rule_id',
                'review_status',
            ).first()

        classification_changed = previous is not None and previous['classification'] != self.classification
        subcategory_changed = previous is None or previous['subcategory_id'] != self.subcategory_id
        if previous is not None and (
            classification_changed or subcategory_changed
        ) and self.categorized_by_rule_id == previous['categorized_by_rule_id']:
            self.categorized_by_rule = None
            changed_fields.add('categorized_by_rule')
        if self.categorization_rule_conflict and (self.subcategory_id or classification_changed):
            self.categorization_rule_conflict = False
            changed_fields.add('categorization_rule_conflict')
        if self.subcategory_id:
            eligible_buckets = self.eligible_buckets(self.classification)
            if not self.subcategory_matches_classification(self.subcategory, self.classification):
                if classification_changed:
                    self.subcategory = None
                    self.budget_bucket = ''
                    changed_fields.update(('subcategory', 'budget_bucket'))
                else:
                    raise ValidationError({
                        'subcategory': 'The selected subcategory is not valid for this classification.'
                    })
            elif subcategory_changed:
                if not self.subcategory.is_active:
                    raise ValidationError({'subcategory': 'New assignments must use an active subcategory.'})
                self.budget_bucket = self.subcategory.budget_bucket or ''
                changed_fields.add('budget_bucket')
            elif self.classification == TransactionClassification.OTHER_INFLOW:
                if self.budget_bucket:
                    raise ValidationError({'budget_bucket': 'Other inflows cannot have a budget bucket snapshot.'})
            elif self.budget_bucket not in eligible_buckets:
                raise ValidationError({
                    'budget_bucket': 'A valid historical bucket snapshot is required for this subcategory.'
                })
        elif self.budget_bucket:
            self.budget_bucket = ''
            changed_fields.add('budget_bucket')
        if self.savings_effect != SavingsEffect.NONE and (
            self.classification != TransactionClassification.TRANSFER
            or not self.subcategory_id
            or self.budget_bucket != BudgetBucket.SAVINGS
        ):
            raise ValidationError({
                'savings_effect': 'Savings contributions and withdrawals require a Transfer with a Savings subcategory.'
            })

        if self.duplicate_resolution == DuplicateResolution.CONFIRMED_DUPLICATE:
            if not self.duplicate_of_id or not self.duplicate_candidates.filter(pk=self.duplicate_of_id).exists():
                raise ValidationError({
                    'duplicate_of': 'Select one of this transaction’s possible duplicate candidates.'
                })
        elif self.duplicate_of_id:
            self.duplicate_of = None
            changed_fields.add('duplicate_of')

        if self.duplicate_resolution == DuplicateResolution.CONFIRMED_DUPLICATE:
            self.review_status = ReviewStatus.REVIEWED
        elif self.review_blockers():
            self.review_status = ReviewStatus.PENDING
        elif self.classification == TransactionClassification.TRANSFER and (
            previous is None or previous['classification'] != self.classification
        ):
            self.review_status = ReviewStatus.PENDING
        elif previous is None:
            self.review_status = (
                ReviewStatus.PENDING
                if self.classification == TransactionClassification.TRANSFER
                else ReviewStatus.NOT_REQUIRED
            )
        if previous is None or previous['review_status'] != self.review_status:
            changed_fields.add('review_status')

        if update_fields is not None and changed_fields:
            kwargs['update_fields'] = set(update_fields) | changed_fields | {'updated_at'}
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.date} {self.raw_description} ({self.amount})'
