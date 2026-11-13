import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

test("C6 editor and export use the same per-line DOM; shade is limited to failing lines", async (t) => {
    const vite = await createServer({
        configFile: false,
        plugins: [react()],
        optimizeDeps: { noDiscovery: true, include: [] },
        server: { middlewareMode: true, watch: null, hmr: false },
        appType: "custom",
        logLevel: "silent",
    });
    t.after(() => vite.close());
    const { TemplateContrastText, hasTemplateContrastLines } =
        await vite.ssrLoadModule(
            "/src/features/editor/TemplateContrastText.jsx",
        );
    const element = {
        text: "첫 줄\n둘째 줄\n셋째 줄",
        x: 20,
        y: 40,
        w: 400,
        style: { size: 30, lineHeight: 48, color: "#ffffff" },
        contrastLines: [
            { text: "첫 줄", color: "#ffffff", shade: false },
            { text: "둘째 줄", color: "#111111", shade: true },
            { text: "셋째 줄", color: "#ffffff", shade: true },
        ],
    };
    element.contrastFor = {
        text: element.text,
        x: 20,
        y: 40,
        w: 400,
        style: { ...element.style },
    };
    assert.equal(hasTemplateContrastLines(element), true);
    const markup = renderToStaticMarkup(
        React.createElement(TemplateContrastText, { element }),
    );
    assert.equal((markup.match(/template-contrast-line/g) || []).length, 3);
    assert.equal((markup.match(/has-shade/g) || []).length, 2);
    assert.match(markup, /class="template-contrast-line is-light">첫 줄/);
    assert.match(
        markup,
        /class="template-contrast-line is-dark has-shade">둘째 줄/,
    );
    for (const patch of [
        { text: "새 문장" },
        { x: 21 },
        { y: 41 },
        { w: 399 },
        { style: { ...element.style, size: 31 } },
        { style: { ...element.style, color: "#111111" } },
    ])
        assert.equal(hasTemplateContrastLines({ ...element, ...patch }), false);
    const editor = readFileSync(
        new URL("../../src/features/editor/Editor.jsx", import.meta.url),
        "utf8",
    );
    const exporter = readFileSync(
        new URL("../../src/features/editor/editorExport.js", import.meta.url),
        "utf8",
    );
    const css = readFileSync(
        new URL("../../src/styles/features.css", import.meta.url),
        "utf8",
    );
    assert.match(editor, /<TemplateContrastText element=\{contrastElement\} \/>/);
    assert.match(exporter, /const clone = blockNode\.cloneNode\(true\)/);
    assert.match(exporter, /toSvg\(clone,/);
    assert.match(
        css,
        /\.template-contrast-line\.is-light\.has-shade\s*\{\s*text-shadow:/,
    );
    assert.match(
        css,
        /\.template-contrast-line\.is-dark\.has-shade\s*\{\s*text-shadow:/,
    );
    assert.doesNotMatch(css, /\.template-contrast-line\s*\{[^}]*text-shadow:/);
});
