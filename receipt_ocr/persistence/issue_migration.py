"""One-way conversion of historical prose reasons to structured evidence.

Only persisted-data migration and saved-result repair tools use this module;
live recognition emits issue codes directly at the source.
"""
import json
import re
from ..domain.issues import issue

OPTIONAL_STAGE_REASONS = {
    "部分识别：未执行项目不能据此判定整单通过",
    "本次为部分识别，未执行项目不参与整单通过判定",
    "签收填写内容需人工确认",
}


def migrate_issues(result):
    issues = []
    for message in result.get("review_reasons", []):
        scope = "date" if re.search("日期|到货", message) else "seal" if re.search("印章|签章", message) else "document"
        code = "low_confidence" if "低置信度" in message else "review_required"
        if "首页未包含签收页脚" in message or "等待商品续页关联" in message:
            code, scope = "awaiting_continuation", "document"
        issues.append(issue(code, scope, message, blocking=message not in OPTIONAL_STAGE_REASONS))
    messages = [result.get("error_message", ""), *result.get("review_reasons", [])]
    for message in messages:
        if re.search(r"单证通|danzhengtong", str(message), re.I) and re.search(
            r"失败|超时|timeout|connectionerror|readtimeout|max retries|nodename|无法连接", str(message), re.I
        ):
            issues.append(issue("provider_failure", "document", str(message), provider="danzhengtong"))
    return issues


def migrate_saved_issues(connection):
    rows = connection.execute("""SELECT id,result_json,error_message FROM results
        WHERE json_valid(result_json) AND json_type(result_json,'$.review_issues') IS NULL""").fetchall()
    for row in rows:
        result = json.loads(row["result_json"])
        result["review_issues"] = migrate_issues({**result, "error_message": row["error_message"]})
        connection.execute("UPDATE results SET result_json=? WHERE id=?",
                           (json.dumps(result, ensure_ascii=False), row["id"]))
