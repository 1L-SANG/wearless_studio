import test from "node:test";
import assert from "node:assert/strict";

import { defaultStoryboard } from "../../src/lib/api/shapes.js";
import { renderGroups } from "../../src/lib/storyboardRenderGroups.js";

const baseColors = [{ id: "base", isBase: true, images: [{ slot: "Front" }] }];
const multiColors = [
    ...baseColors,
    { id: "blue", images: [{ slot: "Front" }] },
    { id: "ivory", images: [{ slot: "Front" }] },
];

const context = (projectId) => ({
    projectId,
    clothingType: "top",
    targetGenders: ["women"],
});

for (const mode of ["basic", "extended"]) {
    for (const [palette, colors] of [
        ["single", baseColors],
        ["multi", multiColors],
    ]) {
        test(`${mode} ${palette}-color seed maps to four ordered render groups with global numbering`, () => {
            const blocks = defaultStoryboard(
                colors,
                mode,
                context(`${mode}:${palette}`),
            );
            const groups = renderGroups(blocks);

            assert.deepEqual(
                groups.map(({ key, title, label }) => ({ key, title, label })),
                [
                    { key: "hooking", title: "Section 1", label: "후킹" },
                    { key: "styling", title: "Section 2", label: "스타일링" },
                    { key: "studio", title: "Section 3", label: "스튜디오" },
                    { key: "product", title: "Section 4", label: "의류 확인" },
                ],
            );
            assert.deepEqual(
                groups.flatMap((group) =>
                    group.items.map((item) => item.index),
                ),
                Array.from(
                    { length: blocks.length },
                    (_value, index) => index + 1,
                ),
            );
            assert.deepEqual(
                groups.flatMap((group) =>
                    group.items.map((item) => item.block),
                ),
                blocks,
            );
            assert.ok(groups.every((group) => group.items.length > 0));
            assert.ok(
                groups[0].items.every(
                    ({ block }) => block.sectionRole === "hooking",
                ),
            );
            assert.ok(
                groups[1].items.every(
                    ({ block }) =>
                        block.sectionRole === "styling" &&
                        block.cutType !== "horizon",
                ),
            );
            assert.ok(
                groups[2].items.every(
                    ({ block }) =>
                        block.sectionRole === "studio" &&
                        block.cutType === "horizon",
                ),
            );
            assert.ok(
                groups[3].items.every(
                    ({ block }) => block.sectionRole === "product",
                ),
            );
        });
    }
}

test("empty boards still expose all render group headers", () => {
    assert.deepEqual(
        renderGroups([]).map((group) => [group.key, group.items.length]),
        [
            ["hooking", 0],
            ["styling", 0],
            ["studio", 0],
            ["product", 0],
        ],
    );
});
