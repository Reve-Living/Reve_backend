from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0078_productaddon"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="google_feed_brand",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="product",
            name="google_feed_color",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="product",
            name="google_feed_material",
            field=models.CharField(blank=True, default="", max_length=180),
        ),
        migrations.AddField(
            model_name="product",
            name="google_feed_fabric_type",
            field=models.CharField(blank=True, default="", max_length=180),
        ),
        migrations.AddField(
            model_name="product",
            name="google_feed_frame_material",
            field=models.CharField(blank=True, default="", max_length=180),
        ),
        migrations.AddField(
            model_name="product",
            name="google_feed_headboard_material",
            field=models.CharField(blank=True, default="", max_length=180),
        ),
        migrations.AddField(
            model_name="product",
            name="google_feed_number_of_drawers",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="product",
            name="google_feed_depth",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="product",
            name="google_feed_length",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="product",
            name="google_feed_width",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="product",
            name="google_feed_height",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="product",
            name="google_feed_seat_height",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
    ]
