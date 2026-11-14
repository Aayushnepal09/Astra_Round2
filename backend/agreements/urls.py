from django.urls import path

from .views import (
    CompanyAgreementDetail, CompanyAgreementList, CompanyAgreementPage,
    CompanyAgreementUpload, CompanyAgreementFields,
    CompanyTemplatePublish, CompanyTemplateList, CompanyTemplateDetail, CompanyTemplatePage,
)


urlpatterns = [
    path("", CompanyAgreementList.as_view(), name="company_agreement_list"),
    path("upload/", CompanyAgreementUpload.as_view(), name="company_agreement_upload"),
    path("published/", CompanyTemplateList.as_view(), name="company_template_list"),
    path("published/<uuid:version_id>/", CompanyTemplateDetail.as_view(), name="company_template_detail"),
    path("published/<uuid:version_id>/pages/<int:page_number>/", CompanyTemplatePage.as_view(), name="company_template_page"),
    path("<uuid:agreement_id>/", CompanyAgreementDetail.as_view(), name="company_agreement_detail"),
    path("<uuid:agreement_id>/pages/<int:page_number>/", CompanyAgreementPage.as_view(), name="company_agreement_page"),
    path("<uuid:agreement_id>/fields/", CompanyAgreementFields.as_view(), name="company_agreement_fields"),
    path("<uuid:agreement_id>/publish/", CompanyTemplatePublish.as_view(), name="company_template_publish"),
]
