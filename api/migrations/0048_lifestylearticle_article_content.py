from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0047_lifestylearticle_article_page_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="lifestylearticle",
            name="article_content",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
