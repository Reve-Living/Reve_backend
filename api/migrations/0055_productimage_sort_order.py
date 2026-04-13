from django.db import migrations, models


def populate_product_image_sort_order(apps, schema_editor):
    ProductImage = apps.get_model("api", "ProductImage")
    product_ids = ProductImage.objects.order_by().values_list("product_id", flat=True).distinct()
    for product_id in product_ids:
        images = ProductImage.objects.filter(product_id=product_id).order_by("id")
        for index, image in enumerate(images, start=1):
            image.sort_order = index
            image.save(update_fields=["sort_order"])


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0054_subcategory_additional_categories"),
    ]

    operations = [
        migrations.AddField(
            model_name="productimage",
            name="sort_order",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(populate_product_image_sort_order, migrations.RunPython.noop),
    ]
