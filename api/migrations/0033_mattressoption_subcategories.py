from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0032_mattressoption_categories"),
    ]

    operations = [
        migrations.AddField(
            model_name="mattressoption",
            name="subcategories",
            field=models.ManyToManyField(blank=True, related_name="mattress_options", to="api.subcategory"),
        ),
    ]
