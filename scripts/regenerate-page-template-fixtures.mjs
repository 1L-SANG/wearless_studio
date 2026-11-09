// Explicit maintenance command. Tests never rewrite their expected results.
import { readFileSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { isDeepStrictEqual } from "node:util";
import {
    assemblePage,
    collectOverlayRegions,
} from "../src/lib/pageTemplates/assemble.js";
import { pageTemplatesCatalog } from "../src/lib/pageTemplates/catalog.js";
import { seedStoryboard } from "../src/lib/pageTemplates/seed.js";
import { addTemplateSection, removeTemplateSection, changeTemplateSectionDesign } from "../src/lib/pageTemplates/sectionActions.js";
import { sectionRuns } from "../src/lib/pageTemplates/sectionRuns.js";
import { patchStoryboardPhoto } from "../src/lib/pageTemplates/photoActions.js";
import { assignGenerationExamples } from "../src/lib/generationExamples.js";
import { validateStoryboardV4 } from "../src/lib/pageTemplates/validateStoryboard.js";
import mockFullDetails from "../src/mock/detailRecommendationsFixture.json" with { type: "json" };
import { Placeholder } from "../src/mock/placeholders.js";
const path = new URL(
    "../tests/fixtures/page_templates_cases.json",
    import.meta.url,
);
const fixture = JSON.parse(readFileSync(path, "utf8"));
// Retained layouts are common design coverage, independent of the removed
// guide template. Keep one points run per fixture under the singleton contract.
const translateCommon = value => {
    if (typeof value === 'string') return value === 'guide' ? 'emo' : value.replace(/^guide\./, 'emo.');
    if (Array.isArray(value)) return value.map(translateCommon);
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
        .map(([key, item]) => [key.replace(/^guide\./, 'emo.'), translateCommon(item)]));
    return value;
};
fixture.cases = fixture.cases.filter(item => !/^woc[23]-/.test(item.name)).map(item => {
    if (!item.name.startsWith('guide-')) return item;
    const input = translateCommon(item.input);
    const album = item.name.includes('fit-album') || item.name === 'guide-3-point';
    const removedKey = album ? 'emo.points_detail' : 'emo.points_fit';
    const removedIds = new Set(input.storyboard.filter(block => block.pageSectionKey === removedKey).map(block => block.id));
    input.storyboard = input.storyboard.filter(block => !removedIds.has(block.id));
    input.cutResults = input.cutResults.filter(cut => !removedIds.has(cut.blockId));
    input.copyResults = input.copyResults.filter(copy => !removedIds.has(copy.blockId));
    return { name: item.name.replace(/^guide-/, 'common-'), input };
});
fixture.catalogVersion = pageTemplatesCatalog.catalogVersion;

const recommendation = count => ({ version: 1, status: 'ready', candidates: [
    { id: 'fabric', kind: 'fabric', label: '원단', rank: 1, informationGroup: 'fabric', photoUse: 'standalone', visibility: 'clear', direction: 'front' },
    ...Array.from({ length: count }, (_, index) => ({ id: `part${index + 1}`, kind: 'structure', label: ['목선', '단추', '소맷단'][index],
        rank: index + 2, informationGroup: `part${index + 1}`, photoUse: 'standalone', visibility: 'clear', direction: 'front' })),
] });
function stableBlocks(blocks, name) {
    const ids = new Map(blocks.map((block, index) => [block.id, `${name}.block${index + 1}`])), groups = new Map(), sections = new Map();
    return blocks.map(block => {
        const { seedFingerprint, seedProductKey, ...next } = structuredClone(block);
        next.id = ids.get(block.id);
        if (!sections.has(block.sectionId)) sections.set(block.sectionId, `${name}.section${sections.size + 1}`);
        next.sectionId = sections.get(block.sectionId);
        if (block.spaceGroupId) {
            if (!groups.has(block.spaceGroupId)) groups.set(block.spaceGroupId,
                block.spaceGroupId.replace(/__[^_]+_[^_]+$/, `__fixture_${groups.size + 1}`));
            next.spaceGroupId = groups.get(block.spaceGroupId);
        }
        if (next.similarSourceBlockId) next.similarSourceBlockId = ids.get(next.similarSourceBlockId);
        if (next.pointItems) next.pointItems = next.pointItems.map(item => ({ ...item, sourceBlockId: ids.get(item.sourceBlockId) }));
        return next;
    });
}
function appendCase(name, input, cutMetadata = { width: 1024, height: 1365 }) {
    input.storyboard = stableBlocks(input.storyboard, name);
    if (input.storyboard[0]?.pageSectionKey === 'show.first') input.storyboard = assignGenerationExamples(input.storyboard,
        { product: input.product, analysis: input.analysis, gender: 'women', projectId: 'fixture-woc2', catalog: [], onlyBlockIds: [input.storyboard[0].id] }).blocks;
    input.cutResults = input.storyboard.filter(block => block.source === 'ai')
        .map(block => ({ blockId: block.id, imageUrl: `photo:${block.id}`, ...cutMetadata }));
    input.copyResults = [];
    fixture.cases.push({ name, input });
}
for (const templateId of ['emo', 'show']) {
    const source = fixture.cases.find(item => item.name === `${templateId}-2-mono`).input;
    for (const count of [0, 1, 2, 3]) {
        const analysis = { ...structuredClone(source.analysis), targetGenders: ['women'], detailRecommendations: recommendation(count) };
        appendCase(`woc2-${templateId}-details-${count}`, { product: structuredClone(source.product), analysis,
            storyboard: seedStoryboard(source.product, analysis, { projectId: 'fixture-woc2' }) });
    }
    for (const colorCount of [3, 4]) for (const colorMode of ['mono', 'point']) {
        const product = { ...structuredClone(source.product), colors: Array.from({ length: colorCount }, (_, index) => ({ id: `color${index + 1}`,
            name: ['아이보리', '그레이', '네이비', '블랙'][index], swatchId: ['ivory', 'gray', 'navy', 'black'][index], isBase: index === 0 })) };
        const analysis = { ...structuredClone(source.analysis), pageStyle: { ...source.analysis.pageStyle, colorMode }, detailRecommendations: recommendation(2) };
        appendCase(`woc2-${templateId}-${colorCount}-neutral-${colorMode}`, { product, analysis,
            storyboard: seedStoryboard(product, analysis, { projectId: 'fixture-woc2' }) });
    }
    const analysis = { ...structuredClone(source.analysis), detailRecommendations: recommendation(0) };
    const product = structuredClone(source.product);
    let storyboard = addTemplateSection(seedStoryboard(product, analysis), product, analysis, 'points');
    const cropSourceId = storyboard.flatMap(block => block.pointItems || [])[0].sourceBlockId;
    appendCase(`woc2-${templateId}-crops`, { product, analysis, storyboard });
    // Old boards keep their stored ROI even after the crop defaults change.
    const savedRegions = [[.15, .16, .7, .35], [.2, .15, .6, .3], [.1, .38, .8, .45]];
    appendCase(`woc2-${templateId}-crops-saved`, { product, analysis, storyboard: storyboard.map(block => block.pointItems
        ? { ...block, pointItems: block.pointItems.map((item, index) => ({ ...item, cropRegion: savedRegions[index] })) } : block) });
    if (templateId === 'emo') for (const [suffix, meta] of [
        ['tiny', { width: 96, height: 144 }], ['large', { width: 3072, height: 4608 }],
        ['unknown', {}], ['preview', { width: 1024, height: 1365, dimensionsKnown: false }],
    ]) appendCase(`woc2-${templateId}-crops-${suffix}`, { product, analysis, storyboard }, meta);
    appendCase(`woc2-${templateId}-crops-mine`, { product, analysis,
        storyboard: patchStoryboardPhoto(storyboard, cropSourceId, { source: 'mine', cutType: null, ownImages: ['/seller-worn.png'] }, product) });
    storyboard = addTemplateSection(storyboard, product, analysis, 'sns');
    const reuseKey = `${templateId}.wear_styling`;
    appendCase(`woc2-${templateId}-sns-fallback`, { product, analysis,
        storyboard: removeTemplateSection(storyboard, reuseKey, { product }) });
    const runs = sectionRuns(storyboard);
    appendCase(`woc2-${templateId}-shuffled`, { product, analysis,
        storyboard: [runs[0], ...runs.slice(1).reverse()].flatMap(run => run.blocks) });
}
for (const templateId of ['emo', 'show']) {
    const source = fixture.cases.find(item => item.name === `${templateId}-2-mono`).input;
    const product = structuredClone(source.product);
    for (const color of product.colors || []) color.images = (color.images || []).filter(image => !['Detail', 'BackDetail'].includes(image.slot));
    const analysis = { ...structuredClone(source.analysis), targetGenders: ['women'], detailRecommendations: recommendation(3) };
    const noFabric = { ...analysis, detailRecommendations: { ...analysis.detailRecommendations,
        candidates: analysis.detailRecommendations.candidates.filter(candidate => candidate.kind !== 'fabric') } };
    appendCase(`woc3-${templateId}-no-fabric`, { product, analysis: noFabric, storyboard: seedStoryboard(product, noFabric, { projectId: 'fixture-woc3' }) });
    const initial = seedStoryboard(product, analysis, { projectId: 'fixture-woc3' });
    appendCase(`woc3-${templateId}-four-detail`, { product, analysis, storyboard: initial });
    const mockProduct = structuredClone(product);
    const mockBase = mockProduct.colors.find(color => color.isBase) || mockProduct.colors[0];
    mockBase.images = [
        { id: 'mock-front', slot: 'Front', src: Placeholder.photo('c1f', 'horizon', 300, 400) },
        { id: 'mock-back', slot: 'Back', src: Placeholder.photo('c1b', 'horizon', 300, 400) },
        { id: 'mock-detail', slot: 'Detail', src: Placeholder.detail('c1d', 300, 400) },
        { id: 'mock-back-detail', slot: 'BackDetail', src: Placeholder.detail('c1bd', 300, 400) },
    ];
    for (const mode of ['default', 'full']) {
        const mockAnalysis = { ...analysis, detailRecommendations: mode === 'full' ? structuredClone(mockFullDetails)
            : { version: 1, status: 'unavailable', candidates: [] } };
        appendCase(`woc3-${templateId}-mock-detail-${mode}`, { product: mockProduct, analysis: mockAnalysis,
            storyboard: seedStoryboard(mockProduct, mockAnalysis, { projectId: 'fixture-woc3' }) });
    }
    for (const [slot, side] of [['Detail', 'front'], ['BackDetail', 'back']]) {
        const originals = structuredClone(product);
        originals.colors[0].images.push({ id: `seller-fabric-${side}`, slot, src: `seller:${side}` });
        const originalAnalysis = { ...analysis, detailRecommendations: { version: 1, status: 'ready', candidates: [] } };
        appendCase(`woc3-${templateId}-seller-fabric-${side}`, { product: originals, analysis: originalAnalysis,
            storyboard: seedStoryboard(originals, originalAnalysis, { projectId: 'fixture-woc3' }) });
    }
    // Legacy zero-photo fabric is stored data, not a new-seed fallback.
    const fabric = initial.find(block => block.pageSectionKind === 'fabric');
    const legacyText = { id: `legacy:${fabric.id}`, taxonomyVersion: 4, pageSectionKey: fabric.pageSectionKey,
        pageSectionKind: 'fabric', pageDesignId: fabric.pageDesignId, sectionId: fabric.sectionId,
        sectionLayout: 'stack', sectionCustom: false, source: 'text', slotKey: null, cutType: null,
        contentRole: null, sectionRole: null };
    appendCase(`woc3-${templateId}-saved-text-fabric`, { product, analysis: noFabric,
        storyboard: initial.flatMap(block => block.pageSectionKind === 'fabric' ? block === fabric ? [legacyText] : [] : [block]) });
    for (const [kind, designId] of [['review', 'I09-SCRIM-01'], ['faq', 'T08-SCRIM-01']]) {
        const present = initial.some(block => block.pageSectionKind === kind) ? initial : addTemplateSection(initial, product, analysis, kind);
        const key = present.find(block => block.pageSectionKind === kind).pageSectionKey;
        appendCase(`woc3-${templateId}-${kind}-gradient`, { product, analysis,
            storyboard: changeTemplateSectionDesign(present, key, designId, { product }) });
    }
    if (templateId === 'show') {
        const ghost = changeTemplateSectionDesign(initial, 'show.first', 'P01-D01', { product });
        appendCase('woc3-show-retained-ghost', { product, analysis, storyboard: ghost });
        appendCase('woc3-show-ghost-source-removed', { product, analysis,
            storyboard: removeTemplateSection(ghost, 'show.color_product', { product }) });
        appendCase('woc3-show-new-first-source-removed', { product, analysis,
            storyboard: removeTemplateSection(initial, 'show.color_product', { product }) });
    }
}
for (const item of fixture.cases.filter(item => /^woc[23]-/.test(item.name))) validateStoryboardV4(item.input.storyboard);
const outputs = fixture.cases.map(({ input }) => ({
    blocks: assemblePage(input),
    regions: collectOverlayRegions(input),
}));
const python = spawnSync(
    process.env.PAGE_TEMPLATE_PYTHON || "server/.venv/bin/python",
    [
        "-c",
        `
import sys,json
sys.path[:0]=['server','server/tests']
from page_template_cases import assemble_input,collect_input_regions
from app.agents.content_roles import validate_storyboard_v4
values=json.load(sys.stdin)
for value in values:
 if value['storyboard'][0]['id'].startswith(('woc2-', 'woc3-')): validate_storyboard_v4(value['storyboard'])
print(json.dumps([dict(blocks=assemble_input(c),regions=collect_input_regions(c)) for c in values],ensure_ascii=False))
`,
    ],
    {
        input: JSON.stringify(fixture.cases.map((c) => c.input)),
        encoding: "utf8",
        maxBuffer: 32 * 1024 * 1024,
    },
);
if (python.status !== 0) throw new Error(python.stderr);
const server = JSON.parse(python.stdout);
for (const [index, output] of outputs.entries()) {
    if (!isDeepStrictEqual(output, server[index]))
        throw new Error(`JS/Python mismatch: ${fixture.cases[index].name}`);
    fixture.cases[index].expected = output.blocks;
    fixture.cases[index].expectedOverlayRegions = output.regions;
}
writeFileSync(path, JSON.stringify(fixture, null, 2) + "\n");
console.log(
    `Regenerated ${outputs.length} cases after exact JS/Python agreement.`,
);
