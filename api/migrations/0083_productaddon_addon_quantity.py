from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0082_productimage_size_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="productaddon",
            name="addon_quantity",
            field=models.PositiveIntegerField(default=1),
        ),
    ]
