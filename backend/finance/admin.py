from django.contrib import admin
from .models import Account, Subcategory, Transaction


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
    list_display = ('date', 'raw_description', 'account', 'amount', 'classification', 'budget_bucket')
    list_filter = ('classification', 'budget_bucket', 'account')
    search_fields = ('raw_description', 'merchant', 'notes')
    readonly_fields = ('budget_bucket', 'created_at', 'updated_at')
