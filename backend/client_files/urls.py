from django.urls import path

from .views import CompanyCompletedArtifact, CompanyFileCreate, CompanyFileEditor, CompanyFileFinalize, CompanyFileList, CompanyFileRequest
from .company import CompanyAgreement, CompanyAgreementSigning


urlpatterns = [
    path("", CompanyFileList.as_view(), name="company_file_list"),
    path("new/", CompanyFileCreate.as_view(), name="company_file_create"),
    path("<uuid:file_id>/", CompanyFileEditor.as_view(), name="company_file_detail"),
    path("<uuid:file_id>/request/", CompanyFileRequest.as_view(), name="company_file_request"),
    path("<uuid:file_id>/finalize/", CompanyFileFinalize.as_view(), name="company_file_finalize"),
    path("<uuid:file_id>/completed.pdf", CompanyCompletedArtifact.as_view(), name="company_completed_pdf"),
    path("<uuid:file_id>/signing-history/", CompanyCompletedArtifact.as_view(history=True), name="company_signing_history"),
    path("<uuid:file_id>/countersign/", CompanyAgreement.as_view(), name="company_agreement"),
    path("<uuid:file_id>/countersign/signing/", CompanyAgreementSigning.as_view(), name="company_agreement_signing"),
    path("<uuid:file_id>/countersign/pages/<int:page_number>/", CompanyAgreement.as_view(), name="company_agreement_page"),
]
