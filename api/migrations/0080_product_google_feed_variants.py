from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0079_product_google_feed_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="google_feed_sku",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="product",
            name="google_feed_special_feature",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="product",
            name="google_feed_variants",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
