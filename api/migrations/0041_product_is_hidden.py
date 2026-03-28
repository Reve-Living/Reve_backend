from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0040_order_promo_code_order_promo_discount_amount_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="is_hidden",
            field=models.BooleanField(default=False),
        ),
    ]
