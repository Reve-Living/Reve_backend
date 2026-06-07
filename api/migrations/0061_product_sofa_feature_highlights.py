from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0060_alter_order_optional_admin_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="sofa_feature_highlights",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
