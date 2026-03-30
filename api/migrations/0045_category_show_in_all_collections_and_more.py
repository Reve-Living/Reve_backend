from django.db import migrations, models


def copy_existing_collection_visibility(apps, schema_editor):
    Category = apps.get_model("api", "Category")
    SubCategory = apps.get_model("api", "SubCategory")

    Category.objects.filter(show_in_collections=True).update(show_in_all_collections=True)
    SubCategory.objects.filter(show_in_collections=True).update(show_in_all_collections=True)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0044_product_dimension_note"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="show_in_all_collections",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="subcategory",
            name="show_in_all_collections",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(copy_existing_collection_visibility, migrations.RunPython.noop),
    ]
