from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0027_product_sort_order"),
    ]

    operations = [
        migrations.CreateModel(
            name="MattressOption",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("image_url", models.URLField(blank=True, max_length=1000)),
                ("price", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ("original_price", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ("enable_bunk_positions", models.BooleanField(default=False)),
                ("price_top", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ("price_bottom", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ("price_both", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.CreateModel(
            name="MattressOptionPrice",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("size_label", models.CharField(max_length=120)),
                ("price", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ("original_price", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ("price_top", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ("price_bottom", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ("price_both", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                (
                    "option",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="prices",
                        to="api.mattressoption",
                    ),
                ),
            ],
            options={
                "ordering": ["id"],
            },
        ),
    ]
