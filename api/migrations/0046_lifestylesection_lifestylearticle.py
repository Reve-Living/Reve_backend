from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0045_category_show_in_all_collections_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="LifestyleSection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(default="Transform Your Home", max_length=255)),
                ("subtitle", models.TextField(blank=True, default="")),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-updated_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="LifestyleArticle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                ("image", models.URLField(blank=True, max_length=1000)),
                ("read_more_type", models.CharField(choices=[("none", "No read more link"), ("url", "External/Internal URL"), ("pdf", "PDF")], default="none", max_length=10)),
                ("read_more_url", models.CharField(blank=True, default="", max_length=1000)),
                ("read_more_pdf", models.CharField(blank=True, default="", max_length=1000)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("section", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="articles", to="api.lifestylesection")),
            ],
            options={
                "ordering": ["sort_order", "-updated_at", "-id"],
            },
        ),
    ]
