"""Project completed input tasks onto the existing auditable JSON format."""

from .contracts import PreparedSeal, SealInputTask


class SealEvidence:
    """Mutable display evidence, addressed by stable input IDs only."""

    def __init__(self, prepared: PreparedSeal):
        self.artifact = prepared.artifact
        self._inputs = {entry["id"]: entry for entry in self.artifact["inputs"]}

    def complete(self, task: SealInputTask, observations, provider, *, selected_image_url=None):
        entry = self._inputs[task.input_id]
        entry["status"] = "completed" if observations else "empty"
        if selected_image_url is not None:
            entry["selected_read_image_url"] = selected_image_url
        for position, row in enumerate(observations):
            if not row.text:
                continue
            read = {"id": f"{task.input_id}:{position}", "input_id": task.input_id,
                    "region_id": task.region_id, "channel": task.channel,
                    "provider": provider, "text": row.text, "confidence": row.confidence,
                    "image_url": selected_image_url or task.image_url}
            if self.artifact.get("rectangular_layout") in {"single_detection", "frame_rows", "frame_columns"}:
                read["rectangle_row_index"] = (
                    position if task.observations is not None else task.rectangle_row_index
                )
            self.artifact["reads"].append(read)
        channel_texts = [item["text"] for item in self.artifact["reads"]
                         if item.get("channel") == task.channel]
        keys = {"ring": ("unwrap_texts", "unwrapped_text"),
                "center": ("round_type_band_texts", "round_type_band_text"),
                "body": ("color_isolated_texts", "color_isolated_text")}
        # Preserve the legacy envelope for an empty read (no new channel keys).
        if any(row.text for row in observations) and task.channel in keys:
            texts_key, text_key = keys[task.channel]
            self.artifact[texts_key] = channel_texts
            self.artifact[text_key] = " | ".join(channel_texts)

    def fail(self, task: SealInputTask, error: Exception):
        self._inputs[task.input_id].update(status="failed", error=str(error))
        self.artifact["errors"].append({"step": task.channel, "message": str(error)})
