"""Shared statement period validation and safe spreadsheet cells."""
from django import forms


class LedgerPeriodForm(forms.Form):
    from_date = forms.DateField(required=False, input_formats=['%Y-%m-%d'])
    to_date = forms.DateField(required=False, input_formats=['%Y-%m-%d'])

    def __init__(self, params):
        super().__init__({'from_date': params.get('from', ''), 'to_date': params.get('to', '')})

    def clean(self):
        data = super().clean()
        start, end = data.get('from_date'), data.get('to_date')
        if start and end and start > end:
            raise forms.ValidationError('From date must be on or before To date.')
        return data


def spreadsheet_text(value):
    """Keep user-entered text from becoming a spreadsheet formula on CSV open."""
    value = str(value or '')
    if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
        return "'" + value
    return value
