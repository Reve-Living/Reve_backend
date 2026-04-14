from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0055_productimage_sort_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="suggested_products",
            field=models.ManyToManyField(
                blank=True,
                related_name="suggested_for_products",
                to="api.product",
            ),
        ),
    ]
