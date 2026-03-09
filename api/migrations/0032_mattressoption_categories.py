from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0031_merge_20260309_0001"),
    ]

    operations = [
        migrations.AddField(
            model_name="mattressoption",
            name="categories",
            field=models.ManyToManyField(blank=True, related_name="mattress_options", to="api.category"),
        ),
    ]
