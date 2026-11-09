from django.db import migrations
from django.db.models import Q


def repair_review_state(apps, schema_editor):
    """Migration 0004 originally initialized UNKNOWN rows as NOT_REQUIRED and was later edited
    after being applied locally, leaving blocked rows outside the Review queue."""
    Transaction = apps.get_model('finance', 'Transaction')
    Transaction.objects.using(schema_editor.connection.alias).exclude(
        duplicate_resolution='CONFIRMED_DUPLICATE',
    ).exclude(review_status='PENDING').filter(
        Q(classification='UNKNOWN')
        | Q(duplicate_resolution='UNRESOLVED')
        | Q(categorization_rule_conflict=True)
        | Q(classification__in=('EXPENSE', 'REFUND'), subcategory__isnull=True)
    ).update(review_status='PENDING')


class Migration(migrations.Migration):

    dependencies = [('finance', '0008_remove_categorizationrule_valid_rule_classification_and_more')]

    operations = [migrations.RunPython(repair_review_state, migrations.RunPython.noop)]
