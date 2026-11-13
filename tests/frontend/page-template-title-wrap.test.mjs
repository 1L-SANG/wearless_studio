import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
    assemblePage,
    collectOverlayRegions,
    measureTextWidth,
    wrapText,
} from "../../src/lib/pageTemplates/assemble.js";
import {
    pageTemplatesCatalog,
    validateCatalog,
} from "../../src/lib/pageTemplates/catalog.js";

const readFixture = (name) =>
    JSON.parse(readFileSync(new URL(`../fixtures/${name}`, import.meta.url)));
const base = readFixture("page_templates_cases.json").cases.find(
    (item) => item.name === "emo-2-mono",
).input;
const cases = readFixture("page_templates_title_wrap.json").cases;
const KEY = "emo.first:first.hero:center";

for (const item of cases)
    for (const position of ["center", "top", "bottom"])
        test(`WOQ6 ${item.name}: ${position} uses balanced copy, size and matching rules`, () => {
            const input = structuredClone(base);
            input.product.name = item.title;
            input.cutResults[0].overlayTextPositions = { [KEY]: position };
            const block = assemblePage(input).find(
                (b) => b.pageSectionKey === "emo.first",
            );
            const title = block.elements.find((e) => e.copyRole === "name");
            const rules = block.elements.filter((e) => e.type === "shape");
            const texts = block.elements.filter((e) => e.type === "text");
            assert.equal(title.text, item.lines.join("\n"));
            assert.equal(title.style.size, item.size);
            assert.equal(title.keepWords, true);
            assert.equal(title.w, 700);
            assert.equal(title.x, 150);
            assert.equal(
                title.h,
                item.lines.length * Math.ceil(item.size * 1.3),
            );
            assert.deepEqual(
                wrapText(
                    title.text,
                    title.w,
                    item.size,
                    undefined,
                    title.style.tracking,
                ),
                item.lines,
            );
            assert.deepEqual(
                rules.map((r) => [r.x, r.w, r.h]),
                Array(2).fill([
                    (1000 - item.ruleWidth) / 2,
                    item.ruleWidth,
                    1.5,
                ]),
            );
            assert.equal(texts[0].y - rules[0].y - rules[0].h, 18);
            assert.equal(rules[1].y - texts.at(-1).y - texts.at(-1).h, 18);
            const top = rules[0].y,
                bottom = rules[1].y + rules[1].h;
            if (position === "center") assert.equal((top + bottom) / 2, 625);
            if (position === "top") assert.equal(top, 100);
            if (position === "bottom") assert.equal(bottom, 1150);
            const plan = collectOverlayRegions(input).find(
                (p) => p.key === KEY,
            );
            const lines = plan.lines.filter((l) =>
                l.key.startsWith(`${KEY}:1:`),
            );
            assert.equal(lines.length, item.lines.length);
            lines.forEach((line, i) => {
                assert.equal(
                    line.region.top,
                    (title.y + i * title.style.lineHeight) / 1250,
                );
                assert.ok(
                    Math.abs(
                        (line.region.right - line.region.left) * 1000 -
                            Math.ceil(item.lineWidths[i]),
                    ) < 1e-9,
                );
            });
            assert.equal(
                title.text.replace(/\s/gu, ""),
                item.title.replace(/\s/gu, ""),
            );
            assert.equal(input.product.name, item.title);
        });

test("WOQ6 name alone sets rule width even when label, tagline and colors are wider", () => {
    const input = structuredClone(base);
    input.product.name = "I";
    for (const text of input.copyResults[0].texts)
        if (text.copyRole !== "name") text.text = "A considerably longer line";
    const block = assemblePage(input).find(
        (b) => b.pageSectionKey === "emo.first",
    );
    assert.deepEqual(
        block.elements.filter((e) => e.type === "shape").map((e) => e.w),
        [48, 48],
    );
});

test("WOQ6 rule policy and font steps live in the shared catalog", () => {
    const nodes =
        pageTemplatesCatalog.designs["P01-D09"].layout[0].groups[0].nodes;
    const title = nodes.find((n) => n.copyRole === "name");
    assert.equal(title.fitWidth, undefined);
    assert.deepEqual(title.titleWrap, {
        photoWidthRatio: 0.7,
        sizes: [51, 44, 38],
        maxLines: 2,
        minSizeMaxLines: 3,
    });
    assert.ok(
        nodes
            .filter((n) => n.type === "rule")
            .every(
                (n) =>
                    n.matchText === "name" &&
                    n.minW === 48 &&
                    n.maxWidthRatio === 0.6,
            ),
    );
});

for (const policy of [
    { photoWidthRatio: 0 },
    { sizes: [51, 38, 44] },
    { sizes: [] },
    { maxLines: 0 },
    { minSizeMaxLines: 1 },
])
    test(`WOQ6 rejects invalid title wrapping ${JSON.stringify(policy)}`, () => {
        const catalog = structuredClone(pageTemplatesCatalog);
        const title = catalog.designs["P01-D09"].layout[0].groups[0].nodes.find(
            (n) => n.copyRole === "name",
        );
        title.titleWrap = { ...title.titleWrap, ...policy };
        assert.throws(() => validateCatalog(catalog), /title wrap/);
    });

test("WOQ6 longest-line width includes uppercase and final-character tracking", () => {
    const item = cases.find((c) => c.name === "uppercase-ascii");
    assert.equal(
        measureTextWidth(
            item.lines.join("\n"),
            700,
            item.size,
            undefined,
            -1.02,
        ),
        item.ruleWidth,
    );
});

for (const name of [
    "가".repeat(19),
    Array(7).fill("가나다라마바사아자차").join(" "),
])
    test(`WOQ6 infeasible copy retains all words at 38px (${name.length} characters)`, () => {
        const input = structuredClone(base);
        input.product.name = name;
        const title = assemblePage(input)
            .flatMap((b) => b.elements)
            .find((e) => e.copyRole === "name");
        assert.equal(title.style.size, 38);
        assert.equal(title.keepWords, true);
        assert.deepEqual(title.text.split("\n"), name.split(" "));
        assert.equal(title.text.replace(/\s/gu, ""), name.replace(/\s/gu, ""));
    });
