"""Execution validity survives assembly, public JSON and saved-evidence replay."""

from copy import deepcopy
import json

import pytest

from receipt_ocr.application.assembly import assemble_result
from receipt_ocr.application.context import DocumentContext
from receipt_ocr.application.deferred_document import combine_saved, empty_snapshot
from receipt_ocr.application.retained_providers import retained_branch, merge_retained_result
from receipt_ocr.application.variant_projection import public_variant, replay_variant
from receipt_ocr.domain.date_selection import refresh_date_selection
from receipt_ocr.domain.printed_field_selection import select_printed_fields
from receipt_ocr.domain.check_comparison import compare_machine_seals
from receipt_ocr.domain.seal_standards import apply_standard_check, seal_candidates
from receipt_ocr.domain.results import StageOutcome
from receipt_ocr.domain.saved_evidence import SavedStageEvidence
from receipt_ocr.domain.stages import STAGES


def json_copy(value):
    return json.loads(json.dumps(value))


def date_payload(actual="2026-11-09"):
    return {"date_check": {"required": "2026-11-09", "actual": actual,
                           "status": "匹配" if actual == "2026-11-09" else "不匹配", "reliable": True}}


def assemble_dates(tmp_path, variants):
    context = DocumentContext(tmp_path / "never-opened.png")
    context.document_type = {"type": "receipt", "reliable": True}
    context.has_footer = True
    context.footer_detection = {"present": True, "method": "printed_requirement"}
    config = {stage: [] for stage in STAGES}
    config.update(date=[variant["method"] for variant in variants], acceptance={"date_match_mode": "any"})
    return assemble_result(context, config, {"date": variants}, [], {},
                           {"要求到货": "2026-11-09"}, None, None, {})


@pytest.mark.parametrize("marker", [{"pending": True}, {"error": "stale request failure"},
    {"simulated": True}, {"mode": "mock"}, {"status": "paused"}, {"execution_status": "skipped"}])
@pytest.mark.parametrize("location", ["variant", "details"])
def test_assembly_json_and_date_refresh_never_revive_an_inactive_old_reading(tmp_path, marker, location):
    stale = {"method": "danzhengtong", "result": date_payload()}
    (stale if location == "variant" else stale["result"]).update(marker)
    current = {"method": "paddle_v6", "result": date_payload("2026-11-08")}
    before = deepcopy([stale, current])
    assembled = assemble_dates(tmp_path, [stale, current])
    assert assembled["date_check"]["actual"] == "2026-11-08"
    saved = json_copy(assembled)
    variant = saved["recognition_variants"]["date"][0]
    evidence = SavedStageEvidence.from_variant("date", variant)
    assert not evidence.active
    projected = variant if location == "variant" else variant["details"]
    assert all(projected[key] == value for key, value in marker.items())
    refreshed = refresh_date_selection(saved)
    assert refreshed["date_check"]["actual"] == "2026-11-08"
    assert refreshed["date_check"]["status"] == "不匹配"
    assert [stale, current] == before
    assert refresh_date_selection(refreshed) == refreshed


@pytest.mark.parametrize("outcome", [
    StageOutcome("date", "danzhengtong", "failed", payload=date_payload()),
    StageOutcome("date", "danzhengtong", "failed", payload=date_payload(), error="failed after older result"),
    StageOutcome("date", "danzhengtong", "skipped", payload=date_payload()),
    StageOutcome("date", "danzhengtong", "pending", payload=date_payload()),
])
def test_explicit_inactive_state_survives_public_and_internal_round_trip(outcome):
    public = json_copy(public_variant("date", outcome))
    assert not SavedStageEvidence.from_variant("date", public).active
    internal = replay_variant("date", public)
    assert "details" not in internal and "value" not in internal
    assert not SavedStageEvidence.from_variant("date", internal).active
    assert StageOutcome.from_variant("date", internal).state == outcome.state
    assert json_copy(public_variant("date", internal)) == public


@pytest.mark.parametrize("variant", [
    {"method": "qingtong", "error": "timeout"},
    {"method": "danzhengtong", "pending": True, "details": None, "error": None, "value": None},
    {"method": "paddle_v6", "execution_status": "skipped", "details": None},
])
def test_failed_or_waiting_variants_without_payload_keep_the_legacy_public_slots(variant):
    public = public_variant("seal", variant)
    assert public["details"] is None and public["value"] is None
    assert "error" in public
    assert not SavedStageEvidence.from_variant("seal", public).active
    assert json_copy(public_variant("seal", replay_variant("seal", public))) == json_copy(public)


def test_public_projection_is_allowlisted_while_internal_snapshot_metadata_stays_lossless():
    variant = {"method": "paddle_v6", "source": "local", "backend": "本地 OCR", "provider": "local",
               "request_trace": {"private_unknown": [1, None]}, "_credentials": {"token": "fake-secret"},
               "_assembly_snapshot": {"nested": "internal only"}, "vendor_extension": {"new": True},
               "result": {**date_payload(), "date_ocr_texts": ["2026年11月9日"],
                          "pending": True, "_credentials": {"token": "fake-payload-secret"},
                          "vendor_extension": {"raw": [1, None]}}}
    before = deepcopy(variant)
    outcome = StageOutcome.from_variant("date", variant)
    assert outcome.to_variant() == before
    public = public_variant("date", outcome)
    assert public["source"] == "local" and public["backend"] == "本地 OCR" and public["provider"] == "local"
    assert public["details"]["pending"] is True
    assert public["details"]["date_ocr_texts"] == ["2026年11月9日"]
    assert public["details"]["danzhengtong_seal"] is None  # Stable historical slot.
    for key in ("_credentials", "_assembly_snapshot", "vendor_extension", "request_trace"):
        assert key not in public and key not in public["details"]
    public["details"]["date_check"]["actual"] = "changed display"
    assert variant == before and outcome.to_variant() == before


def test_public_history_decodes_metadata_without_treating_display_value_as_observation():
    public = {"method": "vision", "value": ["a fabricated display", "匹配"], "error": None,
              "simulated": True, "vendor_extension": {"unknown": [1, None]}, "details": date_payload()}
    internal = replay_variant("date", json_copy(public))
    assert internal["result"]["date_check"]["actual"] == "2026-11-09"
    assert internal["simulated"] is True and internal["vendor_extension"] == public["vendor_extension"]
    assert "details" not in internal and "value" not in internal
    assert not SavedStageEvidence.from_variant("date", internal).active


@pytest.mark.parametrize("details", [None, {}])
def test_legacy_details_placeholders_can_fall_back_to_internal_result(details):
    variant = {"method": "vision", "details": details, "result": date_payload()}
    outcome = StageOutcome.from_variant("date", variant)
    assert outcome.payload == date_payload()
    assert outcome.to_variant()["result"] == date_payload()


def test_nonempty_public_details_have_the_same_precedence_as_saved_evidence_reader():
    variant = {"method": "vision", "result": date_payload(), "details": date_payload("2026-11-08")}
    outcome = StageOutcome.from_variant("date", variant)
    assert outcome.payload == SavedStageEvidence.from_variant("date", variant).details
    assert public_variant("date", outcome)["value"][0] == "2026-11-08"


def test_replay_undoes_only_public_container_defaults_and_retains_trace_presence():
    public = public_variant("date", StageOutcome.completed("date", "vision", date_payload()))
    before = deepcopy(public)
    assert public["details"]["processing_artifacts"] is None
    internal = replay_variant("date", public)
    assert "processing_artifacts" not in internal["result"]
    assert "danzhengtong_seal" in internal["result"] and internal["result"]["danzhengtong_seal"] is None
    assert json_copy(public_variant("date", internal)) == json_copy(public)
    assert public == before


def test_skipped_field_check_survives_public_projection_and_cannot_vote_for_a_field():
    skipped = StageOutcome("fields", "vision", "skipped", payload={"fields": {"客户名称": "旧客户"},
                                                                 "fields_check": None})
    current = StageOutcome.completed("fields", "paddle_v6", {"fields": {"客户名称": "有效客户"}})
    public = json_copy([public_variant("fields", skipped), public_variant("fields", current)])
    assert public[0]["details"]["fields_check"]["execution_status"] == "skipped"
    assert select_printed_fields(public)["fields"]["客户名称"] == "有效客户"
    replayed = [replay_variant("fields", value) for value in public]
    assert select_printed_fields(replayed)["fields"]["客户名称"] == "有效客户"


@pytest.mark.parametrize("marker", [{"simulated": True}, {"mode": "mock"}, {"status": "paused"},
                                    {"error": "old failure"}, {"execution_status": "skipped"}])
def test_retained_provider_replay_preserves_validity_metadata(tmp_path, marker):
    stale = {"method": "vision", "result": date_payload(), **marker}
    current = {"method": "paddle_v6", "result": date_payload("2026-11-08")}
    assembled = json_copy(assemble_dates(tmp_path, [stale, current]))
    branch = retained_branch([(assembled, {})], {stage: [] for stage in STAGES}, "source-hash")
    retained = next(value for value in branch["snapshot"]["results"]["date"] if value["method"] == "vision")
    assert all(retained[key] == value for key, value in marker.items())
    assert not SavedStageEvidence.from_variant("date", retained).active
    replayed = combine_saved(tmp_path / "never-opened.png", "receipt.png", json_copy(branch["snapshot"]))
    assert replayed["date_check"]["actual"] == "2026-11-08"
    assert replayed["date_check"]["status"] == "不匹配"


@pytest.mark.parametrize("has_provenance", [False, True])
def test_retained_snapshot_keeps_existing_context_and_marks_missing_rules_unrecorded(tmp_path, has_provenance):
    document = assemble_dates(tmp_path, [{"method": "paddle_v6", "result": date_payload("2026-11-08")}])
    # This test only checks the handoff, not the provenance schema's contents.
    document.pop("decision_provenance", None)
    if has_provenance:
        document["decision_provenance"] = {"schema_version": 1, "origin": "recorded",
                                           "evaluations": [{"unknown_future_field": [1, None]}]}
    branch = retained_branch([(document, {})], {stage: [] for stage in STAGES}, "source-hash")
    saved = branch["snapshot"]["decision_provenance"]
    if has_provenance:
        assert {key: saved[key] for key in ("schema_version", "origin", "evaluations")} == document["decision_provenance"]
        assert saved["retained_sources"]["date"]["paddle_v6"] == document["decision_provenance"]
        saved["evaluations"].clear()
        assert document["decision_provenance"]["evaluations"]
    else:
        unknown = {"schema_version": 1, "origin": "unrecorded", "evaluations": []}
        assert saved["origin"] == "unrecorded" and saved["evaluations"] == []
        assert saved["retained_sources"]["date"]["paddle_v6"] == unknown


@pytest.mark.parametrize("marker", [{"pending": True}, {"error": "old request failed"},
                                   {"simulated": True}, {"execution_status": "failed"}])
def test_only_inactive_date_source_never_becomes_a_matching_fallback(tmp_path, marker):
    variant = {"method": "danzhengtong", "result": date_payload(), **marker}
    output = json_copy(assemble_dates(tmp_path, [variant]))
    assert output["date_check"].get("actual") == ""
    assert output["date_check"]["reliable"] is False
    assert output["date_check"]["status"] != "匹配"
    assert output["recognition_variants"]["date"][0]["details"]["date_check"] == date_payload()["date_check"]
    assert refresh_date_selection(output)["date_check"] == output["date_check"]
    if marker.get("pending"):
        assert output["recognition_status"]["date"] == "等待单证通结果"
        assert output["overall"] == "待结果"


@pytest.mark.parametrize("mode", ["any", "all"])
def test_pending_without_payload_remains_visible_and_counts_in_all_mode(tmp_path, mode):
    waiting = StageOutcome("date", "danzhengtong", "pending")
    current = StageOutcome.completed("date", "paddle_v6", date_payload())
    context = DocumentContext(tmp_path / "never-opened.png")
    context.document_type = {"type": "receipt", "reliable": True}
    context.has_footer = True
    config = {stage: [] for stage in STAGES}
    config.update(date=["danzhengtong", "paddle_v6"], acceptance={"date_match_mode": mode})
    output = assemble_result(context, config, {"date": [waiting, current]}, [], {},
                             {"要求到货": "2026-11-09"}, None, None, {})
    assert output["recognition_variants"]["date"][0]["pending"] is True
    assert output["recognition_variants"]["date"][0]["details"] is None
    assert output["date_check"]["status"] == ("匹配" if mode == "any" else "需人工复核")
    assert output["date_check"]["reliable"] is (mode == "any")


@pytest.mark.parametrize("marker", [{"pending": True}, {"error": "old request failed"}, {"simulated": True}])
@pytest.mark.parametrize("mode", ["any", "all"])
def test_invalid_matching_seal_cannot_outvote_a_valid_local_mismatch_or_survive_replay(tmp_path, marker, mode):
    requirement = "测试客户收货专用章"
    old = compare_machine_seals(requirement, [requirement])
    old.update(source="清瞳", backend="清瞳", dual_check={"selected": {"ocr": {"matched": True}}})
    valid = compare_machine_seals(requirement, ["另一客户收货专用章"], local=True)
    valid.update(source="本地", backend="本地")
    variants = [{"method": "qingtong", "result": {"seal_check": old}, **marker},
                {"method": "paddle_v6", "result": {"seal_check": valid}}]
    context = DocumentContext(tmp_path / "never-opened.png")
    context.document_type = {"type": "receipt", "reliable": True}
    context.has_footer = True
    context.footer_detection = {"present": True, "method": "printed_requirement"}
    config = {stage: [] for stage in STAGES}
    config.update(seal=["qingtong", "paddle_v6"], acceptance={"seal_match_mode": mode})
    output = assemble_result(context, config, {"seal": variants}, [], {}, {"签章要求": requirement}, None, None, {})
    assert output["seal_check"]["recognized"] == valid["recognized"]
    assert output["seal_check"]["status"] == ("不匹配" if mode == "any" else "需人工复核")
    assert output["recognition_variants"]["seal"][0]["details"]["seal_check"] == old
    saved = json_copy(output)
    branch = retained_branch([(saved, {})], {stage: [] for stage in STAGES}, "source-hash")
    replayed = combine_saved(tmp_path / "never-opened.png", "receipt.png", json_copy(branch["snapshot"]))
    assert replayed["seal_check"]["recognized"] == valid["recognized"]
    assert replayed["seal_check"]["status"] == output["seal_check"]["status"]


@pytest.mark.parametrize("state", ["failed", "pending"])
def test_latest_empty_outcome_blocks_an_older_successful_provider_reading(tmp_path, state):
    older = json_copy(assemble_dates(tmp_path, [{"method": "vision", "result": date_payload()}]))
    latest = deepcopy(older)
    latest["recognition_variants"]["date"] = [public_variant("date", StageOutcome("date", "vision", state))]
    before = deepcopy([latest, older])
    branch = retained_branch([(latest, {}), (older, {})], {stage: [] for stage in STAGES}, "source-hash")
    observations = branch["snapshot"]["results"]["date"]
    assert len(observations) == 1 and observations[0].get("result") is None
    assert not SavedStageEvidence.from_variant("date", observations[0]).active
    output = combine_saved(tmp_path / "never-opened.png", "receipt.png", json_copy(branch["snapshot"]))
    assert output["date_check"].get("actual") == "" and output["date_check"]["reliable"] is False
    assert output["date_check"]["status"] != "匹配"
    assert [latest, older] == before


def test_retaining_two_history_rows_keeps_each_provider_rule_context_and_stays_flat(tmp_path):
    first = json_copy(assemble_dates(tmp_path, [{"method": "vision", "result": date_payload()}]))
    second = json_copy(assemble_dates(tmp_path, [{"method": "paddle_v6", "result": date_payload("2026-11-08")}]))
    old_context = lambda version: {"schema_version": 1, "origin": "recorded", "evaluations": [
        {"operation": "recognition", "rules": {"date_comparison": version}}]}
    first["decision_provenance"] = old_context("older-vision-rule")
    second["decision_provenance"] = old_context("older-paddle-rule")
    history = deepcopy([first, second])
    plan = {stage: [] for stage in STAGES}
    branch = retained_branch([(first, {}), (second, {})], plan, "source-hash")
    sources = branch["snapshot"]["decision_provenance"]["retained_sources"]
    assert sources == {"date": {"vision": first["decision_provenance"], "paddle_v6": second["decision_provenance"]}}
    initial_sources = json_copy(sources)
    for _ in range(3):
        replayed = combine_saved(tmp_path / "never-opened.png", "receipt.png", json_copy(branch["snapshot"]))
        assert replayed["decision_provenance"]["retained_sources"] == initial_sources
        branch = retained_branch([(replayed, {})], plan, "source-hash")
        assert branch["snapshot"]["decision_provenance"]["retained_sources"] == initial_sources
    assert [first, second] == history


def test_retained_context_can_use_the_corresponding_snapshot_when_result_has_none(tmp_path):
    document = assemble_dates(tmp_path, [{"method": "vision", "result": date_payload()}])
    document.pop("decision_provenance", None)
    context = {"schema_version": 1, "origin": "recorded", "evaluations": [
        {"operation": "recognition", "rules": {"date_comparison": "snapshot-rule"}}]}
    saved = {"snapshot": {"source_hash": "source-hash", "decision_provenance": context}}
    branch = retained_branch([(document, saved)], {stage: [] for stage in STAGES}, "source-hash")
    assert branch["snapshot"]["decision_provenance"]["retained_sources"]["date"]["vision"] == context


@pytest.mark.parametrize("marker", [{"simulated": True}, {"error": "child request failed"}, {"pending": True}])
@pytest.mark.parametrize("location", ["request", "result"])
@pytest.mark.parametrize("with_valid_stamp", [False, True])
def test_saved_reassembly_rejects_inactive_seal_children_but_keeps_their_artifacts_and_trace(
        tmp_path, monkeypatch, marker, location, with_valid_stamp):
    from receipt_ocr import danzhengtong_seal

    requirement = "测试客户收货专用章"
    stale = {"index": 0, "status": "completed", "req_uuid": "old-stamp",
             "region": {"role": "收货客户章"}, "result": {"commitResult": {
                 "收货客户印章": {"value": requirement}, "印章颜色": {"value": "蓝色"}}}}
    (stale if location == "request" else stale["result"]).update(marker)
    requests = [stale]
    if with_valid_stamp:
        requests.append({"index": 1, "status": "completed", "req_uuid": "current-stamp",
                         "region": {"role": "收货客户章"}, "result": {"commitResult": {
                             "收货客户印章": {"value": "另一客户收货专用章"}, "印章颜色": {"value": "红色"}}}})
    trace = {"mode": "real", "simulated": False, "status": "completed",
             "model_id": "independent-test", "requests": deepcopy(requests)}
    remote = {"requests": requests, "trace": trace, "download_mode": "deferred"}
    config = {stage: [] for stage in STAGES}
    config.update(seal=["danzhengtong"], acceptance={"seal_match_mode": "any"})
    snapshot = empty_snapshot(config, {"签章要求": requirement})
    snapshot["context"] = {"document_type": {"type": "receipt", "reliable": True},
                           "has_footer": True, "footer_detection": {"present": True}}
    artifact_calls = []
    returned_artifact = danzhengtong_seal._returned_artifact

    def inspect_artifact(item, data, *args, **kwargs):
        artifact_calls.append((item["index"], kwargs["download_mode"]))
        return returned_artifact(item, data, *args, **kwargs)

    monkeypatch.setattr(danzhengtong_seal, "_returned_artifact", inspect_artifact)
    before = deepcopy(remote)
    result = combine_saved(tmp_path / "never-opened.png", "receipt.png", snapshot, remote_seal=remote)
    variant = result["recognition_variants"]["seal"][0]
    check = variant["details"]["seal_check"]
    assert artifact_calls == [(item["index"], "deferred") for item in requests]
    assert len(variant["details"]["processing_artifacts"]["seals"]) == len(requests)
    assert variant["details"]["danzhengtong_seal"] == trace
    assert check["status"] != "匹配"
    if with_valid_stamp:
        assert check["recognized"] == "另一客户收货专用章"
        assert check["provider_color"] == "红色"
        assert [candidate["index"] for candidate in check["candidates"]] == [1]
    else:
        assert check["recognized"] == "" and check["reliable"] is False
        assert check["candidates"] == [] and check["provider_metadata"] == {}
        if marker.get("error"):
            assert marker["error"] in check["message"]
    assert remote == before


@pytest.mark.parametrize("marker", [{"simulated": True}, {"error": "child failed"}, {"pending": True}])
@pytest.mark.parametrize("location", ["request", "result"])
@pytest.mark.parametrize("with_valid_stamp", [False, True])
def test_standard_comparison_and_registration_cannot_reactivate_an_invalid_child_artifact(
        tmp_path, marker, location, with_valid_stamp):
    from PIL import Image
    from receipt_ocr.danzhengtong_seal import returned_image_target

    requirement = "测试客户收货章"
    request = lambda index: {"index": index, "status": "completed", "req_uuid": f"stamp-{index}",
        "original_url": f"/files/test-original-{index}.png", "region": {"role": "收货客户章"},
        "result": {"imgUrls": [f"https://test.invalid/stamp-{index}.png"], "commitResult": {
            "收货客户印章": {"value": requirement}, "印章类型": {"value": "圆形"}, "印章颜色": {"value": "红色"}}}}
    invalid = request(0)
    (invalid if location == "request" else invalid["result"]).update(marker)
    requests = [invalid, request(1)] if with_valid_stamp else [invalid]
    directory, prefix = tmp_path / "artifacts", "/files/artifacts/test"
    for item in requests:
        _, destination, _ = returned_image_target(item, directory, prefix)
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "red").save(destination)  # Cached returns; no download occurs.
    trace = {"mode": "real", "simulated": False, "status": "completed", "requests": deepcopy(requests)}
    remote = {"requests": requests, "trace": trace, "artifact_dir": directory,
              "artifact_url_prefix": prefix, "download_mode": "deferred"}
    config = {stage: [] for stage in STAGES}
    config.update(date=["paddle_v6"], seal=["danzhengtong"], acceptance={"seal_match_mode": "any", "date_match_mode": "any"})
    snapshot = empty_snapshot(config, {"签章要求": requirement, "要求到货": "2026-11-09"})
    snapshot["results"]["date"] = [{"method": "paddle_v6", "result": date_payload()}]
    snapshot["context"] = {"document_type": {"type": "receipt", "reliable": True},
                           "has_footer": True, "footer_detection": {"present": True}}
    combined = combine_saved(tmp_path / "never-opened.png", "receipt.png", snapshot, remote_seal=remote)
    artifacts = combined["recognition_variants"]["seal"][0]["details"]["processing_artifacts"]["seals"]
    assert len(artifacts) == len(requests) and artifacts[0]["returned_image_status"] == "downloaded"
    assert artifacts[0]["combined_text"] == requirement
    assert artifacts[0]["execution_status"] == "skipped"
    assert combined["danzhengtong_seal"] == trace
    candidates = seal_candidates(combined)
    assert [candidate["index"] for candidate in candidates] == ([1] if with_valid_stamp else [])
    assert all(candidate["can_register"] for candidate in candidates)
    checked = apply_standard_check(combined, {"id": 1, "shape": "round", "color": "red", "revision": 1})
    assert checked["seal_standard_check"]["status"] == ("matched" if with_valid_stamp else "unmatched")
    if with_valid_stamp:
        assert checked["seal_check"]["status"] == "匹配" and checked["overall"] == "通过"
    else:
        assert checked["seal_check"]["status"] != "匹配" and checked["overall"] != "通过"
    assert checked["processing_artifacts"] == combined["processing_artifacts"]


@pytest.mark.parametrize("provider", ["danzhengtong", "qingtong"])
@pytest.mark.parametrize("state", ["pending", "failed", "mismatch"])
@pytest.mark.parametrize("same_url", [False, True])
@pytest.mark.parametrize("with_local_stamp", [False, True])
def test_retained_remote_images_cannot_outvote_the_current_provider_outcome(
        tmp_path, provider, state, same_url, with_local_stamp):
    requirement, wrong_text = "测试客户收货专用章", "另一客户收货专用章"
    old_artifact = {"index": 0, "shape_code": "round", "color": "red",
                    "original_url": "/files/old-original.png", "returned_image_url": "/files/old-returned.png",
                    "color_isolated_oriented_url": "/files/old-returned.png", "matching_texts": [requirement]}
    old_artifact.update({"provider": "danzhengtong"} if provider == "danzhengtong"
                        else {"source": "清瞳印章区域"})
    old_check = compare_machine_seals(requirement, [requirement])
    old_check.update(source="单证通" if provider == "danzhengtong" else "清瞳")
    old = assemble_dates(tmp_path, [{"method": "paddle_v6", "result": date_payload()}])
    old["fields"]["签章要求"] = requirement
    old["recognition_config"].update(seal=[provider], acceptance={"seal_match_mode": "any", "date_match_mode": "any"})
    old["seal_check"] = old_check
    old["processing_artifacts"]["seals"] = [old_artifact]
    old["recognition_variants"]["seal"] = [public_variant("seal", {"method": provider, "result": {
        "seal_check": old_check, "processing_artifacts": {"seals": [old_artifact]}}})]
    if with_local_stamp:
        local = {"index": 1, "shape_code": "round", "color": "red", "original_url": "/files/local.png",
                 "color_isolated_oriented_url": "/files/local-clean.png", "matching_texts": [requirement]}
        old["processing_artifacts"]["seals"].append(local)
        old["recognition_config"]["seal"].append("paddle_v6")
        old["recognition_variants"]["seal"].append(public_variant("seal", {"method": "paddle_v6", "result": {
            "seal_check": compare_machine_seals(requirement, [requirement], local=True),
            "processing_artifacts": {"seals": [local]}}}))
    history, remote_kwargs = [(deepcopy(old), {})], {}
    current_url = old_artifact["original_url"] if same_url else "/files/current-original.png"
    if provider == "danzhengtong":
        if state == "pending":
            remote_kwargs["pending"] = True
        elif state == "failed":
            remote_kwargs["remote_seal"] = {"preparation_error": "failed before submission"}
        else:
            request = {"index": 0, "status": "completed", "req_uuid": "current", "original_url": current_url,
                       "region": {"role": "收货客户章"}, "result": {"commitResult": {
                           "收货客户印章": {"value": wrong_text}, "印章类型": {"value": "圆形"},
                           "印章颜色": {"value": "红色"}}}}
            remote_kwargs["remote_seal"] = {"requests": [request], "download_mode": "deferred", "trace": {
                "mode": "real", "simulated": False, "status": "completed", "requests": [deepcopy(request)]}}
    else:
        current = deepcopy(old)
        if state == "mismatch":
            new_artifact = {**old_artifact, "original_url": current_url, "matching_texts": [wrong_text]}
            new_check = {**compare_machine_seals(requirement, [wrong_text]), "source": "清瞳"}
            current["recognition_variants"]["seal"][0] = public_variant("seal", {"method": provider, "result": {
                "seal_check": new_check, "processing_artifacts": {"seals": [new_artifact]}}})
            current["processing_artifacts"]["seals"][0] = new_artifact
        else:
            current["recognition_variants"]["seal"][0] = public_variant("seal", StageOutcome("seal", provider, state))
        history.insert(0, (current, {}))
    before = deepcopy(history)
    branch = retained_branch(history, old["recognition_config"], "same-source")
    combined = combine_saved(tmp_path / "never-opened.png", "receipt.png", branch["snapshot"],
                             preserve_provider_checks=True, **remote_kwargs)
    merged = json_copy(merge_retained_result(branch, combined))
    candidates = seal_candidates(merged)
    assert not any(candidate["source"] == provider and candidate["text_status"] == "匹配" for candidate in candidates)
    remote_candidates = [candidate for candidate in candidates if candidate["source"] == provider]
    assert len(remote_candidates) == (1 if state == "mismatch" else 0)
    if remote_candidates:
        assert remote_candidates[0]["recognized"] == wrong_text
    assert any(artifact.get("skip_reason") == "superseded_provider_evidence"
               for artifact in merged["processing_artifacts"]["seals"])
    checked = apply_standard_check(merged, {"id": 1, "shape": "round", "color": "red", "revision": 1})
    assert checked["seal_standard_check"]["status"] == ("matched" if with_local_stamp else "unmatched")
    if not with_local_stamp:
        assert checked["overall"] != "通过" and checked["seal_check"]["status"] != "匹配"
    assert history == before


@pytest.mark.parametrize("provider", ["danzhengtong", "qingtong"])
@pytest.mark.parametrize("state", ["pending", "failed", "skipped"])
def test_standard_candidates_respect_named_outcomes_and_accept_old_unversioned_artifacts(provider, state):
    requirement = "测试客户收货专用章"
    artifact = {"shape_code": "round", "color": "red", "original_url": "/original.png",
                "returned_image_url": "/returned.png", "color_isolated_oriented_url": "/clean.png",
                "matching_texts": [requirement]}
    artifact.update({"provider": "danzhengtong"} if provider == "danzhengtong"
                    else {"source": "清瞳印章区域"})
    result = {"fields": {"签章要求": requirement}, "processing_artifacts": {"seals": [artifact]}}
    assert seal_candidates(result)[0]["can_register"] is True  # Legacy evidence lacks a variant envelope.
    outcome = StageOutcome("seal", provider, state, payload={"seal_check": compare_machine_seals(requirement, [requirement])})
    for variant in (outcome, public_variant("seal", outcome)):
        result["recognition_variants"] = {"seal": [variant]}
        assert seal_candidates(result) == []
    result["recognition_variants"] = {"seal": [{"method": provider, "details": {}}]}
    assert seal_candidates(result) == []
    result["recognition_variants"]["seal"][0]["details"]["seal_check"] = compare_machine_seals(requirement, [requirement])
    assert seal_candidates(result)[0]["recognized"] == requirement


@pytest.mark.parametrize("marker", [{"status": "failed"}, {"status": "waiting"}, {"simulated": True}])
def test_legacy_stamp_artifacts_still_respect_an_explicit_inactive_trace(marker):
    result = {"fields": {"签章要求": "测试客户收货章"}, "danzhengtong_seal": marker,
              "processing_artifacts": {"seals": [{"provider": "danzhengtong", "shape_code": "round", "color": "red",
                  "original_url": "/original.png", "returned_image_url": "/returned.png",
                  "matching_texts": ["测试客户收货章"]}]}}
    assert seal_candidates(result) == []


def test_retained_merge_keeps_old_non_mapping_image_placeholders():
    branch = {"base": {"processing_artifacts": {"seals": [None, "legacy-placeholder"]}}, "snapshot": {}}
    merged = merge_retained_result(branch, {"processing_artifacts": {"seals": []}})
    assert merged["processing_artifacts"]["seals"] == [None, "legacy-placeholder"]
