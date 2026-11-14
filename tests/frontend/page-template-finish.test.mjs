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
        const input = inputOf(item.name);
        const templateId = input.storyboard[0].pageSectionKey.split('.')[0];
        const blocks = section(
            assemblePage(input),
            `${templateId}.color_product`,
        );
        const fronts = elements(blocks).filter(
            (e) => e.type === "image" && /\.front\d*$/.test(e.slotKey),
        );
        assert.deepEqual(
            fronts.map((e) => e.w),
            item.xs.map(() => item.cellW),
        );
        assert.deepEqual(
            fronts.map((e) => e.x),
            item.xs,
        );
        assert.deepEqual(
            fronts.map((e) => e.sourceBlockId),
            input.storyboard
                .filter(
                    (c) =>
                        /\.front\d*$/.test(c.slotKey || "") &&
                        c.pageSectionKind === "color_product",
                )
                .map((c) => c.id),
        );
        const last = fronts.at(-1);
        if (
            (item.name.endsWith("3-mono") && !item.name.startsWith('common-')) ||
            item.name === "common-four-color-grid"
        )
            assert.equal(last.x + last.w / 2, catalog.canvasWidth / 2);
        assert.equal(
            blocks.filter((b) => b.elements.some((e) => fronts.includes(e)))
                .length,
            Math.ceil(
                fronts.length /
                    catalog.designs[input.storyboard.find(card => card.pageSectionKind === 'color_product').pageDesignId]
                        .layout.find((n) => n.type === "photoGrid").columns,
            ),
        );
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
            ).filter((e) => e.type === "shape" && e.w === 10 && e.h === 10);
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
        const input = inputOf(`${templateId}-2-mono`);
        const rotation = inputOf(`woc20-${templateId}-wear-sets`).storyboard.find(card => card.pageSectionKey === `${templateId}.wear_fit`).spaceGroupId;
        input.storyboard.filter(card => card.pageSectionKey === `${templateId}.wear_fit`).forEach(card => { card.spaceGroupId = rotation; });
        const blocks = section(assemblePage(input), `${templateId}.wear_fit`);
        const captions = elements(blocks).filter((e) =>
            e.copyRole?.startsWith("cap."),
        );
        assert.deepEqual(
            captions.map((e) => e.text),
            templateId === 'show' ? ["FRONT", "SIDE", "BACK"] : ["Front", "Side", "Back"],
        );
        assert.deepEqual(
            captions.map((e) => e.w),
            widths,
        );
        for (const caption of captions) {
            assert.equal(caption.style.size, 22);
            assert.equal(caption.h, caption.style.lineHeight);
            const pill = elements(blocks).find(
                (e) =>
                    e.shape === "rect" &&
                    e.radius === 999 &&
                    e.x === caption.x - 18 &&
                    e.y === caption.y - 9,
            );
            assert.ok(pill);
            assert.equal(pill.w, caption.w + 36);
            assert.equal(pill.h, caption.h + 18);
            assert.equal(
                measureTextWidth(
                    caption.text,
                    1000,
                    22,
                    catalog.textMetrics,
                    caption.style.tracking,
                ),
                caption.w,
            );
        }
    });

test("R3 stacked showroom facts follow the center axis and common columns stay left aligned", () => {
    for (const templateId of ["show", "emo"]) {
        const facts = elements(
            section(
                assemblePage(inputOf(templateId === 'emo' ? 'common-2-mono' : 'show-2-mono')),
                `${templateId}.intro`,
            ),
        ).filter((e) => e.copyRole === "facts");
        assert.ok(facts.length >= 6);
        for (const fact of facts) {
            assert.equal(
                fact.style.align,
                templateId === "show" ? "center" : "left",
            );
            if (templateId === "show") assert.equal(fact.x + fact.w / 2, 500);
        }
    }
});

test("R6 showroom summary dots follow each first line and center the dot/text group", () => {
    const els = elements(
        section(assemblePage(inputOf("show-2-mono")), "show.intro"),
    );
    const summaries = els.filter((e) =>
        /^summary\.\d+$/.test(e.copyRole || ""),
    );
    assert.equal(summaries.length, 3);
    for (const text of summaries) {
        const firstLine = wrapText(
            text.text || text.placeholder,
            text.w,
            text.style.size,
            catalog.textMetrics,
            text.style.tracking,
        )[0];
        const textW = measureTextWidth(
            firstLine,
            text.w,
            text.style.size,
            catalog.textMetrics,
            text.style.tracking,
        );
        const textStart = text.x + (text.w - textW) / 2;
        const dot = els.find(
            (e) =>
                e.shape === "circle" &&
                e.w === 6 &&
                e.x === textStart - 22 &&
                e.y + 3 === text.y + text.style.lineHeight / 2,
        );
        assert.ok(dot);
        assert.equal((dot.x + textStart + textW) / 2, 500);
        assert.equal(text.style.align, "center");
    }
});
