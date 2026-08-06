from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0080_product_google_feed_variants"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="google_feed_mpn",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="product",
            name="google_feed_gtin",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]
