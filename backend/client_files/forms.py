from django import forms

from .models import ClientFile


class ClientFileDraftForm(forms.ModelForm):
    class Meta:
        model = ClientFile
        fields = (
            "reference", "title", "client_name", "client_email",
            "signer_name", "signer_email",
        )

    def clean_reference(self):
        reference = self.cleaned_data["reference"]
        existing = ClientFile.objects.filter(
            company_id=self.instance.company_id,
            reference__iexact=reference,
        ).exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError("This reference is already used in your workspace.")
        return reference
