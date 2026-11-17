from django.urls import path

from . import views

urlpatterns = [
    path("book/", views.choose_service, name="book"),
    path("book/<slug:slug>/", views.choose_time, name="book_time"),
    path("book/<slug:slug>/day/", views.day_panel, name="book_day"),
    path("book/<slug:slug>/ends/", views.end_options, name="book_ends"),
    path("book/<slug:slug>/details/", views.details, name="book_details"),
    path("booking/<str:token>/", views.manage, name="booking_manage"),
    path("booking/<str:token>/cancel/", views.cancel, name="booking_cancel"),
]
