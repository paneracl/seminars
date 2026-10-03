from django.contrib.auth import views as auth_views
from django.urls import path

from . import contest_views, views

urlpatterns = [
    path("", views.problem_list, name="problem_list"),
    path("problems/<slug:code>/", views.problem_detail, name="problem_detail"),
    path("problems/<slug:code>/submit/", views.submit, name="submit"),
    path("problems/<slug:code>/run/", views.run_code, name="run_code"),
    path("problems/<slug:code>/statement/<slug:language>.pdf",
         views.problem_statement_pdf, name="problem_statement_pdf"),

    path("contests/", contest_views.contest_list, name="contest_list"),
    path("contests/<slug:slug>/", contest_views.contest_detail, name="contest_detail"),
    path("contests/<slug:slug>/register/", contest_views.contest_register,
         name="contest_register"),
    path("contests/<slug:slug>/scoreboard/", contest_views.contest_scoreboard,
         name="contest_scoreboard"),
    path("contests/<slug:slug>/clock/", contest_views.contest_clock, name="contest_clock"),
    path("contests/<slug:slug>/<str:label>/", contest_views.contest_problem,
         name="contest_problem"),
    path("contests/<slug:slug>/<str:label>/submit/", contest_views.contest_submit,
         name="contest_submit"),
    path("contests/<slug:slug>/<str:label>/run/", contest_views.contest_run_code,
         name="contest_run_code"),
    path("contests/<slug:slug>/<str:label>/statement/<slug:language>.pdf",
         contest_views.contest_statement_pdf, name="contest_statement_pdf"),

    path("submissions/", views.submission_list, name="submission_list"),
    path("submissions/<int:pk>/", views.submission_detail, name="submission_detail"),
    path("submissions/<int:pk>/status/", views.submission_status, name="submission_status"),

    path("accounts/register/", views.register, name="register"),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
]
