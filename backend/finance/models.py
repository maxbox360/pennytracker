from django.db import models
from django.db.models import Q


class BudgetBucket(models.TextChoices):
    NEEDS = 'NEEDS', 'Needs'
    WANTS = 'WANTS', 'Wants'
    SAVINGS = 'SAVINGS', 'Savings'


class TransactionClassification(models.TextChoices):
    UNKNOWN = 'UNKNOWN', 'Unknown'
    EXPENSE = 'EXPENSE', 'Expense'
    INCOME = 'INCOME', 'Income'
    REFUND = 'REFUND', 'Refund'
    TRANSFER = 'TRANSFER', 'Transfer'
    EXCLUDED = 'EXCLUDED', 'Excluded'


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


class Subcategory(models.Model):
    name = models.CharField(max_length=100)
    budget_bucket = models.CharField(max_length=10, choices=BudgetBucket.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['budget_bucket', 'name', 'id']
        constraints = [
            models.CheckConstraint(
                condition=Q(budget_bucket__in=BudgetBucket.values),
                name='valid_subcategory_budget_bucket',
            ),
        ]

    def __str__(self):
        return self.name


class Transaction(models.Model):
    Classification = TransactionClassification

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='transactions')
    subcategory = models.ForeignKey(
        Subcategory,
        on_delete=models.PROTECT,
        related_name='transactions',
        null=True,
        blank=True,
    )
    date = models.DateField()
    posted_date = models.DateField(null=True, blank=True)
    raw_description = models.TextField()
    merchant = models.CharField(max_length=200, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    classification = models.CharField(
        max_length=10,
        choices=Classification.choices,
        default=Classification.UNKNOWN,
    )
    budget_bucket = models.CharField(max_length=10, choices=BudgetBucket.choices, blank=True)
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
        ]

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        category_may_change = update_fields is None or 'subcategory' in update_fields
        if category_may_change:
            previous_subcategory_id = None
            if self.pk and not self._state.adding:
                previous_subcategory_id = (
                    type(self).objects.filter(pk=self.pk)
                    .values_list('subcategory_id', flat=True)
                    .first()
                )
            if self._state.adding or previous_subcategory_id != self.subcategory_id:
                self.budget_bucket = self.subcategory.budget_bucket if self.subcategory_id else ''
                if update_fields is not None:
                    kwargs['update_fields'] = set(update_fields) | {'budget_bucket'}
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.date} {self.raw_description} ({self.amount})'
