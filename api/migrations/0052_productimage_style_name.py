from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0051_mattressoption_features"),
    ]

    operations = [
        migrations.AddField(
            model_name="productimage",
            name="style_name",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]
