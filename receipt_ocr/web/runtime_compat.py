"""Read-only names retained for scripts predating ApplicationRuntime.

New callers construct an ApplicationRuntime and call application.install_runtime
before initialization. Assigning a legacy module name is not dependency injection.
This adapter never owns resources or synchronizes aliases during a request.
"""

_RESOURCES = {
    'database': 'database',
    'analyzer': 'analyzer',
    'seal_reference_matcher': 'matcher',
    'recognition_service': 'recognition',
    'job_store': 'job_store',
    'job_worker': 'job_worker',
    '_initialized': 'initialized',
    'RUNTIME_PATHS': 'paths',
}
_PATHS = {
    'RESOURCE_DIR': 'resource_dir',
    'DATA_DIR': 'data_dir',
    'STORAGE_DIR': 'storage_dir',
    'UPLOAD_DIR': 'upload_dir',
    'PREVIEW_DIR': 'preview_dir',
    'ARTIFACT_DIR': 'artifact_dir',
    'EXPORT_DIR': 'export_dir',
    'DATABASE_PATH': 'database_path',
    'GROUND_TRUTH_PATH': 'ground_truth_path',
}


def legacy_attribute(runtime, name):
    if name in _RESOURCES:
        return getattr(runtime, _RESOURCES[name])
    if name in _PATHS:
        return getattr(runtime.paths, _PATHS[name])
    raise AttributeError(f'no legacy application attribute {name!r}')
