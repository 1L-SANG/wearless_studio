import {
    beginEditorCrop,
    editorCropPatch,
} from "../../src/features/editor/editorCrop.js";
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import {
    assemblePage,
    estimateTextHeight,
} from "../../src/lib/pageTemplates/assemble.js";
import { pageTemplatesCatalog as catalog } from "../../src/lib/pageTemplates/catalog.js";
import { normalizeEditorBlockRole } from "../../src/lib/storyboardTaxonomy.js";
import {
    alignSkeletonToServer,
    decorateGenBlocks,
} from "../../src/lib/editorWaitSkeleton.js";

const cases = JSON.parse(
    readFileSync(
        new URL("../fixtures/page_templates_cases.json", import.meta.url),
        "utf8",
    ),
).cases;
const caseOf = (name) =>
    structuredClone(cases.find((item) => item.name === name).input);
const section = (blocks, key) =>
    blocks.filter((block) => block.pageSectionKey === key);
const elements = (blocks) => blocks.flatMap((block) => block.elements);
const photos = (blocks) =>
    elements(blocks).filter((element) => element.type === "image");
const text = (blocks, role) =>
    elements(blocks).filter((element) => element.copyRole === role);
const runs = (list) =>
    list.filter((key, index) => !index || key !== list[index - 1]);
const matrix = cases.filter((item) =>
    /^(emo|show)-[123]-(mono|point)$/.test(item.name),
);

for (const item of matrix)
    test(`assembly geometry and generation coverage: ${item.name}`, () => {
        const { input } = item,
            blocks = assemblePage(input);
        const [templateId, count] = item.name.split("-");
        const expectedCounts = {
            emo: [17, 18, 21],
            show: [15, 16, 19],
        };
        const generated = input.storyboard.filter(
            (card) => card.source === "ai",
        );
        assert.equal(
            generated.length,
            expectedCounts[templateId][Number(count) - 1],
        );
        assert.deepEqual(
            runs(
                blocks
                    .filter((block) => block.pageSectionKey)
                    .map((block) => block.pageSectionKey),
            ),
            runs(input.storyboard.map((card) => card.pageSectionKey)),
        );
        const sourceIds = new Set(
            photos(blocks).map((photo) => photo.sourceBlockId),
        );
        generated.forEach((card) =>
            assert.ok(sourceIds.has(card.id), `missing ${card.id}`),
        );
        for (const block of blocks) {
            assert.equal(block.templateId, undefined);
            if (block.pageSectionKey)
                assert.ok(block.h <= catalog.textMetrics.maxBlockHeight);
            for (const element of block.elements) {
                assert.ok(
                    Number.isFinite(element.x) && Number.isFinite(element.y),
                );
                assert.ok(
                    element.x >= 0 &&
                        element.x + element.w <= catalog.canvasWidth,
                );
                assert.ok(element.w > 0 && element.h > 0 && element.y >= 0);
                if (block.h) assert.ok(element.y + element.h <= block.h);
                if (block.pageSectionKey && element.type === "image")
                    assert.ok(
                        element.slotKey &&
                            element.roleHint &&
                            element.sourceBlockId,
                    );
                if (block.pageSectionKey && element.copyRole)
                    assert.equal(
                        element.sourceBlockId,
                        input.storyboard.find(
                            (card) =>
                                card.pageSectionKey === block.pageSectionKey,
                        ).id,
                    );
            }
        }
        assert.deepEqual(
            blocks.slice(-4).map((block) => block.kind),
            ["size", "care", "ai-notice", "common-notice"],
        );
        assert.equal(
            blocks.at(-1).elements[0].text,
            "모니터와 기기 설정에 따라 실제 색상과 다르게 보일 수 있어요.",
        );
        const styling = section(blocks, `${templateId}.wear_styling`);
        const names = elements(styling).filter((el) =>
            el.copyRole?.startsWith("colorName."),
        );
        assert.equal(names.length, (Number(count) - 1) * 2);
        assert.ok(names.every((el) => el.text !== "라이트블루"));
    });

test("first-page text is centered as one group and uses its measured black/white color", () => {
    const input = caseOf("emo-2-point");
    const key = "emo.first:first.hero:center";
    for (const measured of [
        catalog.overlayColors.light,
        catalog.overlayColors.dark,
        "invalid",
        null,
    ]) {
        input.cutResults[0].overlayTextColors = { [key]: measured };
        const first = section(assemblePage(input), "emo.first");
        const image = photos(first)[0];
        const copy = elements(first).filter((el) => el.copyRole);
        assert.equal(copy.length, 3);
        const top = Math.min(...copy.map((el) => el.y));
        const bottom = Math.max(...copy.map((el) => el.y + el.h));
        assert.equal((top + bottom) / 2, image.y + image.h / 2);
        for (const el of copy)
            assert.equal(
                el.style.color,
                [
                    catalog.overlayColors.light,
                    catalog.overlayColors.dark,
                ].includes(measured)
                    ? measured
                    : catalog.overlayColors.light,
            );
    }
});

test("static/fact slots and seller MD text survive pending or disabled AI copy", () => {
    const pendingInput = caseOf("emo-copy-pending"), pending = assemblePage(pendingInput);
    assert.equal(
        text(section(pending, "emo.first"), "name")[0].text,
        "데일리 코튼 셔츠",
    );
    assert.equal(text(section(pending, "emo.first"), "tagline").length, 0);
    assert.equal(text(section(pending, "emo.intro"), "body")[0].text, pendingInput.analysis.mdComment);
    const offInput = caseOf("emo-copy-off-seller"),
        off = assemblePage(offInput);
    assert.equal(
        text(section(off, "emo.intro"), "body")[0].text,
        offInput.analysis.mdComment,
    );
    assert.equal(
        text(section(off, "emo.for_you"), "title")[0].text,
        "이런 분께 추천해요",
    );
    assert.ok(
        elements(section(off, "emo.review"))
            .filter((el) => el.copyRole?.startsWith("items."))
            .every((el) => el.text === "" && el.placeholder),
    );
    const input = caseOf("emo-2-mono");
    input.copyResults[0].texts.push({ role: "name", text: "지어낸 상품명" });
    const blocks = assemblePage(input);
    assert.equal(
        text(section(blocks, "emo.first"), "name")[0].text,
        input.product.name,
    );
    assert.equal(
        text(section(blocks, "emo.points"), "items.0.desc")[0].text,
        input.analysis.featureCopy[0].desc,
    );
});

test("fabric and emotional points fall back to text designs without fabric/point photos", () => {
    const blocks = assemblePage(caseOf("emo-text-fallbacks"));
    for (const [key, designId] of [
        ["emo.fabric", "T03-TEXT-01"],
        ["emo.points", "PT-EMO-TEXT"],
    ]) {
        const group = section(blocks, key);
        assert.equal(group.length, 1);
        assert.equal(group[0].pageDesignId, designId);
        assert.equal(photos(group).length, 0);
    }
    assert.ok(text(section(blocks, "emo.intro"), "body")[0].aiDraft);
    assert.equal(
        elements(blocks).filter((el) => el.copyRole === "notice").length,
        0,
    );
});

test("showroom ghost shares its source ID, failure metadata and empty orphan semantics", () => {
    const input = caseOf("show-reuse-failed"),
        blocks = assemblePage(input);
    const ghost = photos(section(blocks, "show.first")).find(
        (el) => el.slotKey === "first.ghost",
    );
    const original = photos(section(blocks, "show.color_product")).find(
        (el) => el.sourceBlockId === ghost.sourceBlockId,
    );
    assert.ok(original);
    for (const photo of [ghost, original]) {
        assert.equal(photo.src, null);
        assert.equal(photo.genFailed, true);
        assert.equal(photo.genFailReason, "temporary_error");
        assert.equal(photo.genFailRetryable, true);
        assert.equal(photo.genFailSourceBlockId, "retry-source");
    }
    const orphan = photos(
        section(assemblePage(caseOf("show-reuse-orphan")), "show.first"),
    ).find((el) => el.slotKey === "first.ghost");
    assert.equal(orphan.sourceBlockId, undefined);
    assert.equal(orphan.src, null);
    assert.equal(orphan.roleHint, "productOverview");
});

test("SNS reuses the first styling images and story uses the first two distinct copy roles", () => {
    for (const name of ["emo-sns-message", "emo-sns-story"]) {
        const input = caseOf(name),
            blocks = assemblePage(input);
        const styling = input.storyboard.filter(
            (card) => card.pageSectionKey === "emo.wear_styling",
        );
        const images = photos(section(blocks, "emo.sns"));
        assert.equal(images[0].sourceBlockId, styling[0].id);
        assert.equal(images[0].slotKey, "sns.sns1");
        if (name.endsWith("story")) {
            assert.equal(images[1].sourceBlockId, styling[1].id);
            assert.equal(images[1].slotKey, "sns.sns2");
            assert.equal(text(section(blocks, "emo.sns"), "lines.0").length, 1);
            assert.equal(text(section(blocks, "emo.sns"), "lines.1").length, 1);
        }
    }
});

test("extra photos and similar cuts append in their own section, with no dropped or duplicated cards", () => {
    const input = caseOf("emo-overflow"),
        blocks = assemblePage(input);
    for (const id of ["overflow-hero", "gallery-extra"])
        assert.equal(
            photos(blocks).filter((el) => el.sourceBlockId === id).length,
            1,
        );
    const first = section(blocks, "emo.first");
    assert.equal(first.length, 2);
    assert.equal(photos([first[1]])[0].sourceBlockId, "overflow-hero");
    assert.equal(first[1].elements.filter((el) => el.copyRole).length, 0);
});

test("showroom inversion remains monochrome; point color appears only in permitted surfaces and titles", () => {
    const mono = assemblePage(caseOf("show-2-mono")),
        point = assemblePage(caseOf("show-2-point"));
    for (const key of ["show.intro", "show.fabric", "show.points"])
        assert.deepEqual(section(point, key), section(mono, key));
    const common = assemblePage(caseOf("common-2-point"));
    assert.ok(
        elements(section(common, "emo.intro")).some(
            (el) => el.fill === catalog.pointPalette.blue.accentBg,
        ),
    );
    const colorSection = section(
        assemblePage(caseOf("show-2-mono")),
        "show.color_product",
    );
    assert.deepEqual(text(colorSection, "colors.0.en"), []);
    assert.equal(text(colorSection, "label")[0].text, "COLOR");
});

test("base color may be after another color, and unknown color IDs do not acquire a different color name", () => {
    const input = caseOf("emo-2-point");
    input.product.colors.reverse();
    const blocks = assemblePage(input);
    assert.equal(
        text(section(blocks, "emo.for_you"), "title")[0].style.color,
        catalog.pointPalette.blue.accentInk,
    );
    input.storyboard.find(
        (card) => card.pageSectionKey === "emo.color_product",
    ).colorId = "missing";
    assert.equal(
        text(
            section(assemblePage(input), "emo.color_product"),
            "colors.0.name",
        )[0].text,
        "",
    );
});

test("text sizing is deterministic for Korean words, explicit newlines and emoji", () => {
    assert.equal(estimateTextHeight("가나다 라마바", 100, 25, 36), 72);
    assert.equal(estimateTextHeight("가나다\n라마바", 100, 25, 36), 72);
    assert.equal(estimateTextHeight("🙂🙂", 100, 25, 36), 36);
});

test("editor role normalization and skeleton alignment preserve template geometry and names", () => {
    const blocks = assemblePage(caseOf("common-3-point"));
    for (const block of blocks.filter((block) => block.pageSectionKey)) {
        assert.equal(normalizeEditorBlockRole(block), block);
        assert.equal(alignSkeletonToServer([block], true)[0], block);
    }
    const own = {
        ...blocks[0],
        elements: [
            {
                type: "image",
                sourceBlockId: "own",
                slotKey: "first.hero",
                roleHint: "custom",
                src: "/mine.png",
            },
        ],
    };
    const job = { cuts: {}, copy: {}, live: [], failedCuts: [] };
    assert.equal(decorateGenBlocks([own], job)[0].elements[0].src, "/mine.png");
});

test("mock v4 path and generation skeleton use the catalog and selected analysis style", async () => {
    const vite = await createServer({
        configFile: false,
        optimizeDeps: { noDiscovery: true, include: [] },
        resolve: {
            alias: {
                "@": fileURLToPath(new URL("../../src", import.meta.url)),
            },
        },
        server: { middlewareMode: true, hmr: false, ws: false },
        appType: "custom",
    });
    try {
        const { buildEditorBlocksFromStoryboard } =
            await vite.ssrLoadModule("/src/mock/db.js");
        const input = caseOf("common-2-point");
        const actual = buildEditorBlocksFromStoryboard(
            input.storyboard,
            input.product,
            false,
            input.analysis,
        );
        assert.deepEqual(
            runs(
                actual
                    .filter((block) => block.pageSectionKey)
                    .map((block) => block.pageSectionKey),
            ),
            runs(input.storyboard.map((card) => card.pageSectionKey)),
        );
        assert.equal(
            text(section(actual, "emo.intro"), "title")[0].style.color,
            catalog.pointPalette.blue.accentInk,
        );
        assert.ok(actual.every((block) => !block.templateId));
    } finally {
        await vite.close();
    }
});

test("adaptive photo groups resolve independently while scrim headings and bubbles keep their fixed colors", () => {
    const input = caseOf("show-2-point");
    const cut = input.cutResults.find(
        (cut) => cut.blockId === "show.fabric.fabric",
    );
    cut.overlayTextColors = {
        "show.fabric:fabric.fabric:heading": catalog.overlayColors.dark,
        "show.fabric:fabric.fabric:description": catalog.overlayColors.light,
    };
    const fabric = section(assemblePage(input), "show.fabric");
    for (const role of ["label", "title"])
        assert.equal(
            text(fabric, role)[0].style.color,
            catalog.overlayColors.dark,
        );
    for (const role of ["mix", "body"])
        assert.equal(
            text(fabric, role)[0].style.color,
            catalog.overlayColors.light,
        );
    for (const value of cases.filter((item) =>
        ["emo-2-point", "emo-sns-story"].includes(item.name),
    )) {
        const planned = structuredClone(value.input);
        for (const cut of planned.cutResults)
            cut.overlayTextColors = Object.fromEntries(
                Object.keys(cut.overlayTextColors || {}).map((key) => [
                    key,
                    catalog.overlayColors.dark,
                ]),
            );
        const blocks = assemblePage(planned);
        const review = section(blocks, "emo.review");
        assert.equal(
            text(review, "label")[0].style.color,
            catalog.overlayColors.light,
        );
        assert.equal(
            text(review, "items.0.text")[0].style.color,
            catalog.templates[0].colors.ink,
        );
        const faq = section(blocks, "emo.faq");
        assert.equal(
            text(faq, "label")[0].style.color,
            catalog.overlayColors.light,
        );
        assert.equal(
            text(faq, "items.0.q")[0].style.color,
            catalog.templates[0].colors.ink,
        );
        if (value.name.endsWith("story"))
            for (const account of text(section(blocks, "emo.sns"), "handle"))
                assert.equal(account.style.color, catalog.overlayColors.dark);
        assert.ok(
            elements(blocks).every(
                (el) => !Object.hasOwn(el, "_overlayGroupKey"),
            ),
        );
    }
});

test("medium fit album crops a quarter of the source using the editor's saved crop format", () => {
    const input = caseOf("common-3-point");
    const blocks = assemblePage(input);
    const album = photos(section(blocks, "emo.points_fit"));
    assert.equal(album.length, 3);
    for (const photo of album) {
        const source = input.cutResults.find(
            (cut) => cut.blockId === photo.sourceBlockId,
        );
        assert.deepEqual(Object.keys(photo.crop).sort(), [
            "ih",
            "iw",
            "ox",
            "oy",
        ]);
        assert.ok(photo.crop.oy / photo.crop.ih >= 0.249);
        assert.ok(
            photo.crop.oy / photo.crop.ih > 0.2,
            "the upper fifth containing the head is outside the frame",
        );
        assert.ok(
            Math.abs(photo.crop.ox - (photo.crop.iw - photo.w) / 2) <= 0.5,
        );
        assert.ok(photo.crop.oy + photo.h <= photo.crop.ih);
        const opened = beginEditorCrop(
            blocks.find((block) => block.elements.includes(photo)).id,
            photo,
            { width: source.width, height: source.height },
        );
        assert.deepEqual(editorCropPatch(opened).crop, photo.crop);
    }
    const editor = readFileSync(
        new URL("../../src/features/editor/Editor.jsx", import.meta.url),
        "utf8",
    );
    assert.match(
        editor,
        /left: -el\.crop\.ox, top: -el\.crop\.oy, width: el\.crop\.iw, height: el\.crop\.ih/,
    );
});

test("the summary separator is centered in showroom and the common design stays left aligned", () => {
    const show = section(assemblePage(caseOf("show-2-mono")), "show.intro");
    const line = elements(show).find((el) => el.type === "shape" && el.h === 2);
    assert.equal(line.x + line.w / 2, 500);
    const common = section(assemblePage(caseOf("common-2-mono")), "emo.intro");
    const lines = elements(common).filter(
        (el) => el.type === "shape" && el.h === 2,
    );
    assert.deepEqual(
        lines.map((el) => el.x),
        [56],
    );
});
