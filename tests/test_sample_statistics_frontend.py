"""Statistics filtering, shared denominators and UI-to-export scope regressions."""
from test_frontend_behavior import run_node


def test_sample_statistics_frontend_metrics_and_interactions():
    run_node('--test', 'tests/sample_statistics.cjs', 'tests/sample_statistics_metrics.cjs',
             'tests/sample_statistics_view.cjs', 'tests/sample_statistics_preview.cjs')
