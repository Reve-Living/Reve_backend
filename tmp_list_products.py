import os,django
os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings')
django.setup()
from api.models import Product, ProductImage, ProductColor
from collections import defaultdict
res=defaultdict(list)
for img in ProductImage.objects.exclude(color_name=''):
    res[img.product_id].append(img.color_name)
for pid, names in res.items():
    if len(names)>=2:
        p=Product.objects.get(id=pid)
        colors=list(ProductColor.objects.filter(product_id=pid).values_list('name', flat=True))
        print(pid, p.slug, names, colors)
