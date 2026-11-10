from receipt_ocr.samples.listing_cache import SampleListingCache


def test_unchanged_signatures_still_recheck_after_bounded_interval(tmp_path):
    clock = [0]
    cache = SampleListingCache(lambda: [tmp_path], clock=lambda: clock[0])
    values = {'items': [1]}
    first = cache.read(None, lambda: values)
    values = {'items': [2]}
    clock[0] = 29
    assert cache.read(first['revision'], lambda: values)['unchanged'] is True
    clock[0] = 30
    assert cache.read(first['revision'], lambda: values)['items'] == [2]


def test_change_during_listing_never_marks_older_data_as_current(tmp_path):
    source = tmp_path / 'source'
    source.write_text('first')
    cache = SampleListingCache(lambda: [source])

    def overlapping_load():
        data = {'value': source.read_text()}
        source.write_text('second generation')
        return data

    first = cache.read(None, overlapping_load)
    assert first['value'] == 'first'
    next_value = cache.read(first['revision'], lambda: {'value': source.read_text()})
    assert next_value['value'] == 'second generation'
    assert next_value['revision'] != first['revision']
