# coding=utf-8
"""Two models with a plain FK, so an inline admin has a reverse relation to walk."""
from django.db import models


class Author(models.Model):
    name = models.CharField(max_length=100)
    # Deliberately NOT in AuthorAdmin.list_display: this is the stand-in for
    # User.password in the #7369 leak. If it ever reaches a response, the
    # ?_fields= filter is not doing its job.
    secret = models.CharField(max_length=100, default='top-secret-value')

    class Meta:
        app_label = 'xadmin_fixture'

    def __str__(self):
        return self.name


class Book(models.Model):
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name='books')
    title = models.CharField(max_length=100)

    class Meta:
        app_label = 'xadmin_fixture'

    def __str__(self):
        return self.title
