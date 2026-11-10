from django.urls import path

from . import views

urlpatterns = [
    path("book/", views.choose_service, name="book"),
    path("book/<slug:slug>/", views.choose_time, name="book_time"),
    path("book/<slug:slug>/slots/", views.slots, name="book_slots"),
    path("book/<slug:slug>/details/", views.details, name="book_details"),
    path("booking/<str:token>/", views.manage, name="booking_manage"),
    path("booking/<str:token>/cancel/", views.cancel, name="booking_cancel"),
]
