import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import {
    assemblePage,
    measureTextWidth,
    koreanCount,
    collectOverlayRegions,
} from "../../src/lib/pageTemplates/assemble.js";
import {
    pageTemplatesCatalog as catalog,
    validateCatalog,
    walkLayout,
} from "../../src/lib/pageTemplates/catalog.js";
import {
    decorateGenBlocks,
    fillGenBlocks,
    mergeServerBlocks,
} from "../../src/lib/editorWaitSkeleton.js";
import {
    defaultStitchedMode,
    readStitchedPreference,
    writeStitchedPreference,
    canvasBlockRuns,
} from "../../src/features/editor/editorViewMode.js";
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
const section = (blocks, key) => blocks.filter((b) => b.pageSectionKey === key);
const elements = (blocks) => blocks.flatMap((b) => b.elements);
const texts = (blocks) => elements(blocks).filter((e) => e.type === "text");
function withLayout(layout, run) {
    const design = catalog.designs["T08-D01"];
    catalog.designs["T08-D01"] = {
        ...design,
        padding: 0,
        top: 0,
        bottom: 0,
        layout,
    };
    const input = inputOf("emo-2-mono");
    input.storyboard = input.storyboard.filter(
        (c) => c.pageSectionKind === "for_you",
    );
    try {
        return run(input);
    } finally {
        catalog.designs["T08-D01"] = design;
    }
}
const hugNode = (item) => ({
    type: "panel",
    hug: true,
    minW: 80,
    maxW: item.maxW,
    padding: [14, 22],
    fill: "ink",
    nodes: [
        {
            type: "text",
            literal: item.text,
            style: item.style || "body",
            size: item.fontSize || 20,
            color: "invertInk",
        },
    ],
});
for (const item of quality.textCases)
    test(`C2/C3 fixed text and box width: ${item.name}`, () => {
        withLayout([hugNode(item)], (input) => {
            const result = section(assemblePage(input), "emo.for_you");
            const shape = elements(result).find((e) => e.type === "shape");
            const text = texts(result)[0];
            assert.equal(shape.w, item.boxW);
            assert.equal(text.style.size, item.size || item.fontSize || 20);
            assert.equal(text.style.tracking, item.tracking || 0);
            assert.equal(
                measureTextWidth(
                    text.text,
                    text.w,
                    text.style.size,
                    catalog.textMetrics,
                    text.style.tracking,
                ),
                item.textW,
            );
            assert.equal(text.x, 22);
            assert.equal(text.y, 14);
            assert.equal(text.w, shape.w - 44);
            assert.equal(shape.h, text.h + 28);
        });
    });
test("C2/R2 fixed widths and complete tracked panels agree exactly with Python", () => {
    const input = inputOf("emo-2-mono");
    input.storyboard = input.storyboard.filter(
        (c) => c.pageSectionKind === "for_you",
    );
    const py = spawnSync(
        "server/.venv/bin/python",
        [
            "-c",
            `import json,sys
sys.path[:0]=['server','server/tests']
from app.agents.page_templates import CATALOG
from page_template_cases import assemble_input
v=json.load(sys.stdin);out=[]
for layout in v['layouts']:
 CATALOG['designs']['T08-D01'].update(padding=0,top=0,bottom=0,layout=layout)
 out.append(assemble_input(v['input']))
print(json.dumps(out,ensure_ascii=False))`,
        ],
        {
            input: JSON.stringify({
                input,
                layouts: quality.textCases.map((c) => [hugNode(c)]),
            }),
            encoding: "utf8",
            maxBuffer: 4e6,
        },
    );
    assert.equal(py.status, 0, py.stderr);
    const outputs = JSON.parse(py.stdout);
    quality.textCases.forEach((c, i) =>
        withLayout([hugNode(c)], (input) =>
            assert.deepEqual(assemblePage(input), outputs[i]),
        ),
    );
});
test("C2 lists support each/max widths and left/center/right placement", () => {
    for (const hugMode of ["each", "max"])
        withLayout(
            [
                {
                    type: "list",
                    copyRole: "items.n",
                    count: 2,
                    hug: true,
                    hugMode,
                    minW: 80,
                    maxW: 300,
                    align: "center",
                    padding: [14, 22],
                    fill: "ink",
                    gap: 12,
                    items: [{ type: "text", copyRole: "items.n", size: 20 }],
                },
            ],
            (input) => {
                input.copyResults = [
                    {
                        blockId: input.storyboard[0].id,
                        texts: [
                            { role: "items.0", text: "가나다" },
                            { role: "items.1", text: "가나다 라마바" },
                        ],
                    },
                ];
                const shapes = elements(
                    section(assemblePage(input), "emo.for_you"),
                ).filter((e) => e.type === "shape");
                assert.deepEqual(
                    shapes.map((e) => e.w),
                    hugMode === "max" ? [171, 171] : [104, 171],
                );
                assert.ok(shapes.every((e) => e.x + e.w / 2 === 500));
            },
        );
    for (const align of ["left", "center", "right"])
        withLayout([{ ...hugNode(quality.textCases[0]), align }], (input) => {
            const shape = elements(
                section(assemblePage(input), "emo.for_you"),
            )[0];
            assert.equal(
                shape.x,
                align === "left" ? 0 : align === "center" ? 448 : 896,
            );
        });
});
test("C3 catalog validates separate padding and rejects malformed geometry in both interpreters", () => {
    for (const [field, value] of [
        ["padding", [1]],
        ["padding", [1, -2]],
        ["padding", [1, "2"]],
        ["hugMode", "all"],
        ["minW", -1],
        ["photoRadius", -1],
        ["layout", "grid"],
    ]) {
        const data = structuredClone(catalog);
        data.designs["T08-D01"].layout.at(-1)[field] = value;
        assert.throws(() => validateCatalog(data));
    }
    assert.equal(validateCatalog(catalog), catalog);
});
test("C4 section gaps are locked canvas rectangles without adding blocks", () => {
    const page = assemblePage(inputOf("show-2-mono"));
    for (const key of ["show.fabric", "show.points"]) {
        const blocks = section(page, key),
            last = blocks.at(-1),
            gap = last.elements.at(-1);
        assert.equal(
            last.bg,
            catalog.templates.find((t) => t.id === "show").colors.invertBg,
        );
        assert.equal(gap.pageSectionGap, true);
        assert.equal(gap.locked, true);
        assert.equal(gap.fill, catalog.canvasColor);
        assert.equal(gap.y + gap.h, last.h);
        assert.equal(gap.w, 1000);
        assert.equal(
            blocks.length,
            new Set(
                elements(blocks)
                    .filter((e) => e.type === "image")
                    .map((e) => e.sourceBlockId),
            ).size,
        );
    }
});
test("C5 photoStack/photoGrid/pointRows apply their trailing gap once", () => {
    for (const [name, id, type, role] of [
        ["common-fit-album-location-medium", "T07-D06", "photoStack", "fit.tip"],
        ["emo-2-mono", "PT-EMO-01", "pointRows", "notice"],
        ["emo-2-mono", "CP-EMO-01", "photoGrid", "cap.back"],
    ]) {
        const design = catalog.designs[id],
            node = design.layout.find((n) => n.type === type),
            oldGap = node.gap,
            oldColumns = node.columns;
        if (type === "photoGrid") node.columns = 1; // Keep cell geometry fixed while isolating trailing spacing.
        // A real following node, also when the original last text is hidden.
        const tail = {
            type: "text",
            literal: "after repeat",
            style: "body",
            size: 20,
        };
        const i = design.layout.indexOf(node);
        design.layout.splice(i + 1, 0, tail);
        try {
            node.gap = 0;
            const a = assemblePage(inputOf(name));
            node.gap = 40;
            const b = assemblePage(inputOf(name));
            const find = (page) =>
                texts(page).find((e) => e.text === "after repeat");
            assert.equal(find(b).y - find(a).y, 40);
            assert.equal(a.length, b.length);
        } finally {
            node.gap = oldGap;
            if (oldColumns != null) node.columns = oldColumns;
            design.layout.splice(i + 1, 1);
        }
    }
    const tip = catalog.designs["T07-D06"].layout.find(
        (n) => n.type === "panel" && n.fill === "panel",
    );
    assert.equal(tip.padding, 26);
});
test("C7 inline/columns/stacked tables show confirmed facts and no invented SIZE", () => {
    const design = catalog.designs["T06-D01"];
    let table;
    walkLayout(design.layout, (n) => {
        if (n.type === "factTable" && n.layout === "columns") table = n;
    });
    const old = table.layout;
    try {
        for (const layout of ["inline", "columns", "stacked"]) {
            table.layout = layout;
            const input = inputOf("common-2-mono");
            input.product.size = "M";
            const rows = texts(
                section(assemblePage(input), "emo.intro"),
            ).filter((e) => e.copyRole === "facts");
            assert.ok(rows.length);
            assert.ok(rows.every((e) => !e.text.includes("SIZE")));
            if (layout === "inline") assert.equal(rows.length, 1);
            else {
                const label = rows.find(
                        (e) =>
                            e.factLabel === "COLOR" && e.factPart === "label",
                    ),
                    value = rows.find(
                        (e) =>
                            e.factLabel === "COLOR" && e.factPart === "value",
                    );
                assert.equal(label.style.size, 18);
                assert.equal(label.style.align, layout === 'columns' ? 'left' : 'center');
                if (layout === "columns") {
                    assert.equal(value.x - label.x, 150);
                    assert.equal(value.y, label.y);
                } else {
                    assert.equal(value.x, label.x);
                    assert.equal(value.y, label.y + label.h + 4);
                }
            }
        }
    } finally {
        table.layout = old;
    }
});
test("C8 list dots/squares are 6px shapes at the first-line center with a 22px indent", () => {
    for (const marker of ["dot", "square"])
        withLayout(
            [
                {
                    type: "list",
                    copyRole: "items.n",
                    count: 1,
                    marker,
                    items: [
                        {
                            type: "text",
                            literal: "첫 줄\n둘째 줄",
                            size: 20,
                            align: "left",
                        },
                    ],
                },
            ],
            (input) => {
                const els = elements(
                        section(assemblePage(input), "emo.for_you"),
                    ),
                    dot = els.find((e) => e.type === "shape"),
                    text = els.find((e) => e.type === "text");
                assert.equal(dot.shape, marker === "dot" ? "circle" : "rect");
                assert.equal(dot.w, 6);
                assert.equal(dot.h, 6);
                assert.equal(text.x - dot.x, 22);
                assert.equal(dot.y + 3, text.y + text.style.lineHeight / 2);
                assert.equal(text.text.includes("•"), false);
            },
        );
});
test("C9/R5 point-row copy stays centered with rounded and square common photo designs", () => {
    for (const name of ["emo-2-point", "common-2-point"]) {
        const page = assemblePage(inputOf(name)),
            key = name.startsWith("emo") ? "emo.points" : "emo.points_detail";
        const block = section(page, key).at(-1),
            photo = block.elements.find((e) => e.type === "image"),
            copy = block.elements.filter(
                (e) =>
                    e.type === "text" &&
                    (/items\./.test(e.copyRole || "") ||
                        /^\d{2}$/.test(e.text)),
            );
        assert.equal(photo.radius, name.startsWith("emo") ? 16 : 0);
        const top = Math.min(...copy.map((e) => e.y)),
            bottom = Math.max(...copy.map((e) => e.y + e.h));
        assert.equal((top + bottom) / 2, photo.y + photo.h / 2);
    }
});
test("C10 color title uses native Korean counters through ten", () => {
    assert.deepEqual(
        Array.from({ length: 12 }, (_, i) => koreanCount(i + 1)),
        [
            "한",
            "두",
            "세",
            "네",
            "다섯",
            "여섯",
            "일곱",
            "여덟",
            "아홉",
            "열",
            "11",
            "12",
        ],
    );
    assert.equal(
        texts(
            section(assemblePage(inputOf("emo-2-mono")), "emo.color_product"),
        ).find((e) => e.copyRole === "title").text,
        "두 가지 색이에요",
    );
});
test("C11 AI paragraphs without seller MD get at most three individually sized bubbles without losing copy", () => {
    const page = assemblePage(inputOf("emo-md-paragraphs")),
        intro = section(page, "emo.intro");
    const body = texts(intro).filter((e) => e.copyRole === "body"),
        bubbles = elements(intro).filter(
            (e) => e.type === "shape" && e.radius === 26,
        );
    assert.equal(body.length, 3);
    assert.equal(bubbles.length, 3);
    assert.ok(body[2].text.includes("\n오래 입을"));
    assert.deepEqual(
        body.map((e) => e.paragraphIndex),
        [0, 1, 2],
    );
    assert.equal(bubbles[1].y - (bubbles[0].y + bubbles[0].h), 12);
    assert.equal(texts(intro).filter((e) => e.copyRole === "title").length, 1);
    assert.ok(new Set(bubbles.map((e) => e.w)).size > 1);
});
test("C12 same-surface panels remove only horizontal padding and retain opaque photo bubbles", () => {
    for (const fill of ["canvas", "ink"])
        withLayout(
            [
                {
                    type: "panel",
                    fill,
                    padding: [14, 22],
                    nodes: [{ type: "text", literal: "설명", size: 20 }],
                },
            ],
            (input) => {
                const els = elements(
                        section(assemblePage(input), "emo.for_you"),
                    ),
                    text = els.find((e) => e.type === "text");
                assert.equal(text.y, 14);
                assert.equal(text.x, fill === "canvas" ? 0 : 22);
                assert.equal(
                    els.some((e) => e.type === "shape"),
                    fill !== "canvas",
                );
            },
        );
    const reviews = elements(
        section(assemblePage(inputOf("emo-2-mono")), "emo.review"),
    ).filter((e) => e.type === "shape" && e.fill === catalog.canvasColor);
    assert.equal(reviews.length, 3);
});
test("C14 color dots use swatch values and sit next to measured captions", () => {
    for (const name of ["emo-2-mono", "show-2-mono", "common-2-point"]) {
        const input = inputOf(name),
            page = assemblePage(input),
            prefix = input.storyboard[0].pageSectionKey.split('.')[0];
        const els = elements(section(page, `${prefix}.color_product`));
        for (const text of els.filter((e) =>
            /^colors\.\d+\.name$/.test(e.copyRole || ""),
        )) {
            const index = Number(text.copyRole.split(".")[1]);
            const dot = els.find(
                (e) => e.type === "shape" && e.w === 10 && e.x === text.x - 18,
            );
            assert.equal(
                dot.fill,
                catalog.swatchHex[input.product.colors[index].swatchId],
            );
            assert.equal(dot.shape, "circle");
        }
    }
});
test("C16 color captions hide swatch-family English names; opening intro is optional and empty stays hidden", () => {
    for (const template of ["show", "common"]) {
        const prefix = template === 'common' ? 'emo' : template;
        const page = assemblePage(inputOf(`${template}-2-mono`));
        assert.ok(
            !texts(page).some((e) =>
                /^colors\.\d+\.en$/.test(e.copyRole || ""),
            ),
        );
        assert.ok(
            !texts(section(page, `${prefix}.first`)).some(
                (e) => e.copyRole === "intro",
            ),
        );
        const full = assemblePage(inputOf(`${template}-first-intro`));
        const intro = texts(section(full, `${prefix}.first`)).find(
            (e) => e.copyRole === "intro",
        );
        assert.ok(intro.text);
        assert.equal(intro.placeholder, undefined);
    }
});
test("C1 template defaults, per-project preferences, storage failure, and contiguous section groups", () => {
    assert.equal(defaultStitchedMode(null), false);
    assert.equal(defaultStitchedMode([{ pageDesignId: "P01-D09" }]), true);
    const values = new Map(),
        storage = {
            getItem: (k) => values.get(k),
            setItem: (k, v) => values.set(k, v),
        };
    assert.equal(readStitchedPreference("a", storage), null);
    writeStitchedPreference("a", false, storage);
    writeStitchedPreference("b", true, storage);
    assert.equal(readStitchedPreference("a", storage), false);
    assert.equal(readStitchedPreference("b", storage), true);
    const blocked = {
        getItem() {
            throw Error("blocked");
        },
        setItem() {
            throw Error("blocked");
        },
    };
    assert.equal(readStitchedPreference("a", blocked), null);
    assert.doesNotThrow(() => writeStitchedPreference("a", true, blocked));
    values.set("wearless:editor:stitched:a", "bad");
    assert.equal(readStitchedPreference("a", storage), null);
    const blocks = [
        { id: "a", pageSectionKey: "emo.a" },
        { id: "b", pageSectionKey: "emo.a" },
        { id: "c", pageSectionKey: "emo.b" },
        { id: "d" },
        { id: "e", pageSectionKey: "emo.a" },
    ];
    assert.deepEqual(
        canvasBlockRuns(blocks).map((run) => run.items.map((i) => i.index)),
        [[0, 1], [2], [3], [4]],
    );
});
test("generation completion applies final hug geometry, new paragraphs and contrast while preserving seller edits", () => {
    const blank = inputOf("emo-2-mono");
    blank.copyResults = [];
    const start = assemblePage(blank),
        job = { cuts: {}, copy: {}, failedCuts: [], live: [] };
    const local = decorateGenBlocks(start, job),
        final = assemblePage(inputOf("emo-md-paragraphs"));
    const hero = final
        .find((b) => b.pageSectionKind === "first")
        .elements.find((e) => e.type === "text");
    hero.contrastLines = [{ text: hero.text, color: "#111111", shade: true }];
    hero.contrastFor = {
        text: hero.text,
        x: hero.x,
        y: hero.y,
        w: hero.w,
        style: { ...hero.style },
    };
    const merged = mergeServerBlocks(local, final);
    assert.deepEqual(section(merged, "emo.intro"), section(final, "emo.intro"));
    assert.deepEqual(
        section(merged, "emo.for_you"),
        section(final, "emo.for_you"),
    );
    assert.deepEqual(section(merged, "emo.first"), section(final, "emo.first"));
    const edited = structuredClone(local),
        text = texts(section(edited, "emo.intro")).find(
            (e) => e.copyRole === "body",
        );
    text.text = "셀러가 쓴 문장";
    text.x += 7;
    const out = mergeServerBlocks(edited, final);
    assert.equal(
        texts(section(out, "emo.intro")).find((e) => e.copyRole === "body")
            .text,
        "셀러가 쓴 문장",
    );
    assert.equal(
        texts(section(out, "emo.intro")).find((e) => e.copyRole === "body").x,
        text.x,
    );
    assert.ok(out.every((b) => !b.genTemplateBaseline));
    const live = fillGenBlocks(local, {
        ...job,
        copy: Object.fromEntries(
            inputOf("emo-md-paragraphs").copyResults.map((c) => [
                c.blockId,
                c.texts,
            ]),
        ),
    });
    assert.deepEqual(
        section(mergeServerBlocks(live, final), "emo.intro"),
        section(final, "emo.intro"),
    );
});
test("C6 line metadata addresses actual wrapped lines and survives completed assembly", () => {
    const input = inputOf("emo-line-contrast"),
        regions = collectOverlayRegions(input),
        page = assemblePage(input);
    assert.ok(
        regions.every(
            (r) =>
                r.lines.length &&
                r.lines.every((l) => l.key.startsWith(r.key + ":")),
        ),
    );
    const lines = texts(page).filter((e) => e.contrastLines);
    assert.ok(lines.length);
    assert.ok(lines.some((e) => e.contrastLines.some((l) => l.shade)));
    assert.ok(lines.some((e) => e.contrastLines.some((l) => !l.shade)));
});
test("C2 max hugging also works without a visible background and text-width rules disappear for empty copy", () => {
    withLayout(
        [
            {
                type: "list",
                count: 2,
                copyRole: "items.n",
                hug: true,
                hugMode: "max",
                minW: 80,
                maxW: 300,
                padding: [14, 22],
                gap: 12,
                items: [{ type: "text", copyRole: "items.n", size: 20 }],
            },
        ],
        (input) => {
            input.copyResults = [
                {
                    blockId: input.storyboard[0].id,
                    texts: [
                        { role: "items.0", text: "가나다" },
                        { role: "items.1", text: "가나다 라마바" },
                    ],
                },
            ];
            const rows = texts(section(assemblePage(input), "emo.for_you"));
            assert.deepEqual(
                rows.map((e) => e.w),
                [127, 127],
            );
        },
    );
    for (const value of ["면 100%", ""])
        withLayout(
            [
                { type: "text", literal: value, size: 20 },
                { type: "rule", w: "text", h: 2 },
            ],
            (input) => {
                const els = elements(
                        section(assemblePage(input), "emo.for_you"),
                    ),
                    rule = els.find((e) => e.type === "shape");
                if (value)
                    assert.equal(rule.w, measureTextWidth(value, 1000, 20));
                else assert.equal(rule, undefined);
            },
        );
});
test("C16 optional English color roles use the actual name or remain hidden", () => {
    const grid = catalog.designs["CP-EMO-01"].layout.find(
        (n) => n.type === "photoGrid",
    );
    grid.captions.push({
        type: "text",
        copyRole: "colors.n.en",
        size: 20,
        hideEmpty: true,
    });
    try {
        for (const name of ["민트", "Mint"]) {
            const input = inputOf("emo-2-mono");
            input.product.colors[0] = {
                ...input.product.colors[0],
                name,
                swatchId: "green",
            };
            const english = texts(assemblePage(input)).find(
                (e) => e.copyRole === "colors.0.en",
            );
            assert.equal(english?.text, name === "Mint" ? "Mint" : undefined);
        }
    } finally {
        grid.captions.pop();
    }
});
test("DOM-derived text height keeps automatic template completion eligible without masking seller resizing", async () => {
    const { patchTemplateElement } =
        await import("../../src/lib/editorWaitSkeleton.js");
    const { expandBlockHeights } =
        await import("../../src/features/editor/editorGeometry.js");
    const input = inputOf("emo-2-mono");
    input.copyResults = [];
    const job = { cuts: {}, copy: {}, failedCuts: [], live: [] };
    const local = decorateGenBlocks(assemblePage(input), job),
        final = assemblePage(inputOf("emo-md-paragraphs"));
    const apply = (page, edit) =>
        expandBlockHeights(
            page.map((b) => {
                if (b.pageSectionKey !== "emo.intro") return b;
                const body = b.elements.find((e) => e.copyRole === "body");
                const block = edit ? { ...b, h: b.h + 73 } : b;
                return patchTemplateElement(block, body.id, {
                    h: body.h + 1000,
                });
            }),
        );
    assert.deepEqual(
        section(mergeServerBlocks(apply(local, false), final), "emo.intro"),
        section(final, "emo.intro"),
    );
    const resized = apply(local, true),
        out = mergeServerBlocks(resized, final);
    assert.equal(
        section(out, "emo.intro")[0].h,
        section(resized, "emo.intro")[0].h,
    );
});
