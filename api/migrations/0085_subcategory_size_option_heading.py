from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0084_category_size_option_heading"),
    ]

    operations = [
        migrations.AddField(
            model_name="subcategory",
            name="size_option_heading",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
    ]
