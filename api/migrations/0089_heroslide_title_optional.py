from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0088_productaddon_multiple_colours"),
    ]

    operations = [
        migrations.AlterField(
            model_name="heroslide",
            name="title",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
