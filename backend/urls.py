from django.urls import path
from django.views.generic import RedirectView

from login.client import ClientLogin, ClientLogout, ClientSession
from login.company import CompanyLogin, CompanyLogout, CompanySession


urlpatterns = [
    path("", RedirectView.as_view(pattern_name="company_login", permanent=False)),
    path("company/login/", CompanyLogin.as_view(), name="company_login"),
    path("company/session/", CompanySession.as_view(), name="company_session"),
    path("company/logout/", CompanyLogout.as_view(), name="company_logout"),
    path("client/login/", ClientLogin.as_view(), name="client_login"),
    path("client/login/<uuid:invitation_id>/", ClientLogin.as_view(), name="client_invitation"),
    path("client/session/", ClientSession.as_view(), name="client_session"),
    path("client/logout/", ClientLogout.as_view(), name="client_logout"),
]
