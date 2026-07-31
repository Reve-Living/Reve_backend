from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .views import (
    RegisterView,
    HealthCheckView,
    CategoryViewSet,
    SubCategoryViewSet,
    CollectionViewSet,
    HeroSlideViewSet,
    LifestyleSectionViewSet,
    LifestyleArticleViewSet,
    ProductViewSet,
    ProductFiltersView,
    OrderViewSet,
    ReviewViewSet,
    UploadViewSet,
    PaymentViewSet,
    FilterTypeViewSet,
    FilterOptionViewSet,
    DimensionTemplateViewSet,
    ProductStyleLibraryViewSet,
    CategoryFilterViewSet,
    CategoryFiltersView,
    AdminSummaryView,
    MattressOptionViewSet,
    ProductMattressAdminViewSet,
    ProductAddonViewSet,
    PromotionViewSet,
)

router = DefaultRouter()
router.register(r"categories", CategoryViewSet)
router.register(r"subcategories", SubCategoryViewSet)
router.register(r"collections", CollectionViewSet)
router.register(r"hero-slides", HeroSlideViewSet)
router.register(r"lifestyle-sections", LifestyleSectionViewSet)
router.register(r"lifestyle-articles", LifestyleArticleViewSet)
router.register(r"products", ProductViewSet)
router.register(r"orders", OrderViewSet)
router.register(r"reviews", ReviewViewSet)
router.register(r"uploads", UploadViewSet, basename="uploads")
router.register(r"payments", PaymentViewSet, basename="payments")
router.register(r"filter-types", FilterTypeViewSet)
router.register(r"filter-options", FilterOptionViewSet)
router.register(r"dimension-templates", DimensionTemplateViewSet)
router.register(r"style-groups", ProductStyleLibraryViewSet, basename="style-groups")
router.register(r"category-filters", CategoryFilterViewSet)
router.register(r"mattress-options", MattressOptionViewSet)
router.register(r"product-mattresses", ProductMattressAdminViewSet)
router.register(r"product-addons", ProductAddonViewSet)
router.register(r"promotions", PromotionViewSet)

urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health"),
    path("products/filters/", ProductFiltersView.as_view(), name="product-filters"),
    path("", include(router.urls)),
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("categories/<slug:category_slug>/filters/", CategoryFiltersView.as_view(), name="category-filters"),
    path("admin/summary/", AdminSummaryView.as_view(), name="admin-summary"),
]
