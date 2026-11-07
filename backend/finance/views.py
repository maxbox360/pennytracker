from rest_framework.viewsets import ModelViewSet
from .models import Account, Subcategory, Transaction
from .serializers import AccountSerializer, SubcategorySerializer, TransactionSerializer


class AccountViewSet(ModelViewSet):
    queryset = Account.objects.all()
    serializer_class = AccountSerializer


class SubcategoryViewSet(ModelViewSet):
    queryset = Subcategory.objects.all()
    serializer_class = SubcategorySerializer


class TransactionViewSet(ModelViewSet):
    queryset = Transaction.objects.select_related('account', 'subcategory')
    serializer_class = TransactionSerializer
