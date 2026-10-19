"""Post-commit outfit inference. Reads never enqueue work or call a provider."""
import asyncio
from copy import deepcopy
import logging

from .. import repo
from ..agents import detail_recommendations
from . import outfit

log = logging.getLogger(__name__)


def start_project(app, user_id, project_id, product, analysis, source_images, items, *, slots=None):
    from ..public_routes import _analysis_semaphore
    fields = outfit.start_recommendation(app.state.settings, app.state.r2, product, analysis,
        source_images, items, slots=slots, semaphore=_analysis_semaphore)
    follow_existing(app, user_id, project_id, product, analysis, fields)
    return fields


def follow_existing(app, user_id, project_id, product, analysis, fields):
    """Subscribe to already-started inference, including a completed cache hit."""
    key = fields['outfitRecommendation']['contextKey']
    running = getattr(app.state, 'outfit_tasks', None)
    if running is None:
        running = app.state.outfit_tasks = {}
    task_key = (str(project_id), key)
    if task_key in running:
        return fields
    model_task = outfit._inflight.get(key)
    context = outfit.product_context(product, analysis)
    sources = detail_recommendations.analysis_source_entries(product)

    async def complete():
        try:
            result = deepcopy(await asyncio.shield(model_task)) if model_task else outfit.read_recommendation(key) or deepcopy(fields)
            if not result or result['outfitRecommendation']['status'] == 'pending':
                return
            async with app.state.pool.connection() as conn:
                latest = await repo.get_analysis_for_update(conn, project_id) or {}
                current_product = await repo.get_product(conn, project_id) or {}
                if ((latest.get('outfitRecommendation') or {}).get('contextKey') != key
                    or outfit.product_context(current_product, latest) != context
                    or detail_recommendations.analysis_source_entries(current_product) != sources):
                    return
                current_items = await outfit.load_items(conn, user_id, project_id)
                pools = outfit.candidate_pools(current_items, context)
                # Disappeared catalog IDs never become a different silently selected item.
                if result['outfitRecommendation']['sets']:
                    result['outfitRecommendation']['sets'] = outfit.validate_sets(
                        result['outfitRecommendation']['sets'], pools, latest.get('outfitSelections'))
                recommendation = result['outfitRecommendation']
                for role in outfit.PROP_ROLES:
                    outfit.copy_prop(recommendation, latest.get('outfitRecommendation') or {}, role)
                recommendation = outfit.recommendation_display(recommendation, pools, app.state.r2)
                # Late AI changes only suggestions. Every current selection remains authoritative.
                updates = {'outfitRecommendation': recommendation}
                await repo.save_outfit_recommendation(conn, project_id, updates, expected_analysis=latest)
                await conn.commit()
        except Exception as exc:
            log.warning('outfit background completion unavailable: %s', type(exc).__name__)
        finally:
            running.pop(task_key, None)
    task = asyncio.create_task(complete(), name='outfit-project-result')
    running[task_key] = task
    return fields



def follow_props(app, user_id, project_id, product, analysis, fields, role):
    key = fields["outfitRecommendation"]["propsContextKeys"][role]
    running = getattr(app.state, "outfit_tasks", None)
    if running is None:
        running = app.state.outfit_tasks = {}
    task_key = (str(project_id), key, role)
    if task_key in running:
        return
    model_task = outfit._inflight.get(key)
    context = outfit.product_context(product, analysis)
    sources = detail_recommendations.analysis_source_entries(product)
    async def complete():
        try:
            result = deepcopy(await asyncio.shield(model_task)) if model_task else outfit.read_recommendation(key)
            if not result or result["outfitRecommendation"]["props"][role]["status"] == "pending":
                return
            async with app.state.pool.connection() as conn:
                latest = await repo.get_analysis_for_update(conn, project_id) or {}
                current_product = await repo.get_product(conn, project_id) or {}
                recommendation = deepcopy(latest.get("outfitRecommendation") or {})
                if ((recommendation.get("propsContextKeys") or {}).get(role) != key
                    or outfit.product_context(current_product, latest) != context
                    or detail_recommendations.analysis_source_entries(current_product) != sources):
                    return
                items = await outfit.load_items(conn, user_id, project_id)
                pools = outfit.candidate_pools(items, context)
                checked = outfit.validate_props({"props": {role: result["outfitRecommendation"]["props"][role]}}, pools)
                outfit.copy_prop(recommendation, result["outfitRecommendation"], role)
                recommendation["props"][role] = checked["props"][role]
                recommendation = outfit.recommendation_display(recommendation, pools, app.state.r2)
                await repo.save_outfit_recommendation(conn, project_id,
                    {"outfitRecommendation": recommendation}, expected_analysis=latest)
                await conn.commit()
        except Exception as exc:
            log.warning("outfit props background completion unavailable: %s", type(exc).__name__)
        finally:
            running.pop(task_key, None)
    running[task_key] = asyncio.create_task(complete(), name="outfit-project-props-result")
