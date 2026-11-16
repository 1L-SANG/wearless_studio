import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
    assemblePage,
    measureTextWidth,
    wrapText,
    estimateTextHeight,
} from "../../src/lib/pageTemplates/assemble.js";
import { pageTemplatesCatalog as catalog } from "../../src/lib/pageTemplates/catalog.js";
import { resolvePageTokens } from "../../src/lib/pageTemplates/tokens.js";

const fixture = JSON.parse(
    readFileSync(
        new URL("../fixtures/page_templates_cases.json", import.meta.url),
    ),
);
const quality = JSON.parse(
    readFileSync(
        new URL("../fixtures/page_templates_quality.json", import.meta.url),
    ),
);
const inputOf = (name) =>
    structuredClone(fixture.cases.find((c) => c.name === name).input);
const section = (page, key) => page.filter((b) => b.pageSectionKey === key);
const elements = (blocks) => blocks.flatMap((b) => b.elements);

for (const item of quality.metricCases)
    test(`R2 fixed uppercase/tracking measurement: ${item.name}`, () => {
        const { text, width, size, tracking, textW, lines } = item;
        assert.deepEqual(
            wrapText(text, width, size, catalog.textMetrics, tracking),
            lines,
        );
        assert.equal(
            measureTextWidth(text, width, size, catalog.textMetrics, tracking),
            textW,
        );
        assert.equal(
            estimateTextHeight(
                text,
                width,
                size,
                32,
                catalog.textMetrics,
                tracking,
            ),
            lines.length * 32,
        );
    });

for (const item of quality.gridCases)
    test(`R4 fixed grid cell widths and placement: ${item.name}`, () => {
        const input = inputOf(item.name), tid = input.storyboard[0].pageSectionKey.split('.')[0];
        const blocks = section(assemblePage(input), `${tid}.color_product`);
        const photos = elements(blocks).filter(el => el.type === 'image');
        const design = catalog.designs[input.storyboard.find(card => card.pageSectionKind === 'color_product').pageDesignId];
        if (design.choiceAxes?.layout.id === 'column') assert.ok(photos.every(el => el.x === 0 && el.w === 1000));
        else {
            const rowCount = Math.ceil(photos.length / 3), base = Math.floor(photos.length / rowCount), extra = photos.length % rowCount;
            let index = 0;
            for (let row = 0; row < rowCount; row++) {
                const count = base + (row >= rowCount - extra ? 1 : 0), width = Math.floor((888 - 24 * (count - 1)) / count);
                assert.deepEqual(photos.slice(index, index + count).map(el => [el.x, el.w]), Array.from({ length: count }, (_, n) => [56 + n * (width + 24), width]));
                index += count;
            }
        }
        assert.deepEqual(photos.map(el => el.sourceBlockId), input.storyboard.filter(card => card.pageSectionKind === 'color_product' && card.source !== 'text').map(card => card.id));
});

for (const templateId of ["emo", "show"])
    for (const colorMode of ["mono", "point"])
        test(`R1 color dots are circles; white/ivory have a 1px token border: ${templateId}/${colorMode}`, () => {
            const input = inputOf(`${templateId}-light-swatches`);
            input.pageStyle = {
                ...input.analysis.pageStyle,
                templateId,
                colorMode,
            };
            const page = assemblePage(input);
            const dots = elements(
                section(page, `${templateId}.color_product`),
            ).filter((e) => e.type === "shape" && e.w === 26 && e.h === 26);
            assert.ok(dots.length >= 3);
            const tokens = resolvePageTokens(
                input.pageStyle,
                input.product.colors[0].swatchId,
            );
            for (const dot of dots) {
                assert.equal(dot.shape, "circle");
                const light = [
                    catalog.swatchHex.white,
                    catalog.swatchHex.ivory,
                ].includes(dot.fill);
                assert.equal(dot.strokeWidth, light ? 1 : undefined);
                assert.equal(
                    dot.stroke,
                    light ? tokens.colors.accentLine : undefined,
                );
            }
            assert.ok(elements(page).every((e) => e.shape !== "ellipse"));
        });

for (const [templateId, widths] of [
    ["show", [91, 73, 73]],
    ["emo", [67, 55, 55]],
])
    test(`R2 fit pills keep FRONT/SIDE/BACK on one line with full padding: ${templateId}`, () => {
        const input = inputOf(`${templateId}-2-mono`), blocks = section(assemblePage(input), `${templateId}.wear_fit`);
        assert.equal(elements(blocks).filter(el => el.copyRole?.startsWith('cap.')).length, 0);
        assert.ok(elements(blocks).filter(el => el.type === 'image').every(el => el.x === 0 && el.w === 1000));
        assert.equal(elements(blocks).find(el => el.copyRole === 'label').text.toUpperCase(), 'LOOKBOOK');
});

test("R3 stacked showroom facts follow the center axis and common columns stay left aligned", () => {
    for (const templateId of ['show', 'emo']) {
        const facts = elements(section(assemblePage(inputOf(templateId === 'emo' ? 'common-2-mono' : 'show-2-mono')), `${templateId}.intro`)).filter(el => el.copyRole === 'facts');
        assert.equal(facts.length, 6);
        assert.ok(facts.every(el => el.style.align === 'left'));
        assert.deepEqual(facts.filter(el => el.factPart === 'label').map(el => el.x), [56, 56, 56]);
    }
});

test("R6 showroom summary dots follow each first line and center the dot/text group", () => {
    const block = section(assemblePage(inputOf('show-2-mono')), 'show.intro')[0];
    assert.equal(block.elements.filter(el => /^summary\./.test(el.copyRole || '')).length, 0);
    assert.equal(block.elements.find(el => el.copyRole === 'name').style.size, 54);
    assert.equal(block.elements.find(el => el.copyRole === 'body').style.size, 30);
    assert.equal(block.elements.filter(el => el.factPart === 'label').length, 3);
});
