import hashlib
import json
import os
import pathlib
import unicodedata

import pytest


FIXTURE = (
    pathlib.Path(__file__).resolve().parent
    / "fixtures"
    / "private_qc"
    / "stripe_heic_fingerprints.json"
)
DEFAULT_PRIVATE_QC_ROOT = pathlib.Path(
    "/Users/nojeong-un/Downloads/노션에 있는 의상들"
)
EXPECTED_SLOTS = ("Front", "Back", "Detail")
EXPECTED_BUNDLE_SHA256 = (
    "67e09ca5c1ae6a195c73416d41e77b5aca4f30dae6f19d1d1a618d0cd6768a4c"
)


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _path_from_env() -> pathlib.Path:
    return pathlib.Path(os.environ.get("WEARLESS_PRIVATE_QC_ROOT") or DEFAULT_PRIVATE_QC_ROOT)


def _resolve_nfc_path(path: pathlib.Path) -> pathlib.Path | None:
    if path.exists():
        return path
    if path.is_absolute():
        current = pathlib.Path(path.anchor)
        parts = path.parts[1:]
    else:
        current = pathlib.Path(".")
        parts = path.parts
    for part in parts:
        if not current.exists():
            return None
        matches = [child for child in current.iterdir() if _nfc(child.name) == _nfc(part)]
        if len(matches) != 1:
            return None
        current = matches[0]
    return current if current.exists() else None


def _resolve_logical_filename(root: pathlib.Path, logical_filename: str) -> pathlib.Path:
    current = root
    for part in pathlib.PurePosixPath(_nfc(logical_filename)).parts:
        matches = [child for child in current.iterdir() if _nfc(child.name) == _nfc(part)]
        assert len(matches) == 1, f"{logical_filename!r} resolved to {len(matches)} files"
        current = matches[0]
    return current


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bundle_sha256(items: list[dict[str, str]]) -> str:
    """Match frame_shadow_collect._source_bundle_sha256 for multi-image inputs."""
    canonical = json.dumps(
        items,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def test_private_qc_fingerprint_fixture_tracks_only_inputs():
    fixture = _load_fixture()

    assert fixture["bundleId"] == "stripe_heic_private_qc"
    assert fixture["bundleHashAlgorithm"] == "designated-private-qc-bundle-v1"
    assert fixture["expectedBundleSha256"] == EXPECTED_BUNDLE_SHA256
    assert fixture["imageSize"] == "1K"
    assert fixture["imageSizeCap"] == "1K"
    assert [entry["slot"] for entry in fixture["slots"]] == list(EXPECTED_SLOTS)
    assert len(fixture["slots"]) == 3
    assert {"provider", "model", "prompt", "apiKey"}.isdisjoint(fixture)


def test_private_qc_designated_files_match_tracked_fingerprints():
    fixture = _load_fixture()
    root = _resolve_nfc_path(_path_from_env())
    if root is None:
        pytest.skip("private QC root is not present in this environment")

    observed = []
    for entry in fixture["slots"]:
        path = _resolve_logical_filename(root, entry["logicalFilename"])
        assert path.is_file()
        observed_sha256 = _sha256(path)
        assert observed_sha256 == entry["originalSha256"]
        observed.append(
            {
                "slot": entry["slot"],
                "logicalFilename": _nfc(entry["logicalFilename"]),
                "originalSha256": observed_sha256,
            }
        )

    assert [entry["slot"] for entry in observed] == list(EXPECTED_SLOTS)
    assert len({entry["logicalFilename"] for entry in observed}) == 3
    assert len({entry["originalSha256"] for entry in observed}) == 3
    observed_bundle_sha256 = _bundle_sha256(
        [
            {"slot": entry["slot"], "originalSha256": entry["originalSha256"]}
            for entry in observed
        ]
    )
    assert observed_bundle_sha256 == fixture["expectedBundleSha256"]
    assert observed_bundle_sha256 == EXPECTED_BUNDLE_SHA256
