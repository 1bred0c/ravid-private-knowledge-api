from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Application user; extend here instead of replacing Django auth later."""

    pass
