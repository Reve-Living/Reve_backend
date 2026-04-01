from django.db import migrations, models


def copy_image_to_card_image(apps, schema_editor):
    LifestyleArticle = apps.get_model("api", "LifestyleArticle")
    for article in LifestyleArticle.objects.all():
        if not getattr(article, "card_image", "") and getattr(article, "image", ""):
            article.card_image = article.image
            article.save(update_fields=["card_image"])


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0049_lifestylearticle_article_sections"),
    ]

    operations = [
        migrations.AddField(
            model_name="lifestylearticle",
            name="card_image",
            field=models.URLField(blank=True, max_length=1000),
        ),
        migrations.RunPython(copy_image_to_card_image, migrations.RunPython.noop),
    ]
