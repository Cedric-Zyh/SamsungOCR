"""Public stamp orientation strategies and settings."""

from receipt_ocr.recognition.seal.preprocess.orientation_settings import DEFAULT_SEAL_ORIENTATION_MODE, SEAL_ORIENTATION_MODES, resolve_seal_orientation_mode, seal_orientation_mode_label
from receipt_ocr.recognition.seal.preprocess.orientation_angles import decide_orientation, choose_round_stamp_angle, choose_rectangular_stamp_angle
from receipt_ocr.recognition.seal.preprocess.round import prepare_round_stamp
from receipt_ocr.recognition.seal.preprocess.ellipse import prepare_ellipse_stamp
from receipt_ocr.recognition.seal.preprocess.doc_orientation import prepare_round_stamp_doc_ori
from receipt_ocr.recognition.seal.preprocess.combined_orientation import prepare_round_stamp_combined
from receipt_ocr.recognition.seal.preprocess.rectangle_orientation import prepare_rectangular_stamp, prepare_rectangles
from receipt_ocr.providers.orientation import DOC_ORIENTATION_PROVIDER

__all__ = ['DEFAULT_SEAL_ORIENTATION_MODE', 'SEAL_ORIENTATION_MODES', 'DOC_ORIENTATION_PROVIDER', 'resolve_seal_orientation_mode', 'seal_orientation_mode_label', 'decide_orientation', 'choose_round_stamp_angle', 'choose_rectangular_stamp_angle', 'prepare_rectangular_stamp', 'prepare_round_stamp', 'prepare_ellipse_stamp', 'prepare_round_stamp_doc_ori', 'prepare_round_stamp_combined', 'prepare_rectangles']
