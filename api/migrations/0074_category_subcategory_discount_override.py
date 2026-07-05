from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0073_categoryfilter_cat_filter_cat_active_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="discount_override_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="category",
            name="discount_percentage",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="subcategory",
            name="discount_override_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="subcategory",
            name="discount_percentage",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
