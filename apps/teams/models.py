from django.db import models


class Team(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=60, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
