from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0041_product_is_hidden"),
    ]

    operations = [
        migrations.AddField(
            model_name="heroslide",
            name="selected_subcategories",
            field=models.ManyToManyField(blank=True, related_name="hero_slides_selected", to="api.subcategory"),
        ),
    ]
