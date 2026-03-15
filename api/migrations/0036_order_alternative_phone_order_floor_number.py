from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0035_order_reference_images_order_special_notes"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="alternative_phone",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="order",
            name="floor_number",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
