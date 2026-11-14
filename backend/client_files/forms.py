from django import forms
from agreements.models import TemplateVersion

from .models import ClientFile


class ClientFileDraftForm(forms.ModelForm):
    class Meta:
        model = ClientFile
        fields = (
            "reference", "title", "client_name", "client_email",
            "signer_name", "signer_email",
            "template_version", "company_signer_name", "company_signer_email",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["template_version"].queryset = TemplateVersion.objects.filter(
            agreement__company_id=self.instance.company_id,
        )
        self.fields["template_version"].error_messages["invalid_choice"] = "Choose a published version from this workspace."

    def clean_reference(self):
        reference = self.cleaned_data["reference"]
        existing = ClientFile.objects.filter(
            company_id=self.instance.company_id,
            reference__iexact=reference,
        ).exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError("This reference is already used in your workspace.")
        return reference
