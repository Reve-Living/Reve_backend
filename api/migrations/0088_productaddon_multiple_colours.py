from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0087_productaddon_colour_selection"),
    ]

    operations = [
        migrations.AddField(
            model_name="productaddon",
            name="addon_color_names",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
