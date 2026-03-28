from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0042_heroslide_selected_subcategories"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="assembly_service_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="product",
            name="assembly_service_price",
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=10),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="assembly_service_price",
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=10),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="assembly_service_selected",
            field=models.BooleanField(default=False),
        ),
    ]
