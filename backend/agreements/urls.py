from django.urls import path

from .views import (
    CompanyAgreementDetail, CompanyAgreementList, CompanyAgreementPage,
    CompanyAgreementUpload,
)


urlpatterns = [
    path("", CompanyAgreementList.as_view(), name="company_agreement_list"),
    path("upload/", CompanyAgreementUpload.as_view(), name="company_agreement_upload"),
    path("<uuid:agreement_id>/", CompanyAgreementDetail.as_view(), name="company_agreement_detail"),
    path("<uuid:agreement_id>/pages/<int:page_number>/", CompanyAgreementPage.as_view(), name="company_agreement_page"),
]
