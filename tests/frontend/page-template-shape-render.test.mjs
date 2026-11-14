import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { transformWithEsbuild } from "vite";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { assemblePage } from "../../src/lib/pageTemplates/assemble.js";
import { bakeExportBackdrops } from "../../src/features/editor/editorExportRaster.js";
import { isEmptyTextElement, getHiddenExportElementIds } from "../../src/features/editor/editorGeometry.js";
import { isSpeechBubbleElement } from "../../src/features/editor/editorBubbleFit.js";
import { cssWeight } from "../../src/features/editor/fontWeights.js";

// Run the actual editor JSX without importing the app, mounting effects, or
// starting a browser. Only hooks and export DOM/image boundaries are inert.
const editor = readFileSync(
    new URL("../../src/features/editor/Editor.jsx", import.meta.url),
    "utf8",
);
const componentSource = editor.slice(
    editor.indexOf("function CanvasElement("),
    editor.indexOf("\nconst IMAGE_IMPORT_COPY"),
);
const compiled = await transformWithEsbuild(
    componentSource,
    "CanvasElement.jsx",
    { jsx: "transform" },
);
const bindings = {
    React, useEditedTemplateContrast: el => el, useTitlePlatePreview: () => () => null,
    useRef: (current) => ({ current }),
    useState: (initial) => [initial, () => {}],
    useCallback: (fn) => fn,
    useLayoutEffect: () => {},
    isEmptyTextElement,
    isSpeechBubbleElement,
    cssWeight,
    FONT_MAP: {},
    // Exercise the unmeasured/pending text path; the contrast DOM has its own test.
    hasTemplateContrastLines: () => false,
};
const CanvasElement = new Function(
    ...Object.keys(bindings),
    `${compiled.code};return CanvasElement;`,
)(...Object.values(bindings));
const fixture = JSON.parse(
    readFileSync(
        new URL("../fixtures/page_templates_cases.json", import.meta.url),
    ),
);
const input = fixture.cases.find((c) => c.name === "show-light-swatches").input;
const dots = assemblePage(input)
    .flatMap((b) => b.elements)
    .filter((e) => e.type === "shape" && e.shape === "circle");
const markupOf = (el, options = {}) =>
    renderToStaticMarkup(
        CanvasElement({
            el,
            selected: false,
            editing: false,
            preview: false,
            scale: 1,
            ...options,
        }),
    );

const overlayShapes = [
    { type: "shape", shape: "rect", x: 0, y: 525, w: 1000, h: 725,
        fill: "linear-gradient(180deg, rgba(0, 0, 0, 0) 0%, rgba(0, 0, 0, 0.72) 100%)" },
    { type: "shape", shape: "rect", x: 70, y: 1000, w: 400, h: 60, fill: "#ffffff", opacity: .96,
        boxShadow: "0px 3px 12px rgba(0, 0, 0, 0.18)" },
];

test("D216 generic saved shape paints render as gradient, translucent white and a subtle shadow", () => {
    assert.match(markupOf(overlayShapes[0]), /background:linear-gradient\(180deg,/);
    const bubble = markupOf(overlayShapes[1]);
    assert.match(bubble, /opacity:0.96/);
    assert.match(bubble, /box-shadow:0px 3px 12px rgba\(0, 0, 0, 0.18\)/);
});

test("R1/R6 actual editor JSX renders circular swatches and list dots, including 1px light borders", () => {
    assert.ok(dots.some((e) => e.w === 6));
    assert.ok(dots.some((e) => e.w === 10 && e.fill === "#ffffff"));
    assert.ok(dots.some((e) => e.w === 10 && e.fill === "#f3eee1"));
    for (const dot of dots) {
        const markup = markupOf(dot);
        assert.match(markup, /class="el el-shape"/);
        assert.match(markup, /border-radius:50%/);
        assert.ok(markup.includes(`width:${dot.w}px;height:${dot.h}px`));
        assert.ok(markup.includes(`background:${dot.fill}`));
        assert.doesNotMatch(markup, /<line|<svg/);
        if (dot.strokeWidth === 1)
            assert.ok(
                markup.includes(`box-shadow:inset 0 0 0 1px ${dot.stroke}`),
            );
        else assert.doesNotMatch(markup, /box-shadow/);
    }
});

test("WOQ6 actual editor text keeps the planned line breaks and prevents emergency word splitting", () => {
    const input = structuredClone(
        fixture.cases.find((c) => c.name === "emo-title-wrap-two-line-balance")
            .input,
    );
    const block = assemblePage(input).find(
        (b) => b.pageSectionKey === "emo.first",
    );
    const title = block.elements.find((e) => e.copyRole === "name");
    const markup = markupOf(title);
    assert.match(markup, /class="el el-text el-keep-words"/);
    assert.ok(markup.includes(title.text));
    assert.match(markup, /font-size:51px/);
    assert.match(markup, /width:700px/);
    assert.doesNotMatch(markup, /white-space:pre-wrap/);
    assert.doesNotMatch(
        markupOf(block.elements.find((e) => e.copyRole === "label")),
        /el-keep-words/,
    );
    const css = readFileSync(
        new URL("../../src/styles/features.css", import.meta.url),
        "utf8",
    );
    assert.match(
        css,
        /\.el-text\.el-keep-words\s*\{\s*word-break: keep-all;\s*overflow-wrap: normal;/,
    );
    assert.match(css, /\.el-text\.el-keep-words\s*\{[^}]*white-space: pre;/);
});

test("R1 circles pass unchanged through the real download clone and capture functions into SVG/raster boundaries", async () => {
    const source = readFileSync(
        new URL("../../src/features/editor/editorExport.js", import.meta.url),
        "utf8",
    );
    const prepare = source.slice(
        source.indexOf("async function prepareClone("),
        source.indexOf("\nexport function flattenOnWhite"),
    );
    const capture = source.slice(
        source.indexOf("async function captureBlockCanvas("),
        source.indexOf("\nexport async function captureBlockCanvasSlices"),
    );
    const editorMarkup = [...dots, ...overlayShapes].map(markupOf);
    let appended = false,
        removed = false,
        receivedSvg;
    const holder = {
        style: {},
        appendChild() {},
        remove() {
            removed = true;
        },
    };
    const node = {
        cloneNode(deep) {
            assert.equal(deep, true);
            const children = editorMarkup.map((markup) => ({
                markup,
                style: {},
                classList: { remove() {} },
            }));
            return {
                offsetHeight: 100,
                style: {},
                dataset: { exportHeight: "100" },
                classList: { remove() {} },
                children,
                querySelectorAll: (selector) =>
                    selector === ".el" ? children : [],
            };
        },
    };
    const scope = {
        document: {
            createElement: () => holder,
            body: {
                appendChild() {
                    appended = true;
                },
            },
        },
        EXPORT_WIDTH: 1000,
        CHROME_SELECTOR: ".editor-chrome",
        replaceGifPosters() {},
        preflightImages: async () => ({ softFailed: 0 }),
        collectExportImageProbes: async () => [],
        bakeExportBackdrops,
        toSvg: async (clone, options) => {
            assert.equal(appended, true);
            assert.deepEqual(
                clone.children.map((child) => child.markup),
                editorMarkup,
            );
            assert.equal(options.width, 1000);
            assert.equal(options.height, 100);
            return clone.children.map((child) => child.markup).join("");
        },
        rasterizeExportSvg: async (svg, options) => {
            receivedSvg = svg;
            return { width: options.width, height: options.height };
        },
    };
    const captureBlock = new Function(
        ...Object.keys(scope),
        `${prepare}\n${capture}\nreturn captureBlockCanvas;`,
    )(...Object.values(scope));
    const result = await captureBlock(node, 2, "");
    assert.deepEqual(result, {
        canvas: { width: 2000, height: 200 },
        softFailed: 0,
    });
    assert.equal(receivedSvg, editorMarkup.join(""));
    assert.match(receivedSvg, /background:linear-gradient\(180deg,/);
    assert.match(receivedSvg, /box-shadow:0px 3px 12px rgba\(0, 0, 0, 0.18\)/);
    assert.equal(
        (receivedSvg.match(/border-radius:50%/g) || []).length,
        dots.length,
    );
    assert.doesNotMatch(receivedSvg, /<line|<svg/);
    assert.equal(removed, true);
});

test('WOC20 actual editor keeps empty bubbles editable and preview omits them with lining labels', () => {
    const value = fixture.cases.find(c => c.name === 'woc20-emo-review-empty').input;
    const block = assemblePage(value).find(b => b.pageSectionKind === 'review');
    const hidden = getHiddenExportElementIds(block);
    const bubble = block.elements.find(el => el.opacity === .96);
    assert.ok(hidden.has(bubble.id));
    assert.match(markupOf(bubble, { exportHidden: true }), /el-empty-template-copy/);
    assert.equal(markupOf(bubble, { exportHidden: true, preview: true }), '');
    const colors = assemblePage(fixture.cases.find(c => c.name === 'woc20-show-colors-3').input);
    const title = colors.flatMap(b => b.elements).find(el => el.copyRole === 'title');
    assert.match(markupOf(title), /el-lining-numbers/);
    assert.match(readFileSync(new URL('../../src/styles/features.css', import.meta.url), 'utf8'), /\.el-text\.el-lining-numbers\s*\{\s*font-variant-numeric: lining-nums;/);
});

test('WOC20 real download clone removes empty copy groups and uses the measured export height', async () => {
    const source = readFileSync(new URL('../../src/features/editor/editorExport.js', import.meta.url), 'utf8');
    const selector = new Function(`${source.slice(source.indexOf('const CHROME_SELECTOR ='), source.indexOf('// 한 번의 다운로드'))};return CHROME_SELECTOR;`)();
    const code = source.slice(source.indexOf('async function prepareClone('), source.indexOf('\nexport function flattenOnWhite'));
    const value = fixture.cases.find(c => c.name === 'woc20-emo-review-empty').input;
    const block = assemblePage(value).find(b => b.pageSectionKind === 'review');
    const hidden = getHiddenExportElementIds(block);
    const children = block.elements.filter(el => el.type === 'shape' || el.type === 'text').map(el => {
        const markup = markupOf(el, { exportHidden: hidden.has(el.id) });
        return { markup, removed: false, style: {}, classList: { remove() {} }, remove() { this.removed = true; } };
    });
    const clone = { classList: { remove() {} }, style: {}, dataset: { exportHeight: '1616' },
        querySelectorAll(query) {
            if (query === selector) return children.filter(child => selector.split(',').some(css => child.markup.includes(css.slice(1))));
            return query === '.el' ? children.filter(child => !child.removed) : [];
        } };
    const scope = { document: { createElement: () => ({ style: {}, appendChild() {}, remove() {} }), body: { appendChild() {} } },
        EXPORT_WIDTH: 1000, CHROME_SELECTOR: selector, replaceGifPosters() {}, preflightImages: async () => ({ softFailed: 0 }) };
    const prepare = new Function(...Object.keys(scope), `${code};return prepareClone;`)(...Object.values(scope));
    const result = await prepare({ cloneNode: () => clone });
    assert.equal(children.filter(child => !child.removed && child.markup.includes('opacity:0.96')).length, 0);
    assert.ok(children.some(child => !child.removed && child.markup.includes('먼저 입어 본 분들의 후기')));
    assert.equal(result.clone.style.height, '1616px');
    result.dispose();
});
