"""Export real QA-project inputs for the runtime cut-generation A/B harness.

This is deliberately a *read-only* bridge from the production-shaped DB/R2
registries to ``server/ab_out``.  It never scans an R2 prefix: every object must
be reachable from an owner-scoped ``assets`` row and from the product/mannequin
records that the service itself uses.

Safety boundaries:

* fixed account: ``qa-smoke@wearless.kr`` only;
* projects with a FaceMarket licence are excluded;
* only registered virtual model ids mA/mB/mC/mD are accepted;
* product truth images must be ``source='upload'`` and belong to that project;
* the selected mannequin must be ``source='ai'`` and belong to that project;
* DB transaction is explicitly read-only and R2 is GET-only;
* raw UUIDs, R2 keys, filenames and secrets are never printed or written to the
  local manifest.  A one-way project tag is used for local separation.

Usage (from ``server/``)::

    .venv/bin/python -m scripts.export_runtime_ab_project_inputs --inspect-only
    .venv/bin/python -m scripts.export_runtime_ab_project_inputs

The exporter keeps at most one recent, structurally useful candidate for each
of top/bottom/outer/dress and writes only beneath
``server/ab_out/runtime_cut_pipeline_20260804/project_inputs``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import httpx
import psycopg
from PIL import Image, UnidentifiedImageError
from psycopg.rows import dict_row

from scripts._env import load_env

load_env()

from app.agents import mannequin  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402


QA_EMAIL = "qa-smoke@wearless.kr"
VIRTUAL_MODEL_IDS = frozenset({"mA", "mB", "mC", "mD"})
CLOTHING_TYPES = ("top", "bottom", "outer", "dress")
OUT_ROOT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "ab_out/runtime_cut_pipeline_20260804/project_inputs"
)
MAX_ASSET_BYTES = 30 * 1024 * 1024
REST_PROJECT_LIMIT = 100
REST_BATCH_SIZE = 40

_TYPE_ALIASES = {
    "top": "top",
    "상의": "top",
    "bottom": "bottom",
    "하의": "bottom",
    "outer": "outer",
    "아우터": "outer",
    "dress": "dress",
    "원피스": "dress",
}

_DIVERSITY_TOKENS = {
    "top": (
        "stripe", "check", "logo", "print", "knit", "rib", "셔츠", "니트",
        "스트라이프", "체크", "로고", "프린트", "골지",
    ),
    "bottom": (
        "denim", "cargo", "pleat", "wash", "waist", "button", "zipper",
        "데님", "카고", "플리츠", "워싱", "허리", "단추", "지퍼",
    ),
    "outer": (
        "zipper", "button", "pocket", "lapel", "quilt", "hood", "jacket",
        "지퍼", "단추", "포켓", "라펠", "퀼팅", "후드", "재킷",
    ),
    "dress": (
        "print", "pleat", "tier", "waist", "drape", "pattern",
        "프린트", "플리츠", "티어드", "허리", "드레이프", "패턴",
    ),
}

_PROJECT_SQL = """
select pr.id::text as project_id,
       pr.updated_at,
       pr.selected_mannequin_id,
       p.name as product_name,
       p.clothing_type,
       p.colors,
       coalesce(an.payload, '{}'::jsonb) as analysis,
       ma.r2_bucket as mannequin_bucket,
       ma.r2_key as mannequin_key,
       ma.mime_type as mannequin_mime,
       ma.byte_size as mannequin_byte_size
from projects pr
join products p on p.project_id = pr.id
join analyses an on an.project_id = pr.id
join mannequin_cuts mc
  on mc.project_id = pr.id
 and (mc.candidate || '-' || mc.version::text) = pr.selected_mannequin_id
join assets ma
  on ma.id = mc.asset_id
 and ma.user_id = pr.user_id
 and ma.project_id = pr.id
 and ma.source = 'ai'
 and ma.deleted_at is null
where pr.user_id = %s
  and pr.deleted_at is null
  and pr.facemarket_license_id is null
  and pr.selected_mannequin_id is not null
  and coalesce(an.payload ->> 'selectedModelId', an.payload ->> 'selected_model_id')
      = any(%s)
order by pr.updated_at desc
"""

_UPLOAD_ASSETS_SQL = """
select id::text as id, r2_bucket, r2_key, mime_type, byte_size
from assets
where user_id = %s
  and project_id = %s
  and source = 'upload'
  and deleted_at is null
  and mime_type like 'image/%'
  and id = any(%s::uuid[])
"""


class ExportError(RuntimeError):
    """Expected safety/data failure that is safe to summarize without identifiers."""


def normalize_clothing_type(value: Any) -> str | None:
    return _TYPE_ALIASES.get(str(value or "").strip().lower())


def selected_virtual_model(analysis: dict | None) -> str | None:
    analysis = analysis if isinstance(analysis, dict) else {}
    value = analysis.get("selectedModelId") or analysis.get("selected_model_id")
    return value if isinstance(value, str) and value in VIRTUAL_MODEL_IDS else None


def product_image_pairs(colors: Any) -> list[tuple[str, str]]:
    """Return the service-authoritative base-colour inputs in slot order."""
    return mannequin.base_color_images({"colors": colors if isinstance(colors, list) else []})


def _text_blob(row: dict) -> str:
    analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
    values: list[str] = [str(row.get("product_name") or "")]
    for key in (
        "suggestedName", "customCategory", "subCategory", "sellingPoints",
        "aiSuggestedPoints", "styleTags",
    ):
        value = analysis.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value if isinstance(item, str))
    return " ".join(values).lower()


def diversity_signals(row: dict, clothing_type: str) -> list[str]:
    blob = _text_blob(row)
    return sorted({token for token in _DIVERSITY_TOKENS[clothing_type] if token in blob})


def selection_key(row: dict) -> tuple[int, int, datetime]:
    """Prefer structural signal coverage, then richer truth inputs, then recency."""
    clothing_type = row["normalized_clothing_type"]
    updated_at = row.get("updated_at")
    if not isinstance(updated_at, datetime):
        updated_at = datetime.min.replace(tzinfo=timezone.utc)
    return (
        len(row.get("diversity_signals") or diversity_signals(row, clothing_type)),
        len(row.get("product_assets") or []),
        updated_at,
    )


def anonymized_project_tag(project_id: str) -> str:
    return "qa-" + hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:12]


def parse_selected_mannequin(value: Any) -> tuple[str, int] | None:
    """Parse the persisted client id without allowing it into a REST filter unchecked."""
    match = re.fullmatch(r"([AB])-(\d+)", str(value or ""))
    if not match:
        return None
    version = int(match.group(2))
    return (match.group(1), version) if version >= 1 else None


def postgrest_uuid_in(values: list[str]) -> str:
    """Build a PostgREST ``in`` value only after strict UUID validation."""
    clean: list[str] = []
    for value in values:
        try:
            clean.append(str(uuid.UUID(str(value))))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ExportError("a Supabase registry response contained an invalid identifier") from exc
    if not clean:
        raise ExportError("an empty identifier batch cannot be queried")
    return "in.(" + ",".join(clean) + ")"


def _manifest_features(analysis: dict) -> dict:
    """Small non-identity metadata subset useful for experiment stratification."""
    result: dict[str, Any] = {}
    for key in ("suggestedName", "sellingPoints", "aiSuggestedPoints", "styleTags"):
        value = analysis.get(key)
        if isinstance(value, str):
            result[key] = value
        elif isinstance(value, list):
            result[key] = [item for item in value if isinstance(item, str)][:8]
    return result


def _load_candidates_db(settings) -> tuple[list[dict], Counter]:
    """Preferred direct-Postgres path.  Connection failure is handled by the caller."""
    stats: Counter = Counter()
    qualified: list[dict] = []
    with psycopg.connect(
        settings.database_url, row_factory=dict_row, connect_timeout=5
    ) as conn:
        with conn.transaction():
            conn.execute("set transaction read only")
            with conn.cursor() as cur:
                cur.execute("select id::text as id from auth.users where email = %s", (QA_EMAIL,))
                user = cur.fetchone()
                if not user:
                    raise ExportError("the fixed QA account does not exist")

                user_id = user["id"]
                cur.execute(_PROJECT_SQL, (user_id, list(VIRTUAL_MODEL_IDS)))
                rows = cur.fetchall()
                stats["scanned"] = len(rows)

                for row in rows:
                    clothing_type = normalize_clothing_type(row.get("clothing_type"))
                    if clothing_type not in CLOTHING_TYPES:
                        stats["unsupported_clothing_type"] += 1
                        continue
                    if not selected_virtual_model(row.get("analysis")):
                        stats["non_virtual_model"] += 1
                        continue

                    pairs = product_image_pairs(row.get("colors"))
                    if not pairs or not any(slot == "Front" for slot, _ in pairs):
                        stats[f"{clothing_type}_missing_front"] += 1
                        continue
                    ids = [asset_id for _slot, asset_id in pairs]
                    cur.execute(_UPLOAD_ASSETS_SQL, (user_id, row["project_id"], ids))
                    by_id = {asset["id"]: asset for asset in cur.fetchall()}
                    # Partial fallbacks would silently stop testing against the same truth set
                    # used by the service.  Treat any missing/non-upload ref as ineligible.
                    if len(by_id) != len(ids):
                        stats[f"{clothing_type}_unregistered_product_asset"] += 1
                        continue

                    product_assets = []
                    for slot, asset_id in pairs:
                        asset = by_id[asset_id]
                        product_assets.append({"slot": slot, **asset})

                    item = dict(row)
                    item["normalized_clothing_type"] = clothing_type
                    item["product_assets"] = product_assets
                    item["diversity_signals"] = diversity_signals(item, clothing_type)
                    qualified.append(item)
                    stats[f"{clothing_type}_qualified"] += 1

    stats["source_db"] = 1
    return qualified, stats


def _service_headers() -> dict[str, str]:
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not service_key:
        raise ExportError("SUPABASE_SERVICE_ROLE_KEY is not configured for REST fallback")
    return {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Accept": "application/json",
    }


def _json_list(response: httpx.Response, *, source: str) -> list[dict]:
    try:
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Never include response text: PostgREST diagnostics can echo filters/ids.
        error_code = None
        try:
            error_payload = response.json()
            if isinstance(error_payload, dict):
                value = error_payload.get("code")
                if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", value):
                    error_code = value
        except ValueError:
            pass
        suffix = f" (HTTP {response.status_code}, code {error_code})" if error_code else f" (HTTP {response.status_code})"
        raise ExportError(f"the Supabase {source} read failed{suffix}") from exc
    if not isinstance(payload, list):
        raise ExportError(f"the Supabase {source} response shape is invalid")
    return [row for row in payload if isinstance(row, dict)]


def _rest_rows(
    client: httpx.Client,
    *,
    base_url: str,
    headers: dict[str, str],
    table: str,
    params: dict[str, str],
) -> list[dict]:
    response = client.get(
        f"{base_url}/rest/v1/{table}", headers=headers, params=params
    )
    # Table name is a fixed internal constant and safe to surface; response
    # bodies and filters remain redacted because they may contain identifiers.
    return _json_list(response, source=f"registry table {table}")


def _rest_rows_for_ids(
    client: httpx.Client,
    *,
    base_url: str,
    headers: dict[str, str],
    table: str,
    id_field: str,
    ids: list[str],
    select: str,
    extra: dict[str, str] | None = None,
) -> list[dict]:
    result: list[dict] = []
    unique_ids = list(dict.fromkeys(ids))
    for start in range(0, len(unique_ids), REST_BATCH_SIZE):
        batch = unique_ids[start : start + REST_BATCH_SIZE]
        params = {"select": select, id_field: postgrest_uuid_in(batch)}
        params.update(extra or {})
        result.extend(
            _rest_rows(
                client,
                base_url=base_url,
                headers=headers,
                table=table,
                params=params,
            )
        )
    return result


def _qa_user_id(
    client: httpx.Client, *, base_url: str, headers: dict[str, str]
) -> str:
    response = client.get(
        f"{base_url}/auth/v1/admin/users",
        headers=headers,
        params={"email": QA_EMAIL, "page": "1", "per_page": "50"},
    )
    try:
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ExportError("the Supabase admin user lookup failed") from exc
    users = payload.get("users") if isinstance(payload, dict) else None
    if not isinstance(users, list):
        raise ExportError("the Supabase admin user response shape is invalid")
    exact = [
        user for user in users
        if isinstance(user, dict) and user.get("email") == QA_EMAIL
    ]
    if len(exact) != 1:
        raise ExportError("the fixed QA account was not uniquely found")
    user_id = str(exact[0].get("id") or "")
    postgrest_uuid_in([user_id])  # validate before it reaches any owner filter
    return user_id


def _load_candidates_rest(settings) -> tuple[list[dict], Counter]:
    """Read-only Supabase Admin + PostgREST fallback when direct PG is offline."""
    base_url = str(settings.supabase_url or "").rstrip("/")
    if not base_url.startswith("https://"):
        raise ExportError("a secure SUPABASE_URL is required for REST fallback")
    headers = _service_headers()
    stats: Counter = Counter()

    try:
        with httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
            user_id = _qa_user_id(client, base_url=base_url, headers=headers)
            projects = _rest_rows(
                client,
                base_url=base_url,
                headers=headers,
                table="projects",
                params={
                    "select": "id,updated_at,selected_mannequin_id",
                    "user_id": f"eq.{user_id}",
                    "deleted_at": "is.null",
                    "facemarket_license_id": "is.null",
                    "selected_mannequin_id": "not.is.null",
                    "order": "updated_at.desc",
                    "limit": str(REST_PROJECT_LIMIT),
                },
            )
            project_ids = [str(row.get("id") or "") for row in projects]
            if not project_ids:
                stats["scanned"] = 0
                stats["source_rest"] = 1
                return [], stats
            # Validating here ensures every later relation filter is rooted in an
            # owner-filtered, syntactically safe project id.
            postgrest_uuid_in(project_ids)

            products = _rest_rows_for_ids(
                client,
                base_url=base_url,
                headers=headers,
                table="products",
                id_field="project_id",
                ids=project_ids,
                select="project_id,name,clothing_type,colors",
            )
            analyses = _rest_rows_for_ids(
                client,
                base_url=base_url,
                headers=headers,
                table="analyses",
                id_field="project_id",
                ids=project_ids,
                select="project_id,payload",
            )
            cuts = _rest_rows_for_ids(
                client,
                base_url=base_url,
                headers=headers,
                table="mannequin_cuts",
                id_field="project_id",
                ids=project_ids,
                select="project_id,candidate,version,asset_id",
            )

            product_by_project = {
                str(row.get("project_id")): row for row in products
                if row.get("project_id")
            }
            analysis_by_project = {
                str(row.get("project_id")): row.get("payload") or {} for row in analyses
                if row.get("project_id")
            }
            cut_by_key = {
                (str(row.get("project_id")), str(row.get("candidate")), row.get("version")): row
                for row in cuts
            }

            # First assemble the exact selected cut and base-colour ids; then one
            # owner-filtered assets pass covers both kinds without prefix listing.
            staged: list[dict] = []
            all_asset_ids: list[str] = []
            for project in projects:
                project_id = str(project.get("id") or "")
                product = product_by_project.get(project_id)
                analysis = analysis_by_project.get(project_id)
                if not product or not isinstance(analysis, dict):
                    stats["missing_product_or_analysis"] += 1
                    continue
                clothing_type = normalize_clothing_type(product.get("clothing_type"))
                if clothing_type not in CLOTHING_TYPES:
                    stats["unsupported_clothing_type"] += 1
                    continue
                if not selected_virtual_model(analysis):
                    stats["non_virtual_model"] += 1
                    continue
                selected = parse_selected_mannequin(project.get("selected_mannequin_id"))
                if not selected:
                    stats[f"{clothing_type}_invalid_selected_mannequin"] += 1
                    continue
                cut = cut_by_key.get((project_id, selected[0], selected[1]))
                if not cut or not cut.get("asset_id"):
                    stats[f"{clothing_type}_missing_selected_mannequin"] += 1
                    continue
                pairs = product_image_pairs(product.get("colors"))
                if not pairs or not any(slot == "Front" for slot, _ in pairs):
                    stats[f"{clothing_type}_missing_front"] += 1
                    continue
                asset_ids = [str(cut["asset_id"]), *[asset_id for _slot, asset_id in pairs]]
                postgrest_uuid_in(asset_ids)
                all_asset_ids.extend(asset_ids)
                staged.append(
                    {
                        "project_id": project_id,
                        "updated_at": project.get("updated_at"),
                        "product_name": product.get("name"),
                        "clothing_type": product.get("clothing_type"),
                        "colors": product.get("colors"),
                        "analysis": analysis,
                        "normalized_clothing_type": clothing_type,
                        "selected_asset_id": str(cut["asset_id"]),
                        "pairs": pairs,
                    }
                )

            if not all_asset_ids:
                stats["scanned"] = len(projects)
                stats["source_rest"] = 1
                return [], stats
            assets = _rest_rows_for_ids(
                client,
                base_url=base_url,
                headers=headers,
                table="assets",
                id_field="id",
                ids=all_asset_ids,
                select="id,user_id,project_id,source,r2_bucket,r2_key,mime_type,byte_size",
                extra={"user_id": f"eq.{user_id}", "deleted_at": "is.null"},
            )
    except httpx.HTTPError as exc:
        raise ExportError("the Supabase read-only REST fallback could not connect") from exc

    assets_by_id = {str(row.get("id")): row for row in assets if row.get("id")}
    qualified: list[dict] = []
    stats["scanned"] = len(projects)
    for item in staged:
        clothing_type = item["normalized_clothing_type"]
        project_id = item["project_id"]
        mannequin_asset = assets_by_id.get(item["selected_asset_id"])
        if not (
            mannequin_asset
            and mannequin_asset.get("user_id") == user_id
            and mannequin_asset.get("project_id") == project_id
            and mannequin_asset.get("source") == "ai"
        ):
            stats[f"{clothing_type}_unregistered_mannequin_asset"] += 1
            continue

        product_assets = []
        valid_products = True
        for slot, asset_id in item["pairs"]:
            asset = assets_by_id.get(asset_id)
            if not (
                asset
                and asset.get("user_id") == user_id
                and asset.get("project_id") == project_id
                and asset.get("source") == "upload"
                and str(asset.get("mime_type") or "").startswith("image/")
            ):
                valid_products = False
                break
            product_assets.append({"slot": slot, **asset})
        if not valid_products:
            stats[f"{clothing_type}_unregistered_product_asset"] += 1
            continue

        row = dict(item)
        row.update(
            {
                "mannequin_bucket": mannequin_asset.get("r2_bucket"),
                "mannequin_key": mannequin_asset.get("r2_key"),
                "mannequin_mime": mannequin_asset.get("mime_type"),
                "mannequin_byte_size": mannequin_asset.get("byte_size"),
                "product_assets": product_assets,
            }
        )
        row["updated_at"] = _parse_rest_datetime(row.get("updated_at"))
        row["diversity_signals"] = diversity_signals(row, clothing_type)
        qualified.append(row)
        stats[f"{clothing_type}_qualified"] += 1

    stats["source_rest"] = 1
    return qualified, stats


def _parse_rest_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _load_candidates(settings) -> tuple[list[dict], Counter]:
    """Prefer direct PG; use read-only Supabase REST only when PG cannot connect."""
    if settings.database_url:
        try:
            return _load_candidates_db(settings)
        except psycopg.OperationalError:
            pass
    return _load_candidates_rest(settings)


def choose_candidates(candidates: list[dict]) -> dict[str, dict]:
    chosen: dict[str, dict] = {}
    for clothing_type in CLOTHING_TYPES:
        options = [
            row for row in candidates
            if row.get("normalized_clothing_type") == clothing_type
        ]
        if options:
            chosen[clothing_type] = max(options, key=selection_key)
    return chosen


def _extension(mime: str) -> str:
    value = str(mime or "").lower()
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/avif": ".avif",
        "image/gif": ".gif",
    }.get(value, ".img")


def _inspect_image(data: bytes, *, declared_mime: str) -> tuple[int, int, str]:
    if not data or len(data) > MAX_ASSET_BYTES:
        raise ExportError("an asset is empty or exceeds the 30 MiB local-test limit")
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            actual_format = str(image.format or "").lower()
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ExportError("an assets-registry object is not a readable image") from exc
    if width < 1 or height < 1:
        raise ExportError("an assets-registry image has invalid dimensions")
    return width, height, actual_format or declared_mime


def _write_immutable(path: pathlib.Path, data: bytes) -> None:
    digest = hashlib.sha256(data).hexdigest()
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ExportError("a local export path already contains different bytes")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(data)
    temporary.replace(path)


def _download_asset(
    *,
    settings,
    r2_clients: dict[str, R2Client],
    category_dir: pathlib.Path,
    asset: dict,
    stem: str,
) -> dict:
    bucket = asset.get("r2_bucket") or asset.get("bucket")
    key = asset.get("r2_key") or asset.get("key")
    mime = str(asset.get("mime_type") or asset.get("mime") or "")
    if not bucket or not key or not mime.startswith("image/"):
        raise ExportError("a selected registry asset has incomplete image metadata")
    if bucket not in r2_clients:
        r2_clients[bucket] = R2Client(settings, bucket=bucket)
    try:
        data = r2_clients[bucket].get_bytes(key)
    except Exception as exc:  # boto details may contain the private object key; redact them.
        raise ExportError("an R2 GET failed for a selected registered asset") from exc
    declared_size = asset.get("byte_size")
    if declared_size is not None and int(declared_size) != len(data):
        raise ExportError("an R2 object size differs from its assets-registry row")
    width, height, actual_format = _inspect_image(data, declared_mime=mime)
    path = category_dir / f"{stem}{_extension(mime)}"
    _write_immutable(path, data)
    return {
        "path": str(path.relative_to(OUT_ROOT)),
        "mime": mime,
        "bytes": len(data),
        "width": width,
        "height": height,
        "format": actual_format,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def export_candidates(settings, chosen: dict[str, dict]) -> pathlib.Path:
    if not chosen:
        raise ExportError("no qualifying registered QA-project inputs were found")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    r2_clients: dict[str, R2Client] = {}
    exported: list[dict] = []

    for clothing_type in CLOTHING_TYPES:
        row = chosen.get(clothing_type)
        if not row:
            continue
        project_tag = anonymized_project_tag(row["project_id"])
        category_dir = OUT_ROOT / clothing_type / project_tag
        mannequin_asset = {
            "r2_bucket": row["mannequin_bucket"],
            "r2_key": row["mannequin_key"],
            "mime_type": row["mannequin_mime"],
            "byte_size": row["mannequin_byte_size"],
        }
        mannequin_file = _download_asset(
            settings=settings,
            r2_clients=r2_clients,
            category_dir=category_dir,
            asset=mannequin_asset,
            stem="selected_mannequin",
        )
        product_files = []
        for index, asset in enumerate(row["product_assets"], start=1):
            slot = str(asset["slot"]).lower()
            exported_asset = _download_asset(
                settings=settings,
                r2_clients=r2_clients,
                category_dir=category_dir,
                asset=asset,
                stem=f"product_{index:02d}_{slot}",
            )
            exported_asset["slot"] = asset["slot"]
            product_files.append(exported_asset)

        analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
        exported.append(
            {
                "projectTag": project_tag,
                "clothingType": clothing_type,
                "productName": str(row.get("product_name") or ""),
                "selectedVirtualModelId": selected_virtual_model(analysis),
                "updatedAt": row["updated_at"].isoformat() if row.get("updated_at") else None,
                "diversitySignals": row["diversity_signals"],
                "analysisFeatures": _manifest_features(analysis),
                "mannequin": mannequin_file,
                "productImages": product_files,
            }
        )

    manifest = {
        "schemaVersion": 1,
        "purpose": "read-only real QA-project inputs for runtime cut-generation A/B",
        "sourcePolicy": {
            "account": QA_EMAIL,
            "databaseRegistryOnly": True,
            "r2PrefixListing": False,
            "faceMarketExcluded": True,
            "personalizationAssetsExcluded": True,
            "allowedVirtualModels": sorted(VIRTUAL_MODEL_IDS),
            "productAssetSource": "upload",
            "mannequinAssetSource": "ai",
        },
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "missingCategories": [kind for kind in CLOTHING_TYPES if kind not in chosen],
        "projects": exported,
    }
    manifest_path = OUT_ROOT / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest_path


def _print_stats(stats: Counter, chosen: dict[str, dict], *, inspect_only: bool) -> None:
    print("QA project registry inspection (anonymized)")
    source = "postgres" if stats["source_db"] else "supabase-rest"
    print(f"  source: {source}")
    print(f"  scanned selected-mannequin projects: {stats['scanned']}")
    for clothing_type in CLOTHING_TYPES:
        qualified = stats[f"{clothing_type}_qualified"]
        print(
            f"  {clothing_type}: qualified={qualified}, "
            f"selected={'yes' if clothing_type in chosen else 'no'}"
        )
    skipped = sum(
        value for key, value in stats.items()
        if key != "scanned"
        and not key.startswith("source_")
        and not key.endswith("_qualified")
    )
    print(f"  skipped by safety/completeness gates: {skipped}")
    if inspect_only:
        print("  inspect-only: no R2 objects downloaded")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inspect-only",
        action="store_true",
        help="query/count owner-scoped candidates without any R2 GET or local write",
    )
    args = parser.parse_args()
    try:
        settings = load_settings()
        candidates, stats = _load_candidates(settings)
        chosen = choose_candidates(candidates)
        _print_stats(stats, chosen, inspect_only=args.inspect_only)
        if args.inspect_only:
            return 0
        manifest_path = export_candidates(settings, chosen)
        print(f"  local export complete: {manifest_path.relative_to(pathlib.Path.cwd())}")
        return 0
    except ExportError as exc:
        print(f"export failed safely: {exc}")
        return 2
    except Exception as exc:
        # Avoid accidental UUID/R2-key/credential leakage from driver exception text.
        print(f"export failed safely: unexpected {type(exc).__name__}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
