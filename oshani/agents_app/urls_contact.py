"""URL patterns for contact form."""
from django.urls import path
from .views_contact import contact_form_submit

urlpatterns = [
    path('', contact_form_submit, name='contact-form-submit'),
]











