from tools.benchmark_page_inputs import evidence, summarize


def sample(variant, checks, *, name="a.jpg", seconds=2, footer=0.5):
    return dict(
        filename=name,
        variant=variant,
        seconds=seconds,
        evidence=dict(checks=checks, footer_y=footer),
    )


def test_quality_gate_cannot_trade_one_known_correct_field_for_another():
    report = summarize(
        [
            sample(
                "original", {"field:运单号": True, "field:客户名称": False}, seconds=10
            ),
            sample("detect2048", {"field:运单号": False, "field:客户名称": True}),
        ]
    )["detect2048"]
    assert report["paired_speedup"] == 5
    assert report["correct"]["field"] == 1
    assert report["passes_quality_gate"] is False
    assert report["regressions"] == [{"filename": "a.jpg", "check": "field:运单号"}]


def test_incomplete_pairing_cannot_pass_quality_gate():
    report = summarize(
        [
            sample("original", {"document_type": True}, name="a.jpg"),
            sample("original", {"document_type": True}, name="b.jpg"),
            sample("detect2048", {"document_type": True}, name="a.jpg"),
        ]
    )
    assert report["detect2048"]["passes_quality_gate"] is False


def test_footer_anchor_regression_blocks_even_with_identical_text():
    report = summarize(
        [
            sample("original", {"field:x": True}, footer=0.5),
            sample("detect2048", {"field:x": True}, footer=0.53),
        ]
    )["detect2048"]
    assert report["passes_quality_gate"] is False
    assert report["footer_anchor_changes"] == ["a.jpg"]


def test_extra_product_rows_are_a_quality_regression(monkeypatch):
    import tools.benchmark_page_inputs as module

    monkeypatch.setattr(module, "parse_fields", lambda rows: {})
    monkeypatch.setattr(
        module,
        "parse_product_table",
        lambda rows: {"rows": [{"values": {"行号": "10"}}, {"values": {"行号": "20"}}]},
    )
    result = evidence([], {"product_rows": [{"行号": "10"}]}, "unknown")
    assert result["checks"]["product:10:行号"]
    assert result["checks"]["product_row_count"] is False


def test_profile_preserves_prediction_objects_and_records_generator_work(monkeypatch):
    import tools.benchmark_page_inputs as module

    times = iter([10, 12.5])
    monkeypatch.setattr(module.time, "perf_counter", lambda: next(times))
    item = {"rec_text": "原始识别结果"}
    totals = {}
    wrapped = module.TimedPredictor(lambda value: iter([value]), totals, "recognition")
    assert next(iter(list(wrapped(item)))) is item
    assert totals == {"recognition": 2.5}
