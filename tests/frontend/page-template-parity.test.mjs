import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
    assemblePage,
    collectOverlayRegions,
} from "../../src/lib/pageTemplates/assemble.js";
import { pageTemplatesCatalog } from "../../src/lib/pageTemplates/catalog.js";

const fixture = JSON.parse(
    readFileSync(
        new URL("../fixtures/page_templates_cases.json", import.meta.url),
        "utf8",
    ),
);
test("shared fixture targets the current catalog", () =>
    assert.equal(fixture.catalogVersion, pageTemplatesCatalog.catalogVersion));
test('shared fixture uses two templates and preserves common design and WOC2 state coverage', () => {
    const templates = [...new Set(fixture.cases.flatMap(item => item.input.storyboard.map(block => block.pageSectionKey.split('.')[0])))].sort();
    assert.deepEqual(templates, ['emo', 'show']);
    const commonDesigns = new Set(fixture.cases.filter(item => item.name.startsWith('common-'))
        .flatMap(item => item.input.storyboard.map(block => block.pageDesignId)));
    for (const id of ['T06-D01', 'T03-GUIDE-01', 'T05-D07', 'T07-D06', 'CP-GUIDE-01']) assert.ok(commonDesigns.has(id), id);
    for (const item of fixture.cases) assert.ok(new Set(item.input.storyboard.filter(block => block.pageSectionKind === 'points')
        .map(block => block.pageSectionKey)).size <= 1, item.name);
    for (const template of ['emo', 'show']) for (const count of [0, 1, 2, 3]) {
        const item = fixture.cases.find(item => item.name === `woc2-${template}-details-${count}`);
        assert.equal(item.input.storyboard.filter(block => block.pageSectionKind === 'points' && block.source === 'ai').length, count < 2 ? 0 : count);
    }
});
for (const item of fixture.cases)
    test(`JS matches shared editor blocks: ${item.name}`, () => {
        const before = structuredClone(item.input);
        assert.deepEqual(assemblePage(item.input), item.expected);
        assert.deepEqual(
            collectOverlayRegions(item.input),
            item.expectedOverlayRegions,
        );
        assert.deepEqual(item.input, before, "assembly is pure");
    });
