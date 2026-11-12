"""Apply saved business settings around an explicitly supplied recognition service."""
from dataclasses import dataclass
from copy import deepcopy

from ..domain.seal_standards import apply_standard_check
from ..domain.decision_provenance import record_evaluation, standards_catalog_reference, PROVENANCE_KEY
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
        standards = deepcopy(self.database.list_seal_standards())
        with company_name_correction_scope(standards), seal_layout_settings_scope(standards):
            result = self.recognition.recognize(
                *args, recognition_config=recognition_config,
                previous_fields=previous_fields, **kwargs,
            )
        requirement = (result.get('fields') or {}).get('签章要求', '')
        record_evaluation(result, 'recognition_context', ('seal_layout', 'company_name_correction'),
                          origin='recorded', standards_catalog=standards_catalog_reference(standards))
        result = apply_standard_check(result, self.database.get_seal_standard(requirement), record_provenance=True)
        if isinstance(result.get('_assembly_snapshot'), dict):
            result['_assembly_snapshot'][PROVENANCE_KEY] = deepcopy(result[PROVENANCE_KEY])
        return result
