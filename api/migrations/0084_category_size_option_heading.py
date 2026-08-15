from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0083_productaddon_addon_quantity"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="size_option_heading",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
    ]
