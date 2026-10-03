from __future__ import annotations

from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from judge import languages

from .models import Problem


LANGUAGE_CHOICES = [(key, lang.name) for key, lang in languages.LANGUAGES.items()]

# Seed programs, keyed by language, handed to the editor as JSON so a fresh
# submit box opens with a skeleton instead of a blank rectangle.
LANGUAGE_TEMPLATES = {key: lang.template for key, lang in languages.LANGUAGES.items()}


class RegisterForm(UserCreationForm):
    first_name = forms.CharField(max_length=60, required=False, label="Full name")

    class Meta:
        model = User
        fields = ("username", "first_name")


class SubmitForm(forms.Form):
    language = forms.ChoiceField(choices=LANGUAGE_CHOICES,
                                 initial=languages.DEFAULT_LANGUAGE)
    source = forms.CharField(widget=forms.Textarea(attrs={
        "rows": 22, "spellcheck": "false", "autocomplete": "off",
        "id": "id_source", "class": "code-source",
        "placeholder": "Write your solution here.",
    }), required=False)
    upload = forms.FileField(required=False, label="…or upload a file")

    def clean(self):
        data = super().clean()
        source = (data.get("source") or "").strip()
        upload = data.get("upload")

        if upload:
            raw = upload.read()
            if len(raw) > settings.MAX_SOURCE_BYTES:
                raise forms.ValidationError(
                    f"File is too large (limit "
                    f"{settings.MAX_SOURCE_BYTES // 1024} KB).")
            try:
                source = raw.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    source = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    raise forms.ValidationError(
                        "Could not read the file as UTF-8 text. If you saved it "
                        "from a Windows editor, choose UTF-8 encoding.") from None
            guessed = languages.guess_from_filename(upload.name)
            if guessed and not self.data.get("language_explicit"):
                data["language"] = guessed
            data["source_name"] = upload.name

        if not source:
            raise forms.ValidationError("Paste some code or upload a file.")
        if len(source.encode("utf-8")) > settings.MAX_SOURCE_BYTES:
            raise forms.ValidationError(
                f"Source is too large (limit {settings.MAX_SOURCE_BYTES // 1024} KB).")

        # Normalise CRLF: students on Windows would otherwise ship \r into the
        # compiler, which is harmless for C++ but breaks Python indentation
        # detection in some editors.
        data["source"] = source.replace("\r\n", "\n").replace("\r", "\n")
        return data


class ProblemPackageForm(forms.Form):
    archive = forms.FileField(label="Problem package (.zip)")
    code = forms.SlugField(required=False, max_length=64,
                           help_text="Leave blank to use the code in problem.json.")
    replace = forms.BooleanField(required=False, initial=True,
                                 label="Replace if it already exists")


class ProblemAdminForm(forms.ModelForm):
    """Editable package-backed settings shown on the normal Problem admin page."""

    CHECKER_CHOICES = (
        ("token", "token — whitespace-insensitive"),
        ("exact", "exact — exact text after line-ending normalisation"),
        ("float", "float — numeric comparison with tolerance"),
        ("custom", "custom — checker program supplied by package"),
    )

    checker_type = forms.ChoiceField(choices=CHECKER_CHOICES, label="Checker type")

    class Meta:
        model = Problem
        fields = "__all__"

    def clean_time_limit(self):
        value = self.cleaned_data["time_limit"]
        if value <= 0:
            raise forms.ValidationError("Time limit must be greater than 0.")
        return value

    def clean_memory_limit_mb(self):
        value = self.cleaned_data["memory_limit_mb"]
        if value <= 0:
            raise forms.ValidationError("Memory limit must be greater than 0.")
        return value


class ManualTestForm(forms.Form):
    """One editable test case stored in the problem package on disk."""

    original_name = forms.CharField(required=False, widget=forms.HiddenInput)
    name = forms.RegexField(
        regex=r"^[A-Za-z0-9_-]+$",
        max_length=64,
        label="Test name",
        help_text="Filename without .in/.ans, e.g. 01 or sample_1.",
        error_messages={"invalid": "Use only letters, numbers, _ and -."},
    )
    input_text = forms.CharField(
        required=False,
        label="Input (.in)",
        widget=forms.Textarea(attrs={"rows": 7, "class": "vLargeTextField", "spellcheck": "false"}),
    )
    answer_text = forms.CharField(
        required=False,
        label="Expected output (.ans)",
        widget=forms.Textarea(attrs={"rows": 7, "class": "vLargeTextField", "spellcheck": "false"}),
    )
    subtasks = forms.MultipleChoiceField(
        required=False,
        label="Subtask(s)",
        widget=forms.CheckboxSelectMultiple,
    )
    DELETE = forms.BooleanField(required=False, label="Delete")

    def __init__(self, *args, subtask_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        choices = [(str(i), label) for i, label in subtask_choices]
        self.fields["subtasks"].choices = choices
        if len(choices) == 1 and not self.is_bound and not self.initial.get("subtasks"):
            self.initial["subtasks"] = [choices[0][0]]

    def clean_subtasks(self):
        values = self.cleaned_data.get("subtasks") or []
        if not values and len(self.fields["subtasks"].choices) == 1:
            values = [self.fields["subtasks"].choices[0][0]]
        return [int(v) for v in values]


class SubtaskForm(forms.Form):
    """One editable subtask entry from problem.json."""

    original_index = forms.IntegerField(required=False, widget=forms.HiddenInput)
    index = forms.IntegerField(min_value=1, label="Index")
    points = forms.FloatField(min_value=0, label="Points")
    aggregation = forms.ChoiceField(
        choices=(("min", "min — all tests must pass for full subtask score"),
                 ("sum", "sum — test scores are accumulated")),
        label="Aggregation",
    )
    DELETE = forms.BooleanField(required=False, label="Delete")
