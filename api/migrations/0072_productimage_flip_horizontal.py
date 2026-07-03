from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0071_categoryfilter_option_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="productimage",
            name="flip_horizontal",
            field=models.BooleanField(default=False),
        ),
    ]
