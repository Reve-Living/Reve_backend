from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0037_category_meta_fields_product_meta_fields_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="collection",
            name="is_featured",
            field=models.BooleanField(default=False),
        ),
    ]
