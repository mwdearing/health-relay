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
    # An export key is only meaningful for a family the export importer actually
    # produces, and that is `lab_results` alone -- `LabResult`'s docstring records
    # that ECG and medications sync live and never need a manual export. For those
    # two, an export key is rejected here for EVERY token, before the unbound branch
    # returns and before the bound branch rewrites, because `EXPORT_SOURCE_KEY` is one
    # shared constant rather than an installation-scoped key: accepting it would let
    # any caller write into a partition no installation owns, which is the
    # cross-installation path this function exists to close. Rewriting it instead
    # would silently relabel an export claim as a device's, so it is refused rather
    # than converted.
    _reject_export_key_on_live_families(batch)
    if installation_id_hash is None:
        # A token bound to no device may carry a lab-result export import and nothing
        # else under the shared export source. A bound token's sample, workout and
        # sleep rows are rewritten to its installation's key, but an unbound token has
        # no installation, so those rows would land in the one partition nobody owns.
        _reject_export_key_on_non_lab_families(batch)
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
            # ECG and medication dose events are live-lane families, rewritten to the
            # canonical key **unconditionally** -- no export carve-out, unlike
            # lab_results and deleted_records.
            #
            # `EXPORT_SOURCE_KEY` is one shared constant, not an installation-scoped
            # key, and the ownership check above skips it deliberately (line 48) so a
            # manual import is not rejected. Preserving it here would therefore let a
            # paired device attach these rows to a source no installation owns -- the
            # ownership check never sees their key -- which is the cross-installation
            # injection this function exists to stop, reached through a different
            # door. Relying on the later IntegrityError to catch it would be relying
            # on an accident: whether it fires depends on catalog state.
            #
            # These families are live-only by contract, so the carve-out protects no
            # legitimate caller: `LabResult`'s docstring records that "ECG and
            # medications sync live via HealthKit and never need a manual export", and
            # the export importer emits no rows for either. `deleted_records` already
            # canonicalises an export-keyed tombstone unless its family is
            # `lab_result`, which is the same rule applied to the same families.
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


def _reject_export_key_on_live_families(batch: HealthBridgeBatchV1) -> None:
    """Refuse an export source key on a family the export importer never produces.

    `EXPORT_SOURCE_KEY` is exempt from the ownership check so a manual import is not
    rejected, and that exemption is only sound for the families a manual import can
    actually carry. `LabResult`'s docstring is explicit: "ECG and medications sync
    live via HealthKit and never need a manual export", and the export importer emits
    rows for neither. So an export-keyed ECG or medication row is not a manual import
    — it is a row reaching storage with its key never compared to the caller, which
    is a cross-installation write into the one source every installation shares.

    Checked for every token rather than only the bound one, because the unbound branch
    returns the batch unchanged and would otherwise carry such a row through
    untouched.
    """
    if any(ecg.source_key == EXPORT_SOURCE_KEY for ecg in batch.electrocardiograms):
        raise SourcePrincipalMismatchError(EXPORT_SOURCE_KEY)
    if any(
        event.source_key == EXPORT_SOURCE_KEY for event in batch.medication_dose_events
    ):
        raise SourcePrincipalMismatchError(EXPORT_SOURCE_KEY)
    # A tombstone names the same shared export partition as a row: the sync-state
    # upsert deletes the matching active record under the tombstone's own source, so an
    # export-keyed ECG or medication tombstone would delete rows left in that shared
    # source. Lab results are the one family the export importer produces, so only
    # their tombstones keep the exemption.
    if any(
        deleted.source_key == EXPORT_SOURCE_KEY
        and deleted.record_family in {"electrocardiogram", "medication_dose_event"}
        for deleted in batch.deleted_records
    ):
        raise SourcePrincipalMismatchError(EXPORT_SOURCE_KEY)


def _reject_export_key_on_non_lab_families(batch: HealthBridgeBatchV1) -> None:
    """Refuse an export source key on every family except lab results."""
    if (
        any(sample.source_key == EXPORT_SOURCE_KEY for sample in batch.samples)
        or any(workout.source_key == EXPORT_SOURCE_KEY for workout in batch.workouts)
        or any(
            session.source_key == EXPORT_SOURCE_KEY for session in batch.sleep_sessions
        )
        or any(
            deleted.source_key == EXPORT_SOURCE_KEY
            and deleted.record_family != "lab_result"
            for deleted in batch.deleted_records
        )
    ):
        raise SourcePrincipalMismatchError(EXPORT_SOURCE_KEY)


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
