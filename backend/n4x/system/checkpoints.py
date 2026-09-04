"""Application checkpoints owned by the System."""

from __future__ import annotations

import json
import uuid
from base64 import b64decode, b64encode
from typing import Any

from n4x.graph.store import node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.hash import sha256_text
from n4x.kernel.models import (
    ApplicationObject,
    ApplicationRelation,
    CheckpointBlob,
    CheckpointSnapshot,
    GraphCheckpoint,
)


_DEFINITION_SETS = (
    ("actions", "Action", "ActionRevision"),
    ("triggers", "Trigger", "TriggerRevision"),
    ("object_types", "ObjectType", "ObjectTypeRevision"),
    ("relation_types", "RelationType", "RelationTypeRevision"),
)


class CheckpointService:
    """Creates and restores immutable, graph-owned checkpoint snapshots."""

    def __init__(self, uow: GraphUnitOfWork) -> None:
        self.uow = uow
        self.store = uow.store
        self.records = uow.records

    def create(
        self,
        application_id: str,
        *,
        level: str = "revision",
        reason: str,
    ) -> GraphCheckpoint:
        if level not in {"revision", "application_data"}:
            raise ValueError(f"unsupported checkpoint level: {level}")
        with self.uow:
            application = self.records.applications[application_id]
            checkpoint_id = str(uuid.uuid4())
            snapshot_id = f"{checkpoint_id}.snapshot"
            blob_id = f"{checkpoint_id}.blob"
            payload = self._capture_payload(
                application_id, include_data=level == "application_data"
            )
            serialized = json.dumps(
                payload, sort_keys=True, separators=(",", ":")
            )
            checkpoint = GraphCheckpoint(
                id=checkpoint_id,
                level=level,  # type: ignore[arg-type]
                application_id=application_id,
                application_revision_id=application.active_revision_id,
                snapshot_id=snapshot_id,
                reason=reason,
            )
            snapshot = CheckpointSnapshot(
                id=snapshot_id,
                checkpoint_id=checkpoint_id,
                level=level,  # type: ignore[arg-type]
                content_hash=sha256_text(serialized),
                blob_id=blob_id,
                object_count=len(payload.get("objects", [])),
                relation_count=len(payload.get("relations", [])),
            )
            blob = CheckpointBlob(
                id=blob_id,
                checkpoint_id=checkpoint_id,
                snapshot_id=snapshot_id,
                payload=b64encode(serialized.encode("utf-8")).decode(
                    "ascii"
                ),
            )
            self.records.checkpoints.save(checkpoint)
            self.records.checkpoint_snapshots.save(snapshot)
            self.records.checkpoint_blobs.save(blob)
            checkpoint_ref = node_ref("GraphCheckpoint", id=checkpoint.id)
            self.store.create_edge(
                node_ref("Application", id=application_id),
                "HAS_CHECKPOINT",
                checkpoint_ref,
            )
            if checkpoint.application_revision_id is not None:
                self.store.create_edge(
                    checkpoint_ref,
                    "CAPTURES_REVISION",
                    node_ref(
                        "ApplicationRevision",
                        id=checkpoint.application_revision_id,
                    ),
                )
            self.store.create_edge(
                checkpoint_ref,
                "HAS_SNAPSHOT",
                node_ref("CheckpointSnapshot", id=snapshot.id),
            )
            self.store.create_edge(
                node_ref("CheckpointSnapshot", id=snapshot.id),
                "HAS_BLOB",
                node_ref("CheckpointBlob", id=blob.id),
            )
            return checkpoint

    def restore(self, checkpoint_id: str) -> GraphCheckpoint:
        with self.uow:
            checkpoint = self.records.checkpoints[checkpoint_id]
            snapshot = self.records.checkpoint_snapshots[
                checkpoint.snapshot_id
            ]
            blob = self.records.checkpoint_blobs[snapshot.blob_id]
            serialized = b64decode(blob.payload).decode("utf-8")
            if sha256_text(serialized) != snapshot.content_hash:
                raise ValueError(
                    f"checkpoint snapshot hash mismatch: {checkpoint_id}"
                )
            payload = json.loads(serialized)
            self._restore_revision_state(checkpoint.application_id, payload)
            if checkpoint.level == "application_data":
                self._restore_application_data(
                    checkpoint.application_id, payload
                )
            return checkpoint

    def list(self, application_id: str | None = None) -> list[GraphCheckpoint]:
        checkpoints = self.records.checkpoints.values()
        if application_id is not None:
            checkpoints = [
                item
                for item in checkpoints
                if item.application_id == application_id
            ]
        return sorted(checkpoints, key=lambda item: item.created_at)

    def _capture_payload(
        self, application_id: str, *, include_data: bool
    ) -> dict[str, Any]:
        application = self.records.applications[application_id]
        definitions = []
        for collection_name, label, revision_label in _DEFINITION_SETS:
            collection = getattr(self.records, collection_name)
            definitions.extend(
                {
                    "label": label,
                    "revision_label": revision_label,
                    "id": stable.id,
                    "active_revision_id": stable.active_revision_id,
                }
                for stable in collection.values()
                if stable.application_id == application_id
            )
        payload: dict[str, Any] = {
            "format_version": 1,
            "application_id": application_id,
            "active_revision_id": application.active_revision_id,
            "revision_statuses": {
                revision.id: revision.status
                for revision in self.records.revisions.values()
                if revision.application_id == application_id
            },
            "definitions": sorted(
                definitions, key=lambda item: (item["label"], item["id"])
            ),
        }
        if include_data:
            payload["objects"] = [
                item.model_dump(mode="json")
                for item in self.uow.objects.list(application_id)
            ]
            payload["relations"] = [
                item.model_dump(mode="json")
                for item in self.uow.relations.list(application_id)
            ]
        return payload

    def _restore_revision_state(
        self, application_id: str, payload: dict[str, Any]
    ) -> None:
        if payload.get("application_id") != application_id:
            raise ValueError("checkpoint application identity mismatch")
        application = self.records.applications[application_id]
        active_revision_id = payload.get("active_revision_id")
        self.records.applications.save(
            application.model_copy(
                update={"active_revision_id": active_revision_id}
            )
        )
        application_ref = node_ref("Application", id=application_id)
        self.store.delete_edge(application_ref, "ACTIVE_REVISION")
        if active_revision_id is not None:
            self.store.create_edge(
                application_ref,
                "ACTIVE_REVISION",
                node_ref("ApplicationRevision", id=active_revision_id),
            )
        for revision_id, status in payload["revision_statuses"].items():
            revision = self.records.revisions.get(revision_id)
            if revision is not None:
                self.records.revisions.save(
                    revision.model_copy(update={"status": status})
                )
        collections = {
            label: (getattr(self.records, collection), revision_label)
            for collection, label, revision_label in _DEFINITION_SETS
        }
        for item in payload["definitions"]:
            collection, revision_label = collections[item["label"]]
            stable = collection[item["id"]]
            active_id = item["active_revision_id"]
            collection.save(
                stable.model_copy(update={"active_revision_id": active_id})
            )
            stable_ref = node_ref(item["label"], id=stable.id)
            self.store.delete_edge(stable_ref, "ACTIVE_REVISION")
            if active_id is not None:
                self.store.create_edge(
                    stable_ref,
                    "ACTIVE_REVISION",
                    node_ref(revision_label, id=active_id),
                )

    def _restore_application_data(
        self, application_id: str, payload: dict[str, Any]
    ) -> None:
        for relation in self.uow.relations.list(application_id):
            self.uow.relations.delete(relation)
        for application_object in self.uow.objects.list(application_id):
            self.uow.objects.delete(
                application_object.application_id,
                application_object.id,
                application_object.data_space_id,
            )
        for values in payload.get("objects", []):
            application_object = ApplicationObject.model_validate(values)
            self.uow.objects.save(application_object)
            self.uow.objects.attach(application_object)
        for values in payload.get("relations", []):
            self.uow.relations.save(
                ApplicationRelation.model_validate(values)
            )
