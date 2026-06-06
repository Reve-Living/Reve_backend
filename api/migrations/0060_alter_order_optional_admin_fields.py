from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0059_productcolor_is_available"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="address",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AlterField(
            model_name="order",
            name="city",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AlterField(
            model_name="order",
            name="email",
            field=models.EmailField(blank=True, default="", max_length=254),
        ),
        migrations.AlterField(
            model_name="order",
            name="first_name",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AlterField(
            model_name="order",
            name="last_name",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AlterField(
            model_name="order",
            name="phone",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AlterField(
            model_name="order",
            name="postal_code",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
