from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0048_lifestylearticle_article_content"),
    ]

    operations = [
        migrations.AddField(
            model_name="lifestylearticle",
            name="article_sections",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
