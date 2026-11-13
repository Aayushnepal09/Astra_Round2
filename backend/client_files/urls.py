from django.urls import path

from .views import CompanyFileCreate, CompanyFileEditor, CompanyFileList


urlpatterns = [
    path("", CompanyFileList.as_view(), name="company_file_list"),
    path("new/", CompanyFileCreate.as_view(), name="company_file_create"),
    path("<uuid:file_id>/", CompanyFileEditor.as_view(), name="company_file_detail"),
]
