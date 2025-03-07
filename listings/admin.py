from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import Property  # Import your models
from .models import User  # Import your models


admin.site.register(Property)  # Register the model
admin.site.register(User)  # Register the model

