import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
    assemblePage,
    collectOverlayRegions,
} from "../../src/lib/pageTemplates/assemble.js";
import {
    decorateGenBlocks,
    mergeServerBlocks,
} from "../../src/lib/editorWaitSkeleton.js";
import {
    pageTemplatesCatalog,
    validateCatalog,
} from "../../src/lib/pageTemplates/catalog.js";

const cases = JSON.parse(
    readFileSync(
        new URL("../fixtures/page_templates_cases.json", import.meta.url),
    ),
).cases;
const inputOf = (name = "emo-2-mono") =>
    structuredClone(cases.find((c) => c.name === name).input);
const KEY = "emo.first:first.hero:center";
const first = (blocks) => blocks.find((b) => b.pageSectionKey === "emo.first");
const titleOf = (value) => first(assemblePage(value));
const rulesOf = (block) => block.elements.filter((e) => e.type === "shape");

for (const color of ["#ffffff", "#111111"])
    test(`first title rules frame the actual copy and follow ${color} ink`, () => {
        const value = inputOf();
        value.cutResults[0].overlayTextColors = { [KEY]: color };
        const block = titleOf(value);
        const rules = rulesOf(block);
        assert.equal(rules.length, 2);
        const label = block.elements.find((e) => e.copyRole === "label");
        const colors = block.elements.find((e) => e.copyRole === "colors");
        assert.deepEqual(
            rules.map((e) => [e.x, e.y, e.w, e.h, e.fill]),
            [
                [309, 522, 382, 1.5, color],
                [309, 726.5, 382, 1.5, color],
            ],
        );
        assert.equal(label.y - rules[0].y - rules[0].h, 18);
        assert.equal(rules[1].y - colors.y - colors.h, 18);
        assert.ok(
            block.elements
                .filter((e) => e.copyRole)
                .every((e) => e.style.color === color),
        );
    });

for (const [position, labelY, ruleY] of [
    ["center", 541.5, 522],
    ["top", 119.5, 100],
    ["bottom", 963.5, 944],
])
    test(`saved ${position} position moves text and both rules together`, () => {
        const value = inputOf();
        value.cutResults[0].overlayTextPositions = { [KEY]: position };
        const block = titleOf(value);
        assert.equal(
            block.elements.find((e) => e.copyRole === "label").y,
            labelY,
        );
        assert.equal(rulesOf(block)[0]?.y, ruleY);
        const plan = collectOverlayRegions(value).find((p) => p.key === KEY);
        assert.equal(plan.lines[0].region.top, labelY / 1250);
        assert.deepEqual(
            plan.positionCandidates.map((p) => p.position),
            ["center", "top", "bottom"],
        );
        assert.deepEqual(
            plan.positionCandidates.map((p) => p.lines[0].region.top),
            [0.4332, 0.0956, 0.7708],
        );
    });

for (const position of [undefined, "left", null, { y: 0 }, 10])
    test(`missing/invalid position keeps center (${JSON.stringify(position)})`, () => {
        const value = inputOf();
        value.cutResults[0].overlayTextPositions = { [KEY]: position };
        assert.equal(
            titleOf(value).elements.find((e) => e.copyRole === "label").y,
            541.5,
        );
    });

test("mock and pending title start centered, and untouched generation adopts measured final placement", () => {
    const value = inputOf("emo-copy-pending");
    value.cutResults = [];
    const pending = assemblePage(value);
    const live = decorateGenBlocks(pending, {
        status: "running",
        cuts: {},
        copy: {},
    });
    const image = first(pending).elements.find((e) => e.type === "image");
    const rules = rulesOf(first(pending));
    assert.equal(
        (rules[0]?.y + rules[1]?.y + rules[1]?.h) / 2,
        image.y + image.h / 2,
    );
    const finalInput = inputOf();
    finalInput.cutResults[0].overlayTextPositions = { [KEY]: "bottom" };
    const final = assemblePage(finalInput);
    assert.equal(
        first(final).elements.find((e) => e.copyRole === "label").y,
        963.5,
    );
    assert.deepEqual(first(mergeServerBlocks(live, final)), first(final));
});

test("wrapped title keeps both rules outside copy and inside the chosen photo edge", () => {
    const value = inputOf();
    value.product.name = "매일 입는 부드러운 코튼 셔츠 ".repeat(5).trim();
    value.cutResults[0].overlayTextPositions = { [KEY]: "bottom" };
    const block = titleOf(value);
    const text = block.elements.filter((e) => e.copyRole);
    const rules = rulesOf(block);
    assert.equal(rules.length, 2);
    assert.ok(text.find((e) => e.copyRole === "name").h > 67);
    assert.equal(rules[1].y + rules[1].h, 1150);
    assert.equal(text[0].y - rules[0].y - rules[0].h, 18);
    assert.equal(rules[1].y - text.at(-1).y - text.at(-1).h, 18);
});

test("positions on other photo overlays are ignored and have no candidates", () => {
    const value = inputOf("show-2-mono");
    const before = assemblePage(value);
    for (const cut of value.cutResults)
        cut.overlayTextPositions = {
            "show.fabric:fabric.fabric:heading": "bottom",
            "show.fabric:fabric.fabric:description": "top",
        };
    assert.deepEqual(assemblePage(value), before);
    assert.ok(collectOverlayRegions(value).every((p) => !p.positionCandidates));
});

for (const blank of [" ", "\t", " \t "])
    test(`whitespace-only title rows have no measured ink (${JSON.stringify(blank)})`, () => {
        const value = inputOf();
        value.copyResults[0].texts[0].text = `Daily\n${blank}\ncotton`;
        const plan = collectOverlayRegions(value).find((p) => p.key === KEY);
        for (const candidate of plan.positionCandidates) {
            const line = candidate.lines.find((l) => l.key === `${KEY}:0:1`);
            assert.equal(line.region.left, 0.5);
            assert.equal(line.region.right, 0.5);
        }
    });

for (const policy of [
    { anchors: ["top", "center", "bottom"], edgeInsetRatio: 0.08 },
    { anchors: ["center", "top", "bottom"], edgeInsetRatio: -0.1 },
    { anchors: ["center", "top", "bottom"], edgeInsetRatio: 0.6 },
])
    test(
        "catalog rejects invalid title position policy " +
            JSON.stringify(policy),
        () => {
            const catalog = structuredClone(pageTemplatesCatalog);
            catalog.designs["P01-D09"].layout[0].groups[0].positionFallback =
                policy;
            assert.throws(() => validateCatalog(catalog), /overlay position/);
        },
    );
