from django.urls import path

from .views import CompanyCompletedArtifact, CompanyFileCreate, CompanyFileEditor, CompanyFileFinalize, CompanyFileLifecycle, CompanyFileList, CompanyFileRequest, CompanyRequestHistory
from .company import CompanyAgreement, CompanyAgreementSigning


urlpatterns = [
    path("", CompanyFileList.as_view(), name="company_file_list"),
    path("new/", CompanyFileCreate.as_view(), name="company_file_create"),
    path("<uuid:file_id>/", CompanyFileEditor.as_view(), name="company_file_detail"),
    path("<uuid:file_id>/request/", CompanyFileRequest.as_view(), name="company_file_request"),
    path("<uuid:file_id>/request/cancel/", CompanyFileLifecycle.as_view(), name="company_file_cancel"),
    path("<uuid:file_id>/request/prepare-replacement/", CompanyFileLifecycle.as_view(action="prepare"), name="company_file_replacement"),
    path("<uuid:file_id>/requests/", CompanyRequestHistory.as_view(), name="company_request_history"),
    path("<uuid:file_id>/requests/<uuid:request_id>/completed.pdf", CompanyCompletedArtifact.as_view(), name="company_request_completed_pdf"),
    path("<uuid:file_id>/requests/<uuid:request_id>/signing-history/", CompanyCompletedArtifact.as_view(history=True), name="company_request_signing_history"),
    path("<uuid:file_id>/finalize/", CompanyFileFinalize.as_view(), name="company_file_finalize"),
    path("<uuid:file_id>/completed.pdf", CompanyCompletedArtifact.as_view(), name="company_completed_pdf"),
    path("<uuid:file_id>/signing-history/", CompanyCompletedArtifact.as_view(history=True), name="company_signing_history"),
    path("<uuid:file_id>/countersign/", CompanyAgreement.as_view(), name="company_agreement"),
    path("<uuid:file_id>/countersign/signing/", CompanyAgreementSigning.as_view(), name="company_agreement_signing"),
    path("<uuid:file_id>/countersign/pages/<int:page_number>/", CompanyAgreement.as_view(), name="company_countersign_page"),
]
