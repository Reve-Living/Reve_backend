from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0038_collection_is_featured"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="show_in_collections",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="subcategory",
            name="show_in_collections",
            field=models.BooleanField(default=False),
        ),
    ]
