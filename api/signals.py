import logging
import os

import requests
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Product

logger = logging.getLogger(__name__)

# The storefront pre-renders one static SEO page per product (title, canonical,
# structured data) and the sitemap at *frontend build time*. If a product is
# added/edited/removed on the backend without a new frontend deploy, its page
# has no static content for Google to crawl - it silently falls back to the
# generic app shell (same bytes as the homepage, no canonical tag), which
# Search Console reports as Soft 404 / "Discovered or Crawled - not indexed".
#
# To keep the storefront's SEO pages in sync automatically, this pings a
# Vercel Deploy Hook whenever a product changes. Configure VERCEL_DEPLOY_HOOK_URL
# (Vercel project -> Settings -> Git -> Deploy Hooks) to enable it; until then
# this is a no-op.
DEPLOY_HOOK_LOCK_KEY = "reve:frontend-deploy-hook-lock"
DEPLOY_HOOK_DEBOUNCE_SECONDS = 180


def trigger_frontend_rebuild() -> None:
    hook_url = os.getenv("VERCEL_DEPLOY_HOOK_URL", "").strip()
    if not hook_url:
        return

    # Debounce: bulk edits (imports, admin batch updates) should collapse into
    # a single deploy instead of queuing one per row.
    if not cache.add(DEPLOY_HOOK_LOCK_KEY, True, DEPLOY_HOOK_DEBOUNCE_SECONDS):
        return

    try:
        requests.post(hook_url, timeout=5)
    except requests.RequestException:
        logger.warning("Failed to trigger frontend rebuild via Vercel deploy hook", exc_info=True)


@receiver(post_save, sender=Product)
def _product_saved(sender, **kwargs):
    trigger_frontend_rebuild()


@receiver(post_delete, sender=Product)
def _product_deleted(sender, **kwargs):
    trigger_frontend_rebuild()
