// A missing/blocked browser store always leaves the template-aware default available.
const storageKey = (projectId) => `wearless:editor:stitched:${projectId}`;
export function readStitchedPreference(projectId, storage) {
    try {
        if (!projectId) return null;
        const saved = (storage ?? globalThis.localStorage)?.getItem(
            storageKey(projectId),
        );
        return saved === "true" ? true : saved === "false" ? false : null;
    } catch {
        return null;
    }
}
export function writeStitchedPreference(projectId, value, storage) {
    try {
        if (projectId)
            (storage ?? globalThis.localStorage)?.setItem(
                storageKey(projectId),
                String(value),
            );
    } catch {
        /* Keep the in-memory choice for this visit. */
    }
}
export const defaultStitchedMode = (blocks) =>
    Boolean(blocks?.some((block) => block.pageDesignId));
export function canvasBlockRuns(blocks = []) {
    const runs = [];
    blocks.forEach((block, index) => {
        const last = runs.at(-1);
        if (block.pageSectionKey && last?.key === block.pageSectionKey)
            last.items.push({ block, index });
        else
            runs.push({
                key: block.pageSectionKey || null,
                id: block.id,
                items: [{ block, index }],
            });
    });
    return runs;
}
