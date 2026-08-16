from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0086_productaddon_size_variation"),
    ]

    operations = [
        migrations.AddField(
            model_name="productaddon",
            name="addon_color_name",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.RemoveConstraint(
            model_name="productaddon",
            name="unique_product_addon",
        ),
        migrations.AddConstraint(
            model_name="productaddon",
            constraint=models.UniqueConstraint(fields=("main_product", "addon_product", "addon_size_name", "addon_color_name"), name="unique_product_addon"),
        ),
    ]
