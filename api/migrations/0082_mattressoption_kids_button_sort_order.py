from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0081_product_google_feed_mpn_gtin"),
    ]

    operations = [
        migrations.AddField(
            model_name="mattressoption",
            name="kids_button_sort_order",
            field=models.IntegerField(default=0),
        ),
    ]
