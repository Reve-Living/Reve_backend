from django.db import migrations, models
from django.utils.text import slugify


def seed_article_slugs(apps, schema_editor):
    LifestyleArticle = apps.get_model("api", "LifestyleArticle")

    used_slugs = set()
    for article in LifestyleArticle.objects.all().order_by("id"):
        base_slug = slugify(article.title) or f"article-{article.id}"
        slug = base_slug
        counter = 1
        while slug in used_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        article.slug = slug
        article.save(update_fields=["slug"])
        used_slugs.add(slug)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0046_lifestylesection_lifestylearticle"),
    ]

    operations = [
        migrations.AddField(
            model_name="lifestylearticle",
            name="article_body",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="lifestylearticle",
            name="article_intro",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="lifestylearticle",
            name="article_title",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="lifestylearticle",
            name="slug",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.RunPython(seed_article_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="lifestylearticle",
            name="read_more_type",
            field=models.CharField(
                choices=[
                    ("none", "No read more link"),
                    ("url", "External/Internal URL"),
                    ("pdf", "PDF"),
                    ("article", "Article page"),
                ],
                default="none",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="lifestylearticle",
            name="slug",
            field=models.SlugField(blank=True, default="", max_length=255, unique=True),
        ),
    ]
