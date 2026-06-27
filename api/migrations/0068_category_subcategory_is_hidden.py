from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0067_mattressoption_kids_button_label"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="is_hidden",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="subcategory",
            name="is_hidden",
            field=models.BooleanField(default=False),
        ),
    ]
