import test from "node:test";
import assert from "node:assert/strict";
import {
    getTemplate,
    pageTemplatesCatalog as catalog,
    validateCatalog,
    walkLayout,
} from "../../src/lib/pageTemplates/catalog.js";
import {
    baseColorOf,
    resolvePageTokens,
} from "../../src/lib/pageTemplates/tokens.js";

test("catalog keeps saved designs and exposes the current defaults without SNS by default", () => {
    assert.equal(validateCatalog(catalog), catalog);
    assert.equal(Object.keys(catalog.kinds).length, 10);
    assert.equal(Object.keys(catalog.designs).length, 52);
    assert.equal(Object.values(catalog.designs).filter(design => design.pickerHidden).length, 27);
    assert.equal(
        (catalog.firstTitleBand.top + catalog.firstTitleBand.bottom) / 2,
        0.5,
    );
    const first = catalog.designs["P01-D09"].layout[0].groups[0];
    assert.equal(first.anchor, "center");
    assert.equal(first.band, "firstTitleBand");
    for (const template of catalog.templates)
        for (const definition of [
            ...template.sections,
            ...Object.values(template.addPlacements),
        ]) {
            assert.ok(
                definition.photoSlots.every(
                    (slot) => slot.recipe?.examplePrefer !== "topClear",
                ),
            );
        }
    assert.deepEqual(catalog.photoBudgets, { detail: 4, maxExtraColors: 3 });
    assert.equal(getTemplate("guide"), undefined);
    for (const template of catalog.templates) {
        const slots = template.sections.flatMap(
            (section) => section.photoSlots,
        );
        assert.ok(
            slots
                .filter((slot) => slot.recipe?.detailPool === "points")
                .every((slot) => slot.count.max === 3),
        );
        for (const section of template.sections) {
            if (section.kind === "wear")
                assert.equal(section.designId, section.usage === "fit" ? "WEAR-FIT-COLUMN-01" : "P02-D01");
            if (section.kind === "color_product") assert.equal(section.designId, "CP-COLUMN-01");
            if (section.kind === "points") assert.equal(section.designId, template.id === "emo" ? "PT-PHOTO-CARD-01" : "PT-TEXT-PHOTO-01");
            if (section.kind === "sns") assert.equal(section.presence, "optional");
            if (section.presence === "default") assert.notEqual(catalog.designs[section.designId].pickerHidden, true);
        }
        assert.equal(template.addPlacements.wear.designId, "P02-D01");
        assert.equal(template.addPlacements.color_product.designId, "CP-COLUMN-01");
        assert.equal(template.addPlacements.points.designId, template.id === "emo" ? "PT-PHOTO-CARD-01" : "PT-TEXT-PHOTO-01");
        assert.deepEqual(template.addPlacements.sns.designIds, ["I09-D14", "P07-D08"]);
        assert.ok(template.addPlacements.sns.designIds.every(id => catalog.designs[id].pickerHidden));
    }
    assert.deepEqual(
        catalog.menuGroups.map((group) => group.name),
        ["사진", "옷 자세히", "상품 설명", "후기와 소통"],
    );
    assert.deepEqual(
        catalog.templates.map(
            (template) =>
                template.sections.filter(
                    (section) => section.presence === "default",
                ).length,
        ),
        [10, 7],
    );
    assert.deepEqual(
        getTemplate("emo")
            .sections.filter((s) => s.presence === "default")
            .map((s) => s.instanceKey),
        [
            "first",
            "review",
            "intro",
            "points",
            "for_you",
            "fabric",
            "wear_styling",
            "faq",
            "wear_fit",
            "color_product",
        ],
    );
    assert.deepEqual(
        getTemplate("show")
            .sections.filter((s) => s.kind === "points")
            .map((s) => s.instanceKey),
        ["points"],
    );
    for (const id of [
        "P01-D09",
        "P01-D01",
        "P01-D11",
        "P01-D09-G",
        "P01-SHOW-01-G",
        "P01-SHOW-01",
        "P02-D01",
        "P02-D02",
        "P04-D01",
        "WEAR-STYLING-2COL-01",
        "WEAR-STYLING-HERO-01",
        "WEAR-FIT-COLUMN-01",
        "WEAR-FIT-2COL-01",
        "WEAR-FIT-HERO-01",
        "CP-COLUMN-01",
        "CP-EMO-01",
        "CP-SHOW-01",
        "CP-GUIDE-01",
        "T03-D01",
        "T03-D06",
        "T03-D01-P",
        "T03-D06-P",
        "T03-GUIDE-01",
        "T03-TEXT-01",
        "T01-D07",
        "T06-D01",
        "PT-EMO-01",
        "PT-EMO-TEXT",
        "P06-D01",
        "PT-COLUMN-01",
        "PT-COLUMN-CARD-01",
        "PT-BELOW-CARD-01",
        "PT-PHOTO-CARD-01",
        "PT-TEXT-PHOTO-01",
        "T05-D07",
        "T07-D06",
        "T08-D01",
        "I09-D06",
        "I09-SCRIM-01",
        "T08-D07",
        "T08-SCRIM-01",
        "I09-D14",
        "P07-D08",
    ])
        assert.ok(catalog.designs[id], id);
});

const mutations = [
    [
        "deprecated empty-top preference",
        (c) =>
            (c.templates[0].sections[0].photoSlots[0].recipe.examplePrefer =
                "topClear"),
        /examplePrefer/,
    ],
    [
        "missing overlay group identifier",
        (c) => delete c.designs["P01-D09"].layout[0].groups[0].id,
        /overlay group/,
    ],
    [
        "invalid crop",
        (c) =>
            walkLayout(c.designs["T07-D06"].layout, (node) => {
                if (node.cropTop) node.cropTop = 1;
            }),
        /cropTop/,
    ],
    ["ten kinds", (c) => delete c.kinds.sns, /ten section kinds/],
    ["repeatable flag", (c) => (c.kinds.intro.repeatable = true), /repeatable/],
    [
        "nonrepeatable duplicate",
        (c) =>
            c.templates[0].sections.push({
                ...c.templates[0].sections[2],
                instanceKey: "intro_2",
            }),
        /repeated kind/,
    ],
    [
        "first position",
        (c) => c.templates[0].sections.reverse(),
        /first position|one first/,
    ],
    ["missing first", (c) => c.templates[0].sections.shift(), /one first/],
    [
        "duplicate key",
        (c) => (c.templates[0].sections[6].instanceKey = "first"),
        /instanceKey/,
    ],
    [
        "usage on another kind",
        (c) => (c.templates[0].sections[2].usage = "fit"),
        /section usage/,
    ],
    [
        "unknown wear usage",
        (c) => (c.templates[0].sections[6].usage = "unknown"),
        /section usage/,
    ],
    [
        "foreign copy role",
        (c) => (c.designs["T03-D01"].layout[0].copyRole = "fit.body"),
        /copyRole/,
    ],
    [
        "missing color names",
        (c) =>
            walkLayout(c.designs["CP-EMO-01"].layout, (n) => {
                if (n.copyRole === "colors.n.name") n.copyRole = "colors.n.en";
            }),
        /required copyRole/,
    ],
    [
        "missing fronts",
        (c) =>
            walkLayout(c.designs["CP-EMO-01"].layout, (n) => {
                if (n.slot === "front") n.slot = "removed";
            }),
        /required photo/,
    ],
    [
        "missing backs",
        (c) =>
            walkLayout(c.designs["CP-SHOW-01"].layout, (n) => {
                if (n.slot === "back") n.slot = "removed";
            }),
        /required photo/,
    ],
    [
        "wrong design kind",
        (c) => (c.templates[0].sections[0].designId = "T03-D01"),
        /design kind/,
    ],
    [
        "invalid cut type",
        (c) =>
            (c.templates[0].sections[0].photoSlots[0].recipe.cutType =
                "product"),
        /cutType/,
    ],
    [
        "invalid allowed cuts",
        (c) =>
            c.templates[0].sections[0].photoSlots[0].recipe.allowedCutTypes.push(
                "product",
            ),
        /cutType/,
    ],
    [
        "invalid worn shot",
        (c) =>
            (c.templates[0].sections[0].photoSlots[0].recipe.shot = "detail"),
        /shot/,
    ],
    [
        "invalid allowed shots",
        (c) =>
            c.templates[0].sections[0].photoSlots[0].recipe.allowedShots.push(
                "ghost",
            ),
        /shot/,
    ],
    [
        "invalid direction",
        (c) =>
            (c.templates[0].sections[0].photoSlots[0].recipe.direction =
                "above"),
        /direction/,
    ],
    [
        "reuse outside allowed locations",
        (c) =>
            Object.assign(c.templates[0].sections[1].photoSlots[0], {
                fill: "reuse",
                recipe: null,
                reuseFrom: {
                    kind: "wear",
                    usage: "styling",
                    slot: "g",
                    index: 0,
                },
            }),
        /reuse/,
    ],
    [
        "invalid showroom reuse",
        (c) =>
            (c.templates[1].sections[0].photoSlots[1].reuseFrom.color =
                "extra"),
        /reuse/,
    ],
    [
        "missing AI limit",
        (c) => (c.kinds.first.textSlots[0].maxChars = null),
        /maxChars/,
    ],
    [
        "unknown fact path",
        (c) => (c.kinds.first.textSlots[1].factPath = "analysis.transparency"),
        /factPath/,
    ],
    [
        "unknown font",
        (c) => (c.templates[0].fonts.titleKo.font = "Unknown"),
        /font/,
    ],
    [
        "unknown color token",
        (c) => (c.designs["T03-D01"].layout[0].color = "red"),
        /color token/,
    ],
    [
        "unknown layout",
        (c) => (c.designs["T03-D01"].layout[0].type = "html"),
        /layout/,
    ],
    [
        "inversion outside showroom",
        (c) => (c.templates[0].sections[2].block.invert = true),
        /invert block/,
    ],
    [
        "unknown placement",
        (c) => (c.templates[0].addPlacements.faq.after = "missing"),
        /placement target/,
    ],
    ["missing palette", (c) => delete c.pointPalette.blue, /palette/],
    ["bad metric", (c) => (c.textMetrics.lineHeight = 0), /text metric/],
    [
        "missing uppercase metric",
        (c) => delete c.textMetrics.uppercaseAscii,
        /text metric/,
    ],
    [
        "invalid title band",
        (c) => (c.firstTitleBand.top = 0.9),
        /first title band/,
    ],
    [
        "missing body font",
        (c) => delete c.templates[0].fonts.body,
        /required fonts/,
    ],
    [
        "missing ink",
        (c) => (c.templates[0].colors.ink = null),
        /required colors/,
    ],
    [
        "zero photo count",
        (c) => (c.templates[0].sections[0].photoSlots[0].count = 0),
        /photo count/,
    ],
    [
        "wrong photo aspect",
        (c) => (c.designs["P01-D09"].layout[0].aspect = "0/5"),
        /aspect/,
    ],
    [
        "negative geometry",
        (c) => (c.designs["T03-D01"].layout[0].w = -1),
        /geometry/,
    ],
    [
        "invalid default style",
        (c) => (c.defaults.colorMode = "unknown"),
        /default page style/,
    ],
    [
        "wrong additional SNS design",
        (c) => (c.templates[0].addPlacements.sns.designIds = ["P01-D09"]),
        /add design kind/,
    ],
];
for (const [name, mutate, pattern] of mutations)
    test(`catalog rejects ${name}`, () => {
        const changed = structuredClone(catalog);
        mutate(changed);
        assert.throws(() => validateCatalog(changed), pattern);
    });

const expectedPalette = {
    null: ["#5c5c5c", "#f4f4f4", "#e2e2e2"],
    ivory: ["#5b544c", "#f6f3ee", "#ded8d1"],
    beige: ["#75603b", "#fbf2e3", "#e5d7c0"],
    brown: ["#815939", "#fef1e6", "#ebd4c3"],
    purple: ["#6c5984", "#f6f0ff", "#ded4ed"],
    red: ["#87544b", "#fef0ed", "#f0d1cc"],
    yellow: ["#716232", "#f8f3e3", "#e1d9c0"],
    green: ["#446f4f", "#e9f8ec", "#c9e0ce"],
    blue: ["#4b648b", "#edf4ff", "#ccdaf1"],
    navy: ["#536386", "#eef4ff", "#ced9f1"],
    pink: ["#845261", "#ffeff3", "#eed0d8"],
};
const luminance = (hex) => {
    const rgb = [1, 3, 5]
        .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
        .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
};
test("point palette equals contract v2 §5.2 and has 4.5 contrast on white and its light surface", () => {
    for (const [swatch, palette] of Object.entries(catalog.pointPalette)) {
        assert.deepEqual(
            Object.values(palette),
            expectedPalette[swatch] || expectedPalette.null,
        );
        assert.ok(1.05 / (luminance(palette.accentInk) + 0.05) >= 4.5);
        assert.ok(
            (luminance(palette.accentBg) + 0.05) /
                (luminance(palette.accentInk) + 0.05) >=
                4.5,
        );
    }
});

test("token resolution uses the base swatch, preserves body fonts and does not mutate the catalog", () => {
    const before = structuredClone(catalog);
    const product = {
        colors: [
            { id: "other", swatchId: "red" },
            { id: "base", swatchId: "blue", isBase: true },
        ],
    };
    const tokens = resolvePageTokens(
        { templateId: "emo", fontStyle: "boldGothic", colorMode: "point" },
        baseColorOf(product).swatchId,
    );
    assert.equal(tokens.colors.accentInk, catalog.pointPalette.blue.accentInk);
    assert.equal(tokens.fonts.titleKo.weight, 700);
    assert.equal(tokens.fonts.labelEn.font, "Cal Sans");
    assert.equal(tokens.fonts.body.font, "Pretendard");
    assert.equal(tokens.fonts.body.weight, 400);
    assert.equal(
        resolvePageTokens({ colorMode: "point" }, "unknown").colors.accentInk,
        catalog.pointPalette.null.accentInk,
    );
    assert.deepEqual(catalog, before);
});
