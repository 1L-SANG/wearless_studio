import { getTemplate, pageTemplatesCatalog } from "./catalog.js";

export function baseColorOf(product) {
    return (
        (product?.colors || []).find((color) => color.isBase) ||
        product?.colors?.[0] ||
        {}
    );
}

export const isNeutralPageSwatch = (swatch, catalog = pageTemplatesCatalog) => typeof swatch !== 'string'
    || !Object.hasOwn(catalog.pointPalette, swatch) || ['white', 'gray', 'black', 'null'].includes(swatch);

export function resolvePageTokens(
    pageStyle = {},
    baseSwatchId = null,
    catalog = pageTemplatesCatalog,
) {
    const template =
        getTemplate(pageStyle?.templateId, catalog) ||
        getTemplate(catalog.defaults.templateId, catalog);
    const fontStyle = catalog.fontStyles[pageStyle?.fontStyle]
        ? pageStyle.fontStyle
        : catalog.defaults.fontStyle;
    const neutral = isNeutralPageSwatch(baseSwatchId, catalog);
    const colorMode = pageStyle?.colorMode === "point" && !neutral ? "point" : "mono";
    const fonts = Object.fromEntries(
        Object.entries(template.fonts).map(([key, value]) => [
            key,
            { ...value },
        ]),
    );
    for (const [key, override] of Object.entries(
        catalog.fontStyles[fontStyle],
    )) {
        if (!override) continue;
        fonts[key] = { ...fonts[key], ...override };
        if (fontStyle === "serif" && key === "labelEn") {
            fonts[key].italic = template.fonts.labelEn.italic;
            fonts[key].uppercase = Boolean(template.fonts.labelEn.uppercase);
            fonts[key].trackingEm = template.fonts.labelEn.trackingEm;
        }
    }
    const colors = { ...template.colors, canvas: catalog.canvasColor };
    if (colorMode === "point" && !neutral) {
        const point =
            catalog.pointPalette[baseSwatchId] || catalog.pointPalette.null;
        colors.accentInk = point.accentInk;
        colors.accentLine = point.accentLine;
        colors.accentBg = point.accentBg;
        // Existing layouts name the pale surface `panel`; both role names
        // must resolve to the same tint. Inverted sections override it below.
        colors.panel = point.accentBg;
    }
    colors.accentBg ||= colors.panel;
    colors.invertInk ||= catalog.overlayColors.light;
    colors.summaryBg = template.id === 'emo' && colorMode === 'point' && !neutral ? colors.accentBg : colors.canvas;
    colors.pointBg = colors.summaryBg;
    return { template, fontStyle, colorMode, fonts, colors };
}

export function sectionTokens(tokens, section, catalog = pageTemplatesCatalog) {
    if (!section.block?.invert || tokens.template.id !== "show") return tokens;
    const colors = {
        ...tokens.colors,
        ink: tokens.colors.invertInk,
        muted: catalog.neutralColors.invertMuted,
        accentInk: catalog.neutralColors.invertMuted,
        accentLine: catalog.neutralColors.invertLine,
        accentBg: tokens.colors.invertBg,
        panel: tokens.colors.invertBg,
        summaryBg: tokens.colors.invertBg,
        pointBg: tokens.colors.invertBg,
    };
    return { ...tokens, colors };
}
