from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0036_order_alternative_phone_order_floor_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="image_alt_text",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="category",
            name="meta_description",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="category",
            name="meta_title",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="subcategory",
            name="image_alt_text",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="subcategory",
            name="meta_description",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="subcategory",
            name="meta_title",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="product",
            name="meta_description",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="product",
            name="meta_title",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="productimage",
            name="alt_text",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
