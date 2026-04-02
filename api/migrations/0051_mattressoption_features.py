from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0050_lifestylearticle_card_image"),
    ]

    operations = [
        migrations.AddField(
            model_name="mattressoption",
            name="features",
            field=models.TextField(blank=True, default=""),
        ),
    ]

