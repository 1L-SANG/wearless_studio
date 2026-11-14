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
import { sectionRuns, changeTemplateSectionDesign as restoreSavedDesign } from "../src/lib/pageTemplates/sectionRuns.js";
import { patchStoryboardPhoto } from "../src/lib/pageTemplates/photoActions.js";
import { assignGenerationExamples } from "../src/lib/generationExamples.js";
import { validateStoryboardV4 } from "../src/lib/pageTemplates/validateStoryboard.js";
import mockFullDetails from "../src/mock/detailRecommendationsFixture.json" with { type: "json" };
import { withMockPartDescriptions } from "../src/mock/partDescriptionCopy.js";
import { Placeholder } from "../src/mock/placeholders.js";
import { stageAdditions, addStageAddition } from "../src/lib/pageTemplates/stageAdditions.js";
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
fixture.cases = fixture.cases.filter(item => !/^woc(?:[2356]|6b|11)-/.test(item.name)).map(item => {
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
// This retained case exercises AI paragraph splitting without seller-owned copy.
fixture.cases.find(item => item.name === 'emo-md-paragraphs').input.analysis.mdComment = '';
// These original fixtures describe deliberate ghost recipes, not undecided
// legacy products. Make that intention explicit under the section mode contract.
for (const item of fixture.cases) for (const block of item.input.storyboard) {
    if (block.source === 'ai' && block.cutType === 'product' && block.shot === 'ghost'
        && block.pageSectionKind === 'color_product' && !block.productPresentation) block.productPresentation = 'ghost';
}

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
        .map(block => ({ blockId: block.id, imageUrl: `photo:${block.id}`, ...(typeof cutMetadata === 'function' ? cutMetadata(block) : cutMetadata) }));
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
            storyboard: restoreSavedDesign(present, key, designId) });
    }
    if (templateId === 'show') {
        const base = restoreSavedDesign(initial, 'show.first', 'P01-D11');
        const ghost = addStageAddition(base, 'show.first', stageAdditions(base, 'show.first', { product, analysis })[0], { product, analysis });
        appendCase('woc3-show-retained-ghost', { product, analysis, storyboard: ghost });
        appendCase('woc3-show-ghost-source-removed', { product, analysis,
            storyboard: removeTemplateSection(ghost, 'show.color_product', { product }) });
        appendCase('woc3-show-new-first-source-removed', { product, analysis,
            storyboard: removeTemplateSection(initial, 'show.color_product', { product }) });
    }
}
for (const templateId of ['emo', 'show']) {
    const source = fixture.cases.find(item => item.name === `${templateId}-2-mono`).input;
    const product = structuredClone(source.product), analysis = { ...structuredClone(source.analysis), detailRecommendations: recommendation(3) };
    const initial = seedStoryboard(product, analysis, { projectId: 'fixture-woc5' });
    for (const [kind, base] of [['first', 'P01-D11'], ['first', 'P01-D09'], ['first', 'P01-SHOW-01'], ['fabric', 'T03-D01'], ['fabric', 'T03-D06']]) {
        const run = sectionRuns(initial).find(run => run.kind === kind);
        const board = restoreSavedDesign(initial, run.key, base);
        const paired = addStageAddition(board, run.key, stageAdditions(board, run.key, { product, analysis })[0], { product, analysis });
        appendCase(`woc5-${templateId}-${base}-reuse`, { product, analysis, storyboard: paired });
    }
    for (const mode of ['flat', 'natural_light']) {
        const board = patchStoryboardPhoto(initial, initial.find(b => b.pageSectionKind === 'color_product').id, { productPresentation: mode }, product);
        const first = sectionRuns(board)[0], paired = addStageAddition(board, first.key,
            stageAdditions(board, first.key, { product, analysis })[0], { product, analysis });
        appendCase(`woc5-${templateId}-first-${mode}-dedicated`, { product, analysis, storyboard: paired });
    }
    const cropAnalysis = { ...analysis, detailRecommendations: recommendation(0) };
    let cropBoard = addTemplateSection(seedStoryboard(product, cropAnalysis), product, cropAnalysis, 'points');
    cropBoard = removeTemplateSection(cropBoard, `${templateId}.fabric`, { product });
    const points = sectionRuns(cropBoard).find(run => run.kind === 'points');
    cropBoard = addStageAddition(cropBoard, points.key, stageAdditions(cropBoard, points.key, { product, analysis: cropAnalysis })[0], { product, analysis: cropAnalysis });
    appendCase(`woc5-${templateId}-crops-and-fabric`, { product, analysis: cropAnalysis, storyboard: cropBoard });
}
// WOC6 curated combinations, real detail ratio, style order and natural fallbacks.
for (const templateId of ['emo', 'show']) {
    const source = fixture.cases.find(item => item.name === `${templateId}-2-mono`).input;
    const product = structuredClone(source.product);
    const analysis = { ...structuredClone(source.analysis), targetGenders: ['women'], detailRecommendations: recommendation(3) };
    let initial = seedStoryboard(product, analysis, { projectId: 'fixture-woc6' });
    if (!sectionRuns(initial).some(run => run.kind === 'wear' && run.usage === 'styling')) {
        initial = addTemplateSection(initial, product, analysis, 'wear', 'styling');
        const run = sectionRuns(initial).find(run => run.kind === 'wear' && run.usage === 'styling');
        const addition = stageAdditions(initial, run.key, { product, analysis }).find(item => item.kind === 'spaceMember');
        if (!addition) throw new Error('WOC6 styling fixture needs a third published member');
        initial = addStageAddition(initial, run.key, addition, { product, analysis });
    }
    const metrics = block => block.shot === 'detail' ? { width: 1024, height: 1536 } : { width: 1024, height: 1280 };
    const savedPointIds = ['PT-COLUMN-01', 'P06-D01', 'PT-COLUMN-CARD-01', 'PT-BELOW-CARD-01'];
    for (const design of Object.values(pageTemplatesCatalog.designs).filter(design => (!design.pickerHidden || savedPointIds.includes(design.id)) && design.choiceAxes && !design.framePhotos)) {
        const run = sectionRuns(initial).find(run => run.kind === design.kind && (design.kind !== 'wear' || run.usage === design.usage));
        const storyboard = savedPointIds.includes(design.id) ? restoreSavedDesign(initial, run.key, design.id)
            : changeTemplateSectionDesign(initial, run.key, design.id, { product });
        if (storyboard.find(b => b.pageSectionKey === run.key).pageDesignId !== design.id) throw new Error(`WOC6 fixture design rejected: ${design.id}`);
        appendCase(`woc6-${templateId}-${design.id}`, { product, analysis, storyboard }, metrics);
        const saved = fixture.cases.at(-1).input, lead = saved.storyboard.find(b => b.pageSectionKind === 'points' && b.source === 'text');
        saved.copyResults = [{ blockId: lead.id, texts: ['목선 마감', '여밈 마감', '소맷단 마감'].map((text, i) => ({ role: `items.${i}.desc`, text })) }];
    }
    for (const fontStyle of Object.keys(pageTemplatesCatalog.fontStyles)) {
        appendCase(`woc6-${templateId}-font-${fontStyle}`, { product, analysis: { ...analysis, pageStyle: { ...analysis.pageStyle, fontStyle } }, storyboard: initial }, metrics);
    }
    for (const [name, size] of [['unknown', {}], ['wide', { width: 1800, height: 900 }], ['portrait', { width: 600, height: 900 }]]) {
        appendCase(`woc6-${templateId}-detail-${name}`, { product, analysis, storyboard: initial }, block => block.shot === 'detail' ? size : metrics(block));
    }
}
// WOC6B frames use existing storyboard fields only; image data is fixture-local.
for (const templateId of ['emo', 'show']) {
    const source = fixture.cases.find(item => item.name === `${templateId}-2-mono`).input;
    const product = structuredClone(source.product);
    const analysis = { ...structuredClone(source.analysis), targetGenders: ['women'], detailRecommendations: recommendation(3) };
    const shopName = '웨어리스 테스트몰';
    const makeBoard = groups => {
        let board = seedStoryboard(product, analysis, { projectId: 'fixture-woc6b' });
        if (!sectionRuns(board).some(run => run.kind === 'wear' && run.usage === 'styling')) board = addTemplateSection(board, product, analysis, 'wear', 'styling');
        const run = sectionRuns(board).find(run => run.kind === 'wear' && run.usage === 'styling');
        const original = run.blocks.find(block => block.source !== 'text'), instance = run.key.split('.').slice(1).join('.');
        let index = 0;
        const photos = groups.flatMap((shots, group) => shots.map((shot, member) => ({ ...original,
            id: `frame-photo-${++index}`, slotKey: `${instance}.g${index}`, shot, exampleId: null,
            spaceGroupId: original.spaceGroupId.replace(/__[^_]+_[^_]+$/, `__woc6b_${group + 1}`), spaceSetMemberOrder: member,
        })));
        return { key: run.key, board: [...board.slice(0, run.start), ...photos, ...board.slice(run.end)] };
    };
    const addFrameCase = (suffix, layout, count, groups, extra = {}, metrics = { width: 900, height: 1200 }) => {
        const { board, key } = makeBoard(groups), id = `WEAR-STYLING-FRAME-${layout}-0${count}`;
        const storyboard = restoreSavedDesign(board, key, id);
        if (storyboard.find(block => block.pageSectionKey === key).pageDesignId !== id) throw new Error(`WOC6B frame fixture rejected ${id}`);
        appendCase(`woc6b-${templateId}-${suffix}`, { product, analysis, shopName, ...extra, storyboard }, metrics);
    };
    const standard = [['full', 'medium', 'medium'], ['full', 'medium', 'medium']];
    for (const layout of ['COLUMN', '2COL', 'HERO']) for (const count of [1, 2, 3]) addFrameCase(`${layout}-${count}`, layout, count, standard);
    addFrameCase('medium-winner', 'HERO', 3, [['medium', 'full'], ['full', 'medium', 'medium', 'medium']]);
    addFrameCase('full-fill', 'COLUMN', 3, [['full', 'medium', 'medium'], ['full']]);
    addFrameCase('other-fill', '2COL', 3, [['medium'], ['full', 'full']]);
    addFrameCase('no-medium', 'HERO', 2, [['full', 'full'], ['full']]);
    addFrameCase('no-shop', 'COLUMN', 2, standard, { shopName: null });
    addFrameCase('no-remainder', 'COLUMN', 2, [['medium', 'medium']]);
    addFrameCase('unknown-size', '2COL', 2, standard, {}, {});
    for (const [shape, width, height] of [['square', 600, 600], ['landscape', 900, 450]]) {
        const { board, key } = makeBoard([['medium']]);
        const photo = board.find(block => block.pageSectionKey === key && block.source !== 'text');
        const src = `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"/>`)}`;
        const mine = patchStoryboardPhoto(board, photo.id, { source: 'mine', cutType: null, ownImages: [src] }, product);
        appendCase(`woc6b-${templateId}-frame-mine-unknown-${shape}`, { product, analysis, shopName,
            storyboard: restoreSavedDesign(mine, key, 'WEAR-STYLING-FRAME-COLUMN-01') }, {});
    }
    const parts = { ...analysis, partDescriptionCopy: ['목선', '단추', '소맷단'].map(point => ({ point, desc: `${point} 설명` })) };
    const detailBoard = seedStoryboard(product, parts);
    const detailKey = sectionRuns(detailBoard).find(run => run.kind === 'points').key;
    appendCase(`woc6b-${templateId}-part-copy`, { product, analysis: parts, storyboard: restoreSavedDesign(detailBoard, detailKey, 'PT-COLUMN-CARD-01') });
    const wornAnalysis = { ...analysis, detailRecommendations: recommendation(0) };
    const wornBoard = addTemplateSection(seedStoryboard(product, wornAnalysis), product, wornAnalysis, 'points');
    const worn = sectionRuns(wornBoard).find(run => run.kind === 'points');
    wornAnalysis.partDescriptionCopy = worn.blocks.flatMap(block => block.pointItems || []).map(item => ({ point: item.label, desc: `${item.label} 설명` }));
    appendCase(`woc6b-${templateId}-worn-copy`, { product, analysis: wornAnalysis, storyboard: restoreSavedDesign(wornBoard, worn.key, 'PT-BELOW-CARD-01') });
}
// WOC11's two visible designs; legacy fixtures above keep their saved IDs.
for (const templateId of ['emo', 'show']) {
    const source = fixture.cases.find(item => item.name === `${templateId}-2-mono`).input;
    const product = structuredClone(source.product);
    const baseAnalysis = { ...structuredClone(source.analysis), targetGenders: ['women'], detailRecommendations: recommendation(3) };
    const initial = seedStoryboard(product, baseAnalysis, { projectId: 'fixture-woc11' });
    const pointKey = sectionRuns(initial).find(run => run.kind === 'points').key;
    const metadata = block => block.shot === 'detail' ? { width: 600, height: 900 } : { width: 900, height: 1200 };
    for (const designId of ['PT-PHOTO-CARD-01', 'PT-TEXT-PHOTO-01']) {
        const storyboard = changeTemplateSectionDesign(initial, pointKey, designId, { product });
        const analysis = withMockPartDescriptions(storyboard, baseAnalysis, true);
        for (const fontStyle of Object.keys(pageTemplatesCatalog.fontStyles)) for (const colorMode of ['mono', 'point']) {
            appendCase(`woc11-${templateId}-${designId}-${fontStyle}-${colorMode}`, { product, storyboard,
                analysis: { ...analysis, pageStyle: { ...analysis.pageStyle, fontStyle, colorMode } } }, metadata);
        }
        for (const [name, size] of [['portrait', { width: 600, height: 900 }], ['landscape', { width: 1600, height: 900 }], ['unknown', {}]]) {
            appendCase(`woc11-${templateId}-${designId}-${name}`, { product, analysis, storyboard }, block => block.shot === 'detail' ? size : metadata(block));
        }
        appendCase(`woc11-${templateId}-${designId}-copy-off`, { product, analysis, storyboard, copywriting: false }, metadata);
        appendCase(`woc11-${templateId}-${designId}-legacy-one-line-copy`, { product, storyboard,
            analysis: { ...analysis, partDescriptionCopy: analysis.partDescriptionCopy.map(item => ({ ...item, desc: '부위 마감을 확인할 수 있습니다.' })) } }, metadata);
        const cropAnalysis = { ...baseAnalysis, detailRecommendations: recommendation(0) };
        const crops = addTemplateSection(seedStoryboard(product, cropAnalysis), product, cropAnalysis, 'points');
        const cropKey = sectionRuns(crops).find(run => run.kind === 'points').key;
        const cropBoard = changeTemplateSectionDesign(crops, cropKey, designId, { product });
        const cropCopy = withMockPartDescriptions(cropBoard, cropAnalysis, true);
        appendCase(`woc11-${templateId}-${designId}-new-worn`, { product, analysis: cropCopy, storyboard: cropBoard }, metadata);
        const oldRegions = [[.44, .20, .30, .16], [.36, .20, .28, .17], [.30, .27, .40, .24]];
        appendCase(`woc11-${templateId}-${designId}-saved-worn`, { product, analysis: cropCopy,
            storyboard: cropBoard.map(block => block.pointItems ? { ...block,
                pointItems: block.pointItems.map((item, i) => ({ ...item, cropRegion: oldRegions[i] })) } : block) }, metadata);
    }
}
// Explicit server decisions: preserve the same generation inputs and exercise
// ordinary titles at three positions and plates at center/bottom only.
fixture.cases = fixture.cases.filter(item => !item.name.startsWith('woc14-'));
for (const designId of ['P01-D09', 'P01-D09-G']) for (const plated of [false, true]) for (const position of (plated ? ['center', 'bottom'] : ['top', 'center', 'bottom'])) {
    const input = structuredClone(fixture.cases.find(item => item.name === 'emo-2-mono').input);
    input.storyboard = input.storyboard.map(block => block.pageSectionKind === 'first' ? { ...block, pageDesignId: designId } : block);
    const hero = input.storyboard.find(block => block.pageSectionKind === 'first' && block.source !== 'text');
    const key = `${hero.pageSectionKey}:${hero.slotKey}:center`;
    input.cutResults = input.cutResults.map(cut => cut.blockId === hero.id ? { ...cut,
        overlayTextPositions: { [key]: position }, overlayTextPlates: { [key]: plated } } : cut);
    validateStoryboardV4(input.storyboard);
    fixture.cases.push({ name: `woc14-${designId}-${plated ? 'plate' : 'plain'}-${position}`, input });
}
// Seller MD copy has priority over generated copy and retains its original text.
fixture.cases = fixture.cases.filter(item => !item.name.startsWith('woc17-'));
for (const templateId of ['emo', 'show']) {
    const aiBody = 'AI 첫 문단\n\nAI 둘째 문단\nAI 셋째 문단\nAI 넷째 문단';
    for (const [suffix, mdComment, body] of [
        ['seller-multiline', '  Cotton 셔츠를 추천해요.  \n\n  둘째 줄\n셋째 줄\n넷째 줄  \n', aiBody],
        ['absent-ai', '', aiBody],
        ['absent-empty', '', ''],
        ['blank-ai', ' \t\n ', aiBody],
    ]) {
        const input = structuredClone(fixture.cases.find(item => item.name === `${templateId}-2-mono`).input);
        const intro = input.storyboard.find(block => block.pageSectionKind === 'intro');
        intro.pageDesignId = templateId === 'show' ? 'T06-D01' : 'T01-D07';
        input.analysis.mdComment = mdComment;
        input.copywriting = true;
        input.copyResults = input.copyResults.filter(copy => copy.blockId !== intro.id);
        input.copyResults.push({ blockId: intro.id, texts: [{ role: 'body', text: body }] });
        validateStoryboardV4(input.storyboard);
        fixture.cases.push({ name: `woc17-${templateId}-on-${suffix}`, input });
    }
}
for (const item of fixture.cases.filter(item => /^woc(?:[2356]|6b|11)-/.test(item.name))) validateStoryboardV4(item.input.storyboard);
// WOC20 covers both measurement states without needing image generation or a browser.
fixture.cases = fixture.cases.filter(item => !item.name.startsWith('woc20-'));
for (const templateId of ['emo', 'show']) {
    const base = () => structuredClone(fixture.cases.find(item => item.name === `${templateId}-2-mono`).input);
    const push = (suffix, input) => {
        const name = `woc20-${templateId}-${suffix}`;
        const stable = stableBlocks(input.storyboard, name);
        const ids = new Map(input.storyboard.map((card, index) => [card.id, stable[index].id]));
        input.storyboard = stable;
        input.cutResults = (input.cutResults || []).filter(cut => ids.has(cut.blockId)).map(cut => ({ ...cut, blockId: ids.get(cut.blockId) }));
        input.copyResults = (input.copyResults || []).filter(copy => ids.has(copy.blockId)).map(copy => ({ ...copy, blockId: ids.get(copy.blockId) }));
        fixture.cases.push({ name, input });
    };
    for (const count of [1, 3]) {
        const input = base();
        input.product.colors = ['pink', 'ivory', 'blue'].slice(0, count).map((swatchId, index) => ({ id: `c${index}`, name: swatchId, swatchId, isBase: index === 0 }));
        input.storyboard = seedStoryboard(input.product, input.analysis, { projectId: 'woc20' }).filter(card => card.pageSectionKind === 'color_product');
        input.cutResults = []; input.copyResults = [];
        push(`colors-${count}`, input);
    }
    for (const position of ['pending', 'overlay', 'outside']) {
        const input = base();
        input.storyboard = addTemplateSection(input.storyboard, input.product, input.analysis, 'faq')
            .filter(card => card.pageSectionKind === 'faq').map(card => ({ ...card, pageDesignId: 'T08-D07' }));
        const photo = input.storyboard.find(card => card.source !== 'text');
        input.cutResults = [{ blockId: photo.id, imageUrl: 'photo:faq', width: 1000, height: 1250,
            ...(position !== 'pending' ? { overlayTextPositions: { [`${photo.pageSectionKey}:${photo.slotKey}:heading`]: position } } : {}) }];
        input.copyResults = [{ blockId: input.storyboard[0].id, texts: [
            { role: 'items.0.q', text: '어떤 소재인가요?' }, { role: 'items.0.a', text: '면 60%, 폴리에스터 40%예요.' },
            { role: 'items.1.q', text: '어떤 색이 있나요?' }, { role: 'items.1.a', text: '로즈, 아이보리, 블루가 있어요.' },
            { role: 'items.2.q', text: '핏은 어떤 편인가요?' }, { role: 'items.2.a', text: '품이 여유 있는 세미 오버핏이에요.' } ] }];
        push(`faq-${position}`, input);
    }
    for (const lines of [0, 1, 2, 3]) {
        const input = structuredClone(fixture.cases.find(item => item.name === `woc11-${templateId}-PT-PHOTO-CARD-01-template-mono`).input);
        input.storyboard = input.storyboard.filter(card => card.pageSectionKind === 'points');
        input.analysis.partDescriptionCopy.forEach(item => { item.desc = ['', '단추로 앞을 여밀 수 있어요', '단추가 더해진 여밈으로\n차분한 옷차림을 완성해요', '단추가 더해진 여밈으로\n차분한 옷차림을 완성해요\n한 줄 더 있어요'][lines]; });
        push(`part-lines-${lines}`, input);
    }
    const wear = base();
    wear.storyboard = seedStoryboard(wear.product, wear.analysis, { projectId: 'woc20' }).filter(card => card.pageSectionKind === 'wear');
    wear.cutResults = []; wear.copyResults = [];
    push('wear-sets', wear);
    const loose = structuredClone(wear);
    loose.storyboard.forEach(card => { delete card.spaceGroupId; });
    push('wear-loose', loose);
    for (const content of ['empty', 'filled']) {
        const input = base();
        input.storyboard = addTemplateSection(input.storyboard, input.product, input.analysis, 'review')
            .filter(card => card.pageSectionKind === 'review').map(card => ({ ...card, pageDesignId: 'I09-D06' }));
        input.copyResults = content === 'empty' ? [] : [{ blockId: input.storyboard[0].id, texts: [{ role: 'items.0.text', text: '먼저 입어 본 후기예요.' }] }];
        push(`review-${content}`, input);
    }
}
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
 if value['storyboard'][0]['id'].startswith(('woc2-', 'woc3-', 'woc5-', 'woc6-', 'woc6b-', 'woc11-')): validate_storyboard_v4(value['storyboard'])
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
