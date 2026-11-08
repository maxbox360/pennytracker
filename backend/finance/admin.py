from django.contrib import admin
from .models import Account, BudgetTargetSettings, ImportBatch, Subcategory, Transaction


@admin.register(BudgetTargetSettings)
class BudgetTargetSettingsAdmin(admin.ModelAdmin):
    list_display = ('needs_percent', 'wants_percent', 'savings_percent')
    readonly_fields = ('id',)

    def has_add_permission(self, request):
        return not BudgetTargetSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = (
        'original_filename',
        'account',
        'imported_at',
        'earliest_transaction_date',
        'latest_transaction_date',
        'source_row_count',
        'imported_row_count',
        'already_represented_row_count',
        'possible_duplicate_row_count',
    )
    list_filter = ('account', 'format_identifier')
    search_fields = ('original_filename', 'file_sha256')
    readonly_fields = (
        'account',
        'original_filename',
        'file_sha256',
        'format_identifier',
        'source_row_count',
        'imported_row_count',
        'already_represented_row_count',
        'possible_duplicate_row_count',
        'earliest_transaction_date',
        'latest_transaction_date',
        'imported_at',
    )

    def has_add_permission(self, request):
        return False


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ('name', 'institution', 'account_type', 'is_savings_destination', 'is_active')
    list_filter = ('account_type', 'is_savings_destination', 'is_active')
    search_fields = ('name', 'institution')


@admin.register(Subcategory)
class SubcategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'budget_bucket', 'is_active')
    list_filter = ('budget_bucket', 'is_active')
    search_fields = ('name',)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        return False

    list_display = (
        'date',
        'raw_description',
        'account',
        'amount',
        'classification',
        'review_status',
        'duplicate_resolution',
        'budget_bucket',
    )
    list_filter = ('classification', 'review_status', 'duplicate_resolution', 'budget_bucket', 'account')
    search_fields = ('raw_description', 'merchant', 'notes')
    readonly_fields = (
        'account',
        'date',
        'posted_date',
        'raw_description',
        'amount',
        'source_type',
        'import_batch',
        'source_row',
        'budget_bucket',
        'created_at',
        'updated_at',
    )
