from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("api", "0077_category_subcategory_content_overrides")]

    operations = [
        migrations.CreateModel(
            name="ProductAddon",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("addon_price", models.DecimalField(decimal_places=2, max_digits=10)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("addon_product", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="addon_for_products", to="api.product")),
                ("main_product", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="product_addons", to="api.product")),
            ], options={"ordering": ["sort_order", "id"]},
        ),
        migrations.AddConstraint(model_name="productaddon", constraint=models.UniqueConstraint(fields=("main_product", "addon_product"), name="unique_product_addon")),
        migrations.AddConstraint(model_name="productaddon", constraint=models.CheckConstraint(condition=~models.Q(("main_product", models.F("addon_product"))), name="product_addon_not_self")),
    ]
