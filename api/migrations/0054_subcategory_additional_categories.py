from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0053_announcementsettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="subcategory",
            name="additional_categories",
            field=models.ManyToManyField(blank=True, related_name="shared_subcategories", to="api.category"),
        ),
    ]
