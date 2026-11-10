"""Build explicit sample inputs from existing web-test fixtures."""
from receipt_ocr.samples.dependencies import SampleDependencies, SamplePaths
from receipt_ocr.samples.service import SampleCenter as _SampleCenter


def sample_dependencies(ctx):
    analyzer = getattr(ctx, 'analyzer', None)
    return SampleDependencies(
        database=ctx.database,
        paths=SamplePaths(ctx.DATA_DIR, ctx.GROUND_TRUTH_PATH, frozenset(ctx.ALLOWED_EXTENSIONS)),
        analyze=ctx._configured_analyze,
        project_result=getattr(ctx, '_project_paginated_result', lambda value: value),
        seal_api_enabled=bool(analyzer and analyzer.seal_api.enabled),
        logger=ctx.app.logger,
    )


def SampleCenter(ctx):
    return _SampleCenter(sample_dependencies(ctx))
