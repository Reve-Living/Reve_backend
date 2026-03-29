from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0043_product_assembly_service_and_orderitem_assembly_service"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="dimension_note",
            field=models.TextField(blank=True, default=""),
        ),
    ]
