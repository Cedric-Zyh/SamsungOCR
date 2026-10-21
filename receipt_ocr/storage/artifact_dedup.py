"""Reuse identical completed evidence within one recognition result.

Each recognition has its own output directory. Only completed output is
compacted; source images, historical runs and physical-stamp identities stay
independent. No hard links or image re-encoding are involved.
"""
from hashlib import sha256
from pathlib import Path


IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}


def deduplicate_artifacts(result, directory, url_prefix):
    directory = Path(directory)
    if not directory.is_dir():
        return result
    protected_urls = set()

    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                # Candidate identities are derived from these source URLs.
                if key in {'original_url', 'color_isolated_url'} and isinstance(item, str):
                    protected_urls.add(item)
                collect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item)

    collect(result)
    prefix = url_prefix.rstrip('/') + '/'

    def url(path):
        return prefix + path.relative_to(directory).as_posix()

    files = [p for p in directory.rglob('*') if p.is_file() and not p.is_symlink()
             and p.suffix.lower() in IMAGE_SUFFIXES]
    files.sort(key=lambda p: (url(p) not in protected_urls, str(p)))
    seen, replacements, redundant = {}, {}, []
    for path in files:
        try:
            content = path.read_bytes()
        except OSError:
            continue  # An optional optimization must not fail a successful OCR.
        key = (path.suffix.lower(), len(content), sha256(content).digest())
        canonical = seen.get(key)
        if canonical is None:
            seen[key] = path
        elif url(path) not in protected_urls:
            # Also cover any diagnostic local paths retained in provider output.
            replacements[url(path)] = url(canonical)
            replacements[str(path)] = str(canonical)
            replacements[str(path.resolve())] = str(canonical.resolve())
            redundant.append(path)

    def rewrite(value):
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, tuple):
            return tuple(rewrite(item) for item in value)
        if isinstance(value, str):
            base, separator, query = value.partition('?')
            return replacements.get(base, base) + separator + query
        return value

    if not redundant:
        return result
    optimized = rewrite(result)
    for path in redundant:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass  # Leaving an extra copy is safe; every new reference is valid.
    return optimized
