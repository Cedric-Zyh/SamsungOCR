from tools.analysis.operations.check_architecture import violations


def test_import_direction_is_clean():
    assert violations() == []
