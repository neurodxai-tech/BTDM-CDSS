"""
DICOM header de-identification, applied at upload time BEFORE anything is
stored. Pixel data is untouched, so inference is unaffected.

Follows the spirit of the DICOM PS3.15 Basic Application Level
Confidentiality Profile: direct identifiers are blanked, dates removed,
UIDs replaced, private tags dropped. It does NOT inspect pixel data for
burned-in text (e.g. names printed on secondary-capture images) — the
site's own de-identification process must still handle that before data
reaches this system.
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger("btdm.services.deid")

# Blanked (value set to empty) if present.
_BLANK_KEYWORDS = [
    "PatientName", "PatientID", "IssuerOfPatientID", "OtherPatientIDs",
    "OtherPatientNames", "OtherPatientIDsSequence", "PatientBirthDate",
    "PatientBirthTime", "PatientAddress", "PatientTelephoneNumbers",
    "PatientMotherBirthName", "MedicalRecordLocator", "EthnicGroup",
    "PatientComments", "AccessionNumber", "StudyID",
    "ReferringPhysicianName", "ReferringPhysicianAddress",
    "ReferringPhysicianTelephoneNumbers", "PerformingPhysicianName",
    "NameOfPhysiciansReadingStudy", "OperatorsName", "PhysiciansOfRecord",
    "RequestingPhysician", "InstitutionName", "InstitutionAddress",
    "InstitutionalDepartmentName", "StationName", "DeviceSerialNumber",
    "StudyDate", "SeriesDate", "AcquisitionDate", "ContentDate",
    "StudyTime", "SeriesTime", "AcquisitionTime", "ContentTime",
    "AcquisitionDateTime", "InstanceCreationDate", "InstanceCreationTime",
    "RequestAttributesSequence", "AdmissionID", "CurrentPatientLocation",
]
# Replaced with freshly generated UIDs (consistently within one upload, so
# multi-file studies still group correctly).
_UID_KEYWORDS = [
    "StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID",
    "FrameOfReferenceUID", "MediaStorageSOPInstanceUID",
]


class UidMapper:
    """Maps each original UID to one new UID for the lifetime of an upload."""

    def __init__(self) -> None:
        from pydicom.uid import generate_uid

        self._generate = generate_uid
        self._map: dict[str, str] = {}

    def __call__(self, uid: str) -> str:
        if uid not in self._map:
            self._map[uid] = self._generate()
        return self._map[uid]


def deidentify_dicom(file_bytes: bytes, uid_mapper: UidMapper) -> bytes:
    import pydicom

    ds = pydicom.dcmread(io.BytesIO(file_bytes), force=True)
    ds.remove_private_tags()

    for kw in _BLANK_KEYWORDS:
        if kw in ds:
            elem = ds.data_element(kw)
            if elem.VR == "SQ":
                del ds[elem.tag]
            else:
                elem.value = ""

    for kw in _UID_KEYWORDS:
        if kw in ds and ds.data_element(kw).value:
            setattr(ds, kw, uid_mapper(str(ds.data_element(kw).value)))
    fm = getattr(ds, "file_meta", None)
    if fm is not None and "MediaStorageSOPInstanceUID" in fm and "SOPInstanceUID" in ds:
        fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

    ds.PatientIdentityRemoved = "YES"
    ds.DeidentificationMethod = "BTDM-CDSS upload: PS3.15 basic profile subset (headers only)"

    out = io.BytesIO()
    ds.save_as(out, write_like_original=True)
    return out.getvalue()
