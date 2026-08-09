from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0081_product_google_feed_mpn_gtin"),
    ]

    operations = [
        migrations.AddField(
            model_name="productimage",
            name="size_name",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]
