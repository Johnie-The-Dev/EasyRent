# Generated by Django 5.1.6 on 2025-03-02 21:57

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0004_tenantcollection'),
    ]

    operations = [
        migrations.RenameField(
            model_name='property',
            old_name='location',
            new_name='town',
        ),
    ]
