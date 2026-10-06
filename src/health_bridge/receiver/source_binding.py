import hashlib
import hmac
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from health_bridge.contract import HealthBridgeBatchV1
from health_bridge.receiver.tokens import ReceiverTokenPrincipal

LEGACY_PHONE_SOURCE_KEY: Final = "apple_health.phone"
PHONE_SOURCE_PREFIX: Final = f"{LEGACY_PHONE_SOURCE_KEY}."
INSTALLATION_HASH_DOMAIN: Final = "health-bridge-pairing:installation:"
# The Apple Health export.zip lab-results importer (HealthRelay B3) is a manual,
# one-shot upload, not a per-device live HealthKit lane -- it deliberately uses a
# source key outside the apple_health.phone[.<installation>] family so rows stay
# traceable to a manual import (see AppleHealthExportLabImporter.sourceKey on the
# client). Bug found 2026-09-29 (Michael: every export send got 403'd forever,
# which then blocked every other queued item behind it): this file's allowlist
# never accounted for that key, so it always failed principal binding. It carries
# no device-installation claim to verify, so it is accepted unconditionally, the
# same way the legacy bare phone key is.
EXPORT_SOURCE_KEY: Final = "apple_health.export"


@dataclass(frozen=True)
class SourcePrincipalMismatchError(ValueError):
    source_key: str


def bind_batch_to_principal(
    batch: HealthBridgeBatchV1,
    principal: ReceiverTokenPrincipal,
) -> HealthBridgeBatchV1:
    installation_id_hash = principal.installation_id_hash
    claimed_source_keys = _claimed_source_keys(batch)
    if installation_id_hash is None:
        for source_key in claimed_source_keys:
            if source_key == EXPORT_SOURCE_KEY:
                continue
            if source_key == LEGACY_PHONE_SOURCE_KEY or source_key.startswith(
                PHONE_SOURCE_PREFIX
            ):
                raise SourcePrincipalMismatchError(source_key)
        return batch

    canonical_source_key = f"{PHONE_SOURCE_PREFIX}{installation_id_hash}"
    for source_key in claimed_source_keys:
        if source_key == EXPORT_SOURCE_KEY:
            continue
        if not _source_belongs_to_installation(
            source_key,
            installation_id_hash=installation_id_hash,
            canonical_source_key=canonical_source_key,
        ):
            raise SourcePrincipalMismatchError(source_key)

    canonical_source = batch.sources[0].model_copy(
        update={
            "source_key": (
                canonical_source_key
                if batch.sources[0].source_key != EXPORT_SOURCE_KEY
                else EXPORT_SOURCE_KEY
            )
        }
    )
    return batch.model_copy(
        update={
            "sources": (canonical_source,),
            "samples": tuple(
                sample.model_copy(update={"source_key": canonical_source_key})
                for sample in batch.samples
            ),
            "workouts": tuple(
                workout.model_copy(update={"source_key": canonical_source_key})
                for workout in batch.workouts
            ),
            "sleep_sessions": tuple(
                session.model_copy(update={"source_key": canonical_source_key})
                for session in batch.sleep_sessions
            ),
            "deleted_records": tuple(
                deleted.model_copy(
                    update={}
                    if deleted.source_key == EXPORT_SOURCE_KEY
                    and deleted.record_family == "lab_result"
                    else {"source_key": canonical_source_key}
                )
                for deleted in batch.deleted_records
            ),
            "lab_results": tuple(
                lab_result.model_copy(
                    update={}
                    if lab_result.source_key == EXPORT_SOURCE_KEY
                    else {"source_key": canonical_source_key}
                )
                for lab_result in batch.lab_results
            ),
            # ECG and medication dose events are HealthKit live-lane families like
            # samples/workouts/sleep_sessions above, so they are rewritten to the
            # canonical key unconditionally -- the export importer only ever produces
            # lab results, and lab_results is the only family with an EXPORT_SOURCE_KEY
            # exemption. Leaving these unrewritten made a legacy-key batch wedge:
            # sources was canonical, these rows were not, so source_id() raised
            # IntegrityError, /v1/batches 500'd and the outbox retried forever.
            "electrocardiograms": tuple(
                ecg.model_copy(update={"source_key": canonical_source_key})
                for ecg in batch.electrocardiograms
            ),
            "medication_dose_events": tuple(
                event.model_copy(update={"source_key": canonical_source_key})
                for event in batch.medication_dose_events
            ),
            "sync": batch.sync.model_copy(
                update={
                    "cursors": tuple(
                        cursor.model_copy(update={"source_key": canonical_source_key})
                        for cursor in batch.sync.cursors
                    )
                }
            ),
        }
    )


def _claimed_source_keys(batch: HealthBridgeBatchV1) -> set[str]:
    return {
        *(source.source_key for source in batch.sources),
        *(sample.source_key for sample in batch.samples),
        *(workout.source_key for workout in batch.workouts),
        *(session.source_key for session in batch.sleep_sessions),
        *(deleted.source_key for deleted in batch.deleted_records),
        *(lab_result.source_key for lab_result in batch.lab_results),
        *(ecg.source_key for ecg in batch.electrocardiograms),
        *(event.source_key for event in batch.medication_dose_events),
        *(cursor.source_key for cursor in batch.sync.cursors),
    }


def _source_belongs_to_installation(
    source_key: str,
    *,
    installation_id_hash: str,
    canonical_source_key: str,
) -> bool:
    if source_key == LEGACY_PHONE_SOURCE_KEY:
        return True
    if hmac.compare_digest(source_key, canonical_source_key):
        return True
    if not source_key.startswith(PHONE_SOURCE_PREFIX):
        return False
    installation_id = source_key.removeprefix(PHONE_SOURCE_PREFIX)
    try:
        normalized_installation_id = str(UUID(installation_id))
    except ValueError:
        return False
    claimed_hash = hashlib.sha256(
        f"{INSTALLATION_HASH_DOMAIN}{normalized_installation_id}".encode()
    ).hexdigest()
    return hmac.compare_digest(claimed_hash, installation_id_hash)
