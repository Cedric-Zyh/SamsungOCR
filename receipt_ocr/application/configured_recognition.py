"""Apply saved business settings around an explicitly supplied recognition service."""
from dataclasses import dataclass

from ..domain.seal_standards import apply_standard_check
from ..recognition.seal.layout_settings import seal_layout_settings_scope
from ..recognition.seal.postprocess.name_correction import company_name_correction_scope


@dataclass
class ConfiguredRecognition:
    database: object
    analyzer: object
    recognition: object
    restore_remote_settings: object

    def restore_credentials(self):
        # Another application window can save new credentials between runs.
        self.restore_remote_settings(self.database)
        saved = self.database.get_json_setting('qingtong_credentials', None)
        if isinstance(saved, dict):
            self.analyzer.seal_api.api_key = saved.get('api_key', '')

    def recognize(self, *args, recognition_config=None, previous_fields=None, **kwargs):
        self.restore_credentials()
        standards = self.database.list_seal_standards()
        with company_name_correction_scope(standards), seal_layout_settings_scope(standards):
            result = self.recognition.recognize(
                *args, recognition_config=recognition_config,
                previous_fields=previous_fields, **kwargs,
            )
        requirement = (result.get('fields') or {}).get('签章要求', '')
        return apply_standard_check(result, self.database.get_seal_standard(requirement))
