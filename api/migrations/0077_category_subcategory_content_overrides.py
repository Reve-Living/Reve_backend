from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("api", "0076_productsize_stock_status")]

    operations = [
        migrations.AddField(model_name="category", name="faqs_override_enabled", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="category", name="faqs", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="category", name="delivery_info_override_enabled", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="category", name="delivery_info", field=models.TextField(blank=True, default="")),
        migrations.AddField(model_name="category", name="delivery_title", field=models.CharField(blank=True, default="", max_length=150)),
        migrations.AddField(model_name="subcategory", name="faqs_override_enabled", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="subcategory", name="faqs", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="subcategory", name="delivery_info_override_enabled", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="subcategory", name="delivery_info", field=models.TextField(blank=True, default="")),
        migrations.AddField(model_name="subcategory", name="delivery_title", field=models.CharField(blank=True, default="", max_length=150)),
    ]
