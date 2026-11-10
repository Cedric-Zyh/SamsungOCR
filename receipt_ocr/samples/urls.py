"""Map generated recognition URLs to isolated sample storage."""

def sample_urls(value):
    if isinstance(value, dict):
        return {key: sample_urls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sample_urls(item) for item in value]
    if isinstance(value, str) and value.startswith(("/files/previews/", "/files/artifacts/")):
        return value.replace("/files/", "/files/samples/", 1)
    return value
