from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0070_product_stock_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="categoryfilter",
            name="option_order",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
