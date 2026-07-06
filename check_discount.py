#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from api.models import Category, Product

# Check categories with override enabled
categories = Category.objects.filter(discount_override_enabled=True)
print(f"Categories with override enabled: {categories.count()}")
for cat in categories:
    print(f"  - {cat.name}: {cat.discount_percentage}%")
    products = Product.objects.filter(category=cat)
    if products.exists():
        prod = products.first()
        print(f"    First product: {prod.name}, discount_percentage: {prod.discount_percentage}")

# Check a specific product
products = Product.objects.all()[:5]
for prod in products:
    print(f"\nProduct: {prod.name}")
    print(f"  - discount_percentage: {prod.discount_percentage}")
    print(f"  - category: {prod.category.name if prod.category else 'None'}")
    if prod.category:
        print(f"  - category.discount_override_enabled: {prod.category.discount_override_enabled}")
        print(f"  - category.discount_percentage: {prod.category.discount_percentage}")
