from django.db import migrations


def anonymize_deleted_accounts(apps, schema_editor):
    audit_event = apps.get_model("listings", "ManagerAuditEvent")
    database = schema_editor.connection.alias
    deleted_usernames = audit_event.objects.using(database).filter(
        action="account_deleted",
    ).values_list("target_username", flat=True).distinct()
    audit_event.objects.using(database).filter(
        target_username__in=deleted_usernames,
    ).update(target_username="Deleted account")


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0014_managerauditevent"),
    ]

    operations = [
        migrations.RunPython(anonymize_deleted_accounts, migrations.RunPython.noop),
    ]
