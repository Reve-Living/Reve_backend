from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0085_subcategory_size_option_heading"),
    ]

    operations = [
        migrations.AddField(
            model_name="productaddon",
            name="addon_size_name",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.RemoveConstraint(
            model_name="productaddon",
            name="unique_product_addon",
        ),
        migrations.AddConstraint(
            model_name="productaddon",
            constraint=models.UniqueConstraint(fields=("main_product", "addon_product", "addon_size_name"), name="unique_product_addon"),
        ),
    ]
