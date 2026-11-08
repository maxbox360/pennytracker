"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from finance.views import (
    AccountViewSet,
    CategorizationRuleViewSet,
    ImportBatchViewSet,
    SubcategoryViewSet,
    TransactionViewSet,
    all_time_report_view,
    budget_target_settings,
    category_trend_view,
    confirm_import,
    historical_report_drilldown,
    monthly_report_drilldown,
    monthly_report_view,
    preview_import,
    yearly_report_view,
)
from finance.csrf import csrf_token

router = DefaultRouter()
router.register('accounts', AccountViewSet)
router.register('import-batches', ImportBatchViewSet)
router.register('subcategories', SubcategoryViewSet)
router.register('categorization-rules', CategorizationRuleViewSet, basename='categorization-rule')
router.register('transactions', TransactionViewSet, basename='transaction')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/csrf/', csrf_token, name='csrf-token'),
    path('api/imports/preview/', preview_import, name='import-preview'),
    path('api/imports/', confirm_import, name='import-confirm'),
    path('api/budget-targets/', budget_target_settings, name='budget-target-settings'),
    path('api/reports/all-time/', all_time_report_view, name='all-time-report'),
    path('api/reports/yearly/', yearly_report_view, name='yearly-report'),
    path('api/reports/monthly/', monthly_report_view, name='monthly-report'),
    path('api/reports/monthly/drilldown/', monthly_report_drilldown, name='monthly-report-drilldown'),
    path('api/reports/drilldown/', historical_report_drilldown, name='historical-report-drilldown'),
    path('api/reports/category-trend/', category_trend_view, name='category-trend'),
    path('api/', include(router.urls)),
]
