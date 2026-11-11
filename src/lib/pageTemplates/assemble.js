import {
    getTemplate,
    pageTemplatesCatalog,
    sectionDefinition,
    textSlotForRole,
} from "./catalog.js";
import { baseColorOf, resolvePageTokens, sectionTokens } from "./tokens.js";
import { buildAutoBlocks } from "./autoBlocks.js";
import { templateSectionName } from './slots.js';
import { isWornCropSource, reuseSourceForSlot } from './sectionDependencies.js';

const round = (value) => Math.floor(value + 0.5);
const lightSwatch = (hex) => {
    const rgb = Number.parseInt(hex.slice(1), 16);
    return (
        0.2126 * ((rgb >> 16) & 255) +
            0.7152 * ((rgb >> 8) & 255) +
            0.0722 * (rgb & 255) >
        225
    );
};
const string = (value) => (value == null ? "" : String(value));
const number = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);
const localSlot = (card, section) => {
    const key = string(card?.slotKey),
        prefix = `${section.instanceKey}.`;
    return key.startsWith(prefix) ? key.slice(prefix.length) : key;
};
const matches = (key, prefix) =>
    Boolean(
        key &&
        (prefix === "*" ||
            key === prefix ||
            key.startsWith(`${prefix}.`) ||
            new RegExp(`^${prefix}\\d+$`).test(key)),
    );
const colorName = (color, catalog) =>
    string(
        color?.name || color?.label || catalog.swatchLabels[color?.swatchId],
    );
const roleFor = (role, ctx) =>
    string(role)
        .replace(/\.n(?=\.|$)/g, `.${ctx.index || 0}`)
        .replace("{direction}", ctx.card?.direction || "front");

export function textUnits(text, metrics = pageTemplatesCatalog.textMetrics) {
    return Array.from(text).reduce(
        (sum, char) =>
            sum +
            (/\s/u.test(char)
                ? metrics.space
                : /[A-Z]/u.test(char)
                  ? metrics.uppercaseAscii
                  : char.codePointAt(0) < 128
                    ? metrics.ascii
                    : metrics.wide),
        0,
    );
}

// CSS letter-spacing includes the final character. Sum hundredths as integers
// so JS and Python agree at ceil/wrapping boundaries, including negative tracking.
const textWidth = (text, size, metrics, tracking) =>
    Math.max(
        0,
        (textUnits(text, metrics) * size * 100 +
            Array.from(text).length *
                round(tracking * 100) *
                metrics.unitsPerEm) /
            (metrics.unitsPerEm * 100),
    );

/** Shared word wrapping. Preserve spaces and explicit newlines; never split a word. */
export function wrapText(
    text,
    width,
    size,
    metrics = pageTemplatesCatalog.textMetrics,
    tracking = 0,
) {
    const budget = Math.max(size / metrics.unitsPerEm, width);
    const lines = [];
    for (const paragraph of string(text).split("\n")) {
        let line = "",
            spaces = "";
        for (const token of paragraph.match(/\s+|\S+/gu) || []) {
            if (/^\s+$/u.test(token)) {
                spaces += token;
                continue;
            }
            if (
                line &&
                textWidth(line + spaces + token, size, metrics, tracking) >
                    budget
            ) {
                lines.push(line);
                line = token;
            } else {
                line += spaces + token;
            }
            spaces = "";
        }
        lines.push(line + spaces);
    }
    return lines;
}

export function measureTextWidth(
    text,
    width,
    size,
    metrics = pageTemplatesCatalog.textMetrics,
    tracking = 0,
) {
    return Math.ceil(
        Math.min(
            width,
            Math.max(
                0,
                ...wrapText(text, width, size, metrics, tracking).map((line) =>
                    textWidth(line, size, metrics, tracking),
                ),
            ),
        ),
    );
}

export function estimateTextHeight(
    text,
    width,
    size,
    lineHeight,
    metrics = pageTemplatesCatalog.textMetrics,
    tracking = 0,
) {
    return (
        Math.max(1, wrapText(text, width, size, metrics, tracking).length) *
        lineHeight
    );
}

// Explicit newlines are mandatory breaks. Whitespace between unbroken words is
// preserved; only the separator used for a new line is replaced. At the font
// floor, an oversized word may additionally break after ] or before (.
function titleParagraphs(text, width, size, metrics, tracking, naturalBreaks) {
    return text.split("\n").map((text) => {
        const spans = [];
        for (const match of text.matchAll(/\S+/gu)) {
            const word = match[0],
                cuts = [0];
            if (
                naturalBreaks &&
                textWidth(word, size, metrics, tracking) > width
            )
                for (let i = 1; i < word.length; i += 1)
                    if (word[i - 1] === "]" || word[i] === "(") cuts.push(i);
            cuts.push(word.length);
            for (let i = 1; i < cuts.length; i += 1)
                spans.push([match.index + cuts[i - 1], match.index + cuts[i]]);
        }
        if (!spans.length) spans.push([0, text.length]);
        spans[0][0] = 0;
        spans.at(-1)[1] = text.length;
        return { text, spans };
    });
}

function balancedTitleLines(
    paragraphs,
    width,
    size,
    metrics,
    tracking,
    maxLines,
) {
    let best = null,
        bestScore = null;
    const visit = (paragraphIndex, start, lines, widths) => {
        if (paragraphIndex === paragraphs.length) {
            const score = [
                lines.length,
                Math.max(...widths) - Math.min(...widths),
                Math.max(...widths),
            ];
            if (
                !bestScore ||
                score.some(
                    (value, i) =>
                        value < bestScore[i] &&
                        score.slice(0, i).every((v, j) => v === bestScore[j]),
                )
            ) {
                best = lines;
                bestScore = score;
            }
            return;
        }
        if (lines.length + paragraphs.length - paragraphIndex > maxLines)
            return;
        const { text, spans } = paragraphs[paragraphIndex];
        for (let end = start; end < spans.length; end += 1) {
            const line = text.slice(spans[start][0], spans[end][1]);
            const lineWidth = round(
                textWidth(line, size, metrics, tracking) * 100,
            );
            if (lineWidth > width * 100) break;
            const last = end === spans.length - 1;
            visit(
                last ? paragraphIndex + 1 : paragraphIndex,
                last ? 0 : end + 1,
                [...lines, line],
                [...widths, lineWidth],
            );
        }
    };
    visit(0, 0, [], []);
    return best;
}

function layoutProductTitle(text, width, policy, metrics, trackingEm) {
    const floor = policy.sizes.at(-1);
    for (const size of policy.sizes) {
        const tracking = round(trackingEm * size * 100) / 100;
        const paragraphs = titleParagraphs(
            text,
            width,
            size,
            metrics,
            tracking,
            size === floor,
        );
        const lines =
            balancedTitleLines(
                paragraphs,
                width,
                size,
                metrics,
                tracking,
                policy.maxLines,
            ) ||
            (size === floor &&
                balancedTitleLines(
                    paragraphs,
                    width,
                    size,
                    metrics,
                    tracking,
                    policy.minSizeMaxLines,
                ));
        if (lines) return { size, lines };
        if (size !== floor) continue;
        // Infeasible input still retains every word at the font floor. Never
        // invent an internal break or silently discard the remaining copy.
        return {
            size,
            lines: paragraphs.flatMap(({ text, spans }) => {
                const lines = [];
                let start = 0;
                for (let end = 1; end < spans.length; end += 1)
                    if (
                        textWidth(
                            text.slice(spans[start][0], spans[end][1]),
                            size,
                            metrics,
                            tracking,
                        ) > width
                    ) {
                        lines.push(
                            text.slice(spans[start][0], spans[end - 1][1]),
                        );
                        start = end;
                    }
                lines.push(text.slice(spans[start][0], spans.at(-1)[1]));
                return lines;
            }),
        };
    }
}

const paddingOf = (node) =>
    Array.isArray(node.padding)
        ? node.padding
        : [node.padding || 0, node.padding || 0];
const rgba = (color, opacity) =>
    `rgba(${[1, 3, 5].map((index) => parseInt(color.slice(index, index + 2), 16)).join(", ")}, ${opacity})`;
const shapePaint = (node) => ({
    ...(node.fillOpacity != null ? { opacity: node.fillOpacity } : {}),
    ...(node.shadow ? { boxShadow: `${node.shadow.x}px ${node.shadow.y}px ${node.shadow.blur}px ${rgba(node.shadow.color, node.shadow.opacity)}` } : {}),
});
const samePanelSurface = (node, fill, surfaceColor) =>
    fill && fill.toLowerCase() === surfaceColor?.toLowerCase()
    && (node.fillOpacity ?? 1) === 1 && !node.shadow;

function renderScrim(scrim, x, y, width, height) {
    if (!scrim) return [];
    const h = height * (scrim.heightRatio ?? 1);
    return [{
        type: "shape", shape: "rect", x, y: y + height - h, w: width, h, radius: 0,
        ...(scrim.gradient
            ? { fill: `linear-gradient(180deg, ${rgba(scrim.color, 0)} 0%, ${rgba(scrim.color, scrim.opacity)} 100%)` }
            : { fill: scrim.color, opacity: scrim.opacity }),
    }];
}
const nativeCounts = [
    "",
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
];
export const koreanCount = (count) => nativeCounts[count] || string(count);

function materialsText(analysis, product) {
    return (analysis.materials || product.materials || [])
        .map((item) => {
            if (typeof item !== "object" || !item) return string(item);
            return item.name
                ? `${item.name}${item.ratio != null ? ` ${item.ratio}%` : ""}`
                : "";
        })
        .filter(Boolean)
        .join(", ");
}

function factText(role, ctx) {
    const { product, analysis, catalog, card } = ctx;
    const colors = product.colors || [];
    const selected =
        card?.colorId != null
            ? colors.find(
                  (color) => string(color.id) === string(card.colorId),
              ) || {}
            : colors[ctx.index || 0] || {};
    const fit = catalog.fitLabels[analysis.fit] || string(analysis.fit);
    if (role === "name") return string(product.name);
    if (role === "colors")
        return colors
            .map((color) => colorName(color, catalog))
            .filter(Boolean)
            .join(", ");
    if (ctx.kind.kind === "color_product" && role === "title")
        return `${koreanCount(colors.length)} 가지 색이에요`;
    if (/^colors\.\d+\.name$/.test(role) || /^colorName\.\d+$/.test(role))
        return colorName(selected, catalog);
    if (/^colors\.\d+\.en$/.test(role)) {
        const name = colorName(selected, catalog);
        return /^[A-Za-z][A-Za-z\s-]*$/.test(name) ? name : "";
    }
    if (role === "mix") return materialsText(analysis, product);
    if (role === "who") return string(ctx.profile.display_name) || "MD";
    if (role === "handle") return string(ctx.profile.handle);
    if (role === "facts")
        return [
            ["COLOR", factText("colors", ctx)],
            ["FABRIC", materialsText(analysis, product)],
            ["FIT", fit],
        ]
            .filter(([, value]) => value)
            .map(([label, value]) => `${label}  ${value}`)
            .join("\n");
    if (/^items\.\d+\.name$/.test(role)) {
        if (card?.pointItem?.kind === "wornCrop") return card.pointItem.label;
        const candidates = analysis.detailRecommendations?.candidates || [];
        return string(
            candidates.find(
                (item) =>
                    card?.detailTargetId != null &&
                    string(item.id) === string(card.detailTargetId),
            )?.label,
        );
    }
    // These facts do not exist in the current analysis contract. Do not infer them.
    return "";
}

function contentFor(node, ctx) {
    if (node.literal != null)
        return {
            text: string(node.literal)
                .replace(
                    "{number}",
                    string((ctx.index || 0) + 1).padStart(2, "0"),
                )
                .replace(
                    "{initial}",
                    Array.from(ctx.profile.display_name || "")[0] || "MD",
                ),
            slot: null,
            role: null,
        };
    const role = roleFor(node.copyRole, ctx),
        slot = textSlotForRole(ctx.kind, role);
    if (!slot) throw new Error(`Unknown copyRole ${ctx.kind.kind}.${role}`);
    let text = "";
    if (slot.source === "static")
        text =
            node.default ??
            (role === "label" && ctx.section.usage === "fit"
                ? "Fit"
                : slot.default);
    else if (slot.source === "fact") text = factText(role, ctx);
    else if (!ctx.copywriting && slot.source === "seller+ai")
        text = string(ctx.analysis.mdComment);
    else if (ctx.copywriting) {
        text = ctx.copy[role] || "";
        if (/^items\.\d+\.desc$/.test(role)) {
            const name = factText(role.replace(/\.desc$/, ".name"), ctx);
            const part = ctx.analysis.partDescriptionCopy?.find?.((item) => item.point === name)?.desc;
            if (node.copyPolicy === 'partOnly') {
                const lines = string(part).replace(/\r\n?/g, '\n').split('\n');
                // Old one-line copy remains stored and remains usable by saved layouts.
                text = lines.length === 2 && lines.every(line => line.trim()) ? lines.join('\n') : '';
            } else {
                text = part
                    || (ctx.card?.pointItem?.kind !== 'wornCrop'
                        && ctx.analysis.featureCopy?.find?.((item) => item.point === name)?.desc)
                    || text;
            }
        }
    }
    if (ctx.paragraphRole === role) text = ctx.paragraphText;
    return { text: string(text), slot, role };
}

function renderText(node, x, y, width, ctx) {
    const content = contentFor(node, ctx);
    if (node.hideEmpty && !content.text) return { elements: [], h: 0 };
    if (
        node.onlyExtraColor &&
        (!ctx.card?.colorId ||
            string(ctx.card.colorId) === string(ctx.baseColor.id))
    )
        return { elements: [], h: 0 };
    const token = node.style || content.slot?.style || "body";
    const fontKey = ["small"].includes(token)
        ? "body"
        : token === "num"
          ? "labelEn"
          : token;
    const font = { ...ctx.tokens.fonts[fontKey],
        ...(node.weight === 'light' ? { weight: 300 } : {}),
        ...(node.trackingEm != null ? { trackingEm: node.trackingEm } : {}) };
    const metrics = ctx.catalog.textMetrics;
    const uppercase =
        content.slot?.uppercase ||
        (font.uppercase && content.slot?.kind !== "fact");
    let text = uppercase ? content.text.toUpperCase() : content.text;
    if (node.numberPrefix && text) text = `${(ctx.index || 0) + 1}. ${text}`;
    if (node.singleLine) text = text.replace(/\s+/gu, " ").trim();
    let layoutWidth = Math.max(1, width - (node.swatchDot ? 18 : 0));
    let size = node.size || 25;
    const fallback = content.slot?.default || "";
    let display = text || (uppercase ? fallback.toUpperCase() : fallback);
    if (node.singleLine) display = display.replace(/\s+/gu, " ").trim();
    const titleWrap =
        ["P01-D09", "P01-D09-G"].includes(ctx.designId) && content.role === "name" && node.titleWrap;
    if (titleWrap) {
        layoutWidth = Math.min(
            layoutWidth,
            ctx.photoWidth * titleWrap.photoWidthRatio,
        );
        const layout = layoutProductTitle(
            display,
            layoutWidth,
            titleWrap,
            metrics,
            font.trackingEm || 0,
        );
        size = layout.size;
        display = layout.lines.join("\n");
        if (text) text = display;
        const align = node.align || ctx.align;
        x +=
            align === "center"
                ? (width - layoutWidth) / 2
                : align === "right"
                  ? width - layoutWidth
                  : 0;
        width = layoutWidth;
    } else if (display && node.singleLine) {
        const units = textUnits(display, metrics)
            + Array.from(display).length * (font.trackingEm || 0) * metrics.unitsPerEm;
        size = Math.min(size, Math.max(1, Math.floor(layoutWidth * metrics.unitsPerEm / Math.max(1, units))));
        while (size > 1 && textWidth(display, size, metrics, round((font.trackingEm || 0) * size * 100) / 100) > layoutWidth) size -= 1;
    } else if (display) {
        const longest = Math.max(
            ...display
                .split(/\s+/u)
                .map(
                    (word) =>
                        textUnits(word, metrics) +
                        Array.from(word).length *
                            (font.trackingEm || 0) *
                            metrics.unitsPerEm,
                ),
            1,
        );
        size = Math.min(
            size,
            Math.max(
                1,
                Math.floor(
                    (layoutWidth * ctx.catalog.textMetrics.unitsPerEm) /
                        longest,
                ),
            ),
        );
    }
    const factor =
        fontKey === "titleKo"
            ? metrics.titleLineHeight
            : fontKey === "labelEn"
              ? metrics.labelLineHeight
              : metrics.lineHeight;
    const lineHeight = Math.ceil(node.lineHeight != null ? size * node.lineHeight : (size * factor) / metrics.unitsPerEm);
    let color = node.color || content.slot?.color || "ink";
    if (!node.color && fontKey === "titleKo")
        color =
            ctx.tokens.template.id !== "show" &&
            ctx.tokens.colorMode === "point"
                ? "accentInk"
                : "ink";
    if (
        !node.color &&
        fontKey === "labelEn" &&
        ctx.tokens.template.id === "guide"
    )
        color = ctx.kind.kind === "first" ? "accentInk" : "ink";
    const bareOverlay = ctx.overlayGroupKey && !ctx.opaqueBackground;
    const autoContrast = bareOverlay && !ctx.fixedOverlayColor;
    const style = {
        font: font.font,
        weight: font.weight,
        italic: Boolean(font.italic),
        size,
        tracking: round((font.trackingEm || 0) * size * 100) / 100,
        lineHeight,
        align: node.align || ctx.align,
        color:
            bareOverlay
                ? ctx.fixedOverlayColor || ctx.onPhoto
                : color === "onPhoto"
                  ? ctx.onPhoto
                  : ctx.tokens.colors[color],
    };
    if (
        ctx.tokens.template.id === "guide" &&
        ctx.tokens.colorMode === "point" &&
        ctx.kind.kind === "intro" &&
        color === "muted"
    )
        style.color = ctx.catalog.neutralColors.panelMuted;
    const h = estimateTextHeight(
        text || display,
        layoutWidth,
        size,
        lineHeight,
        metrics,
        style.tracking,
    );
    const element = { type: "text", x, y, w: width, h, text, style };
    if (titleWrap || node.singleLine) element.keepWords = true;
    if (autoContrast)
        element._overlayGroupKey = ctx.overlayGroupKey;
    if (content.role) {
        Object.assign(element, {
            sourceBlockId: ctx.sourceId,
            copyRole: content.role,
            textSlotVersion: 1,
        });
        if (!text)
            element.placeholder =
                (titleWrap ? display : node.default || content.slot.default) ||
                "내용을 입력해 주세요";
        if (
            content.slot.source === "aiDraft" ||
            (content.slot.source === "seller+ai" && !ctx.analysis.mdComment)
        )
            element.aiDraft = true;
    }
    if (ctx.paragraphRole === content.role) {
        element.paragraphIndex = ctx.paragraphIndex;
        element.paragraphCount = ctx.paragraphCount;
    }
    if (node.swatchDot && content.text) {
        const color = ctx.product.colors?.[ctx.index || 0] || {};
        const fill = ctx.catalog.swatchHex[color.swatchId];
        if (fill) {
            const textW = measureTextWidth(
                text,
                width - 18,
                size,
                metrics,
                style.tracking,
            );
            const groupW = textW + 18;
            const start =
                x +
                (style.align === "center"
                    ? (width - groupW) / 2
                    : style.align === "right"
                      ? width - groupW
                      : 0);
            element.x = start + 18;
            element.w = textW;
            element.style.align = "left";
            return {
                elements: [
                    {
                        type: "shape",
                        shape: "circle",
                        x: start,
                        y: y + (lineHeight - 10) / 2,
                        w: 10,
                        h: 10,
                        fill,
                        radius: 999,
                        ...(lightSwatch(fill)
                            ? {
                                  stroke: ctx.tokens.colors.accentLine,
                                  strokeWidth: 1,
                              }
                            : {}),
                    },
                    element,
                ],
                h,
            };
        }
    }
    return { elements: [element], h };
}

function selectCards(node, ctx) {
    if (node.slot === "$current") return [ctx.card || null];
    if (node.slots) return node.slots.map((slot) => resolvePhoto(slot, ctx));
    // Loose/similar photos lack slot keys. Curated wildcards include them in
    // place; saved legacy layouts retain their existing overflow behavior.
    if (node.slot === "*" && (node.type === "photoFlow" || node.includeLoose || ctx.catalog.designs[ctx.designId]?.choiceAxes))
        return ctx.photos;
    return ctx.photos.filter((card) =>
        matches(localSlot(card, ctx.section), node.slot || "*"),
    );
}

function resolvePhoto(slotKey, ctx) {
    const own = ctx.photos.find((card) =>
        matches(localSlot(card, ctx.section), slotKey),
    );
    if (own) return own;
    const slot = ctx.section.photoSlots?.find(
        (item) => item.slotKey === slotKey,
    );
    if (slot?.fill !== "reuse") return null;
    return reuseSourceForSlot(ctx.storyboard, slot, ctx.product);
}

const naturalPhoto = (node) => node.fit === "contain" && node.aspect === "source";
const knownImageSize = (meta) => number(meta.width) > 0 && number(meta.height) > 0
    && meta.dimensionsKnown !== false;
const naturalImageSize = (node, meta, catalog) => knownImageSize(meta)
    ? [number(meta.width), number(meta.height)]
    : (node.fallbackAspect || catalog.textMetrics.fallbackAspect).split("/").map(Number);

function coverImageSize(card, meta, product) {
    const known = value => value && Number.isFinite(value.width) && value.width > 0
        && Number.isFinite(value.height) && value.height > 0 && value.dimensionsKnown !== false;
    if (known(meta) && (card?.source !== 'mine' || meta.imageUrl === card.ownImages?.[0]))
        return [meta.width, meta.height];
    const image = card?.source === 'mine' && (product.colors || []).flatMap(color => color.images || [])
        .find(image => image.src === card.ownImages?.[0] && known(image));
    return image ? [image.width, image.height] : null;
}

function imageHeight(node, width, card, ctx) {
    const meta = ctx.cuts[card?.id] || {};
    if (naturalPhoto(node)) {
        const [sw, sh] = naturalImageSize(node, meta, ctx.catalog);
        const region = card?.pointItem?.kind === "wornCrop" ? card.pointItem.cropRegion : null;
        // The selected region is the derived photo; sizing it as the full
        // original would crop the same source a second time.
        return Math.max(1, round(width * sh * (region?.[3] || 1) / (sw * (region?.[2] || 1))));
    }
    let ratio = node.aspect;
    if (!ratio || ratio === "source")
        ratio =
            number(meta.width) > 0 && number(meta.height) > 0
                ? `${meta.width}/${meta.height}`
                : ctx.catalog.textMetrics.fallbackAspect;
    const [w, h] = ratio.split("/").map(Number);
    const height = round((width * h) / w);
    return Math.max(
        round((width * ctx.catalog.textMetrics.minImageHeight) / 880),
        Math.min(
            round((width * ctx.catalog.textMetrics.maxImageHeight) / 880),
            height,
        ),
    );
}

function renderPhoto(node, x, y, width, ctx) {
    const card =
        node.slot === "$current" ? ctx.card : resolvePhoto(node.slot, ctx);
    const meta = ctx.cuts[card?.id] || {};
    const slot = ctx.section.photoSlots?.find(
        (item) =>
            matches(localSlot(card, ctx.section), item.slotKey) ||
            item.slotKey === node.slot,
    );
    const src =
        card?.source === "mine"
            ? card.ownImages?.[0] || null
            : meta.imageUrl || null;
    const h = imageHeight(node, width, card, ctx);
    const element = {
        type: "image",
        x,
        y,
        w: width,
        h,
        src,
        radius: node.radius || 0,
        slotKey: ctx.targetSlot
            ? `${ctx.section.instanceKey}.${ctx.targetSlot}`
            : card && card.pageSectionKey === ctx.key && card.slotKey
              ? card.slotKey
              : `${ctx.section.instanceKey}.${node.slot === "$current" ? `extra${(ctx.index || 0) + 1}` : node.slot}`,
        roleHint:
            card?.source === "mine"
                ? "custom"
                : slot?.contentRole || card?.contentRole || "coordination",
    };
    if (card) {
        element.sourceBlockId = card.id;
        if (card.cutType) element.cutType = card.cutType;
        if (card.pageSectionKey === ctx.key) ctx.used.add(card.id);
    }
    if (node.cropTop && node.fit !== "contain") {
        const sw = number(meta.width) > 0 ? number(meta.width) : 2,
            sh = number(meta.height) > 0 ? number(meta.height) : 3;
        const scale = Math.max(width / sw, h / (sh * (1 - node.cropTop)));
        const iw = Math.max(width, round(sw * scale)),
            ih = Math.max(h, round(sh * scale));
        element.crop = {
            iw,
            ih,
            ox: round((iw - width) / 2),
            oy: Math.min(ih - h, round(sh * scale * node.cropTop)),
        };
    }
    const coverSize = node.fit === 'cover' ? coverImageSize(card, meta, ctx.product) : null;
    if (node.fit === 'cover' && (coverSize || card?.pointItem?.kind === 'wornCrop')) {
        const worn = card?.pointItem?.kind === 'wornCrop';
        // Unknown ordinary photos use native object-fit cover, without guessing
        // their source ratio. Worn ROIs retain the generated-wear 3:4 fallback.
        const [sw, sh] = coverSize || [3, 4];
        const [rx, ry, rw, rh] = worn ? card.pointItem.cropRegion : [0, 0, 1, 1];
        // A fixed viewport covers the selected region, including saved legacy ROIs.
        // Unlike natural legacy layouts, cover intentionally permits upscaling.
        const scale = Math.max(width / (sw * rw), h / (sh * rh));
        const iw = Math.max(width, round(sw * scale)), ih = Math.max(h, round(sh * scale));
        element.crop = { iw, ih,
            ox: Math.max(0, Math.min(iw - width, round((rx + rw / 2) * iw - width / 2))),
            oy: Math.max(0, Math.min(ih - h, round((ry + rh / 2) * ih - h / 2))) };
        if (worn) {
            element.pointItemKind = 'wornCrop';
            element.bodyPart = card.pointItem.bodyPart;
        }
    } else if (card?.pointItem?.kind === "wornCrop") {
        const [rx, ry, rw, rh] = card.pointItem.cropRegion;
        const [sw, sh] = naturalPhoto(node)
            ? naturalImageSize(node, meta, ctx.catalog)
            : [number(meta.width) > 0 ? number(meta.width) : 2,
                number(meta.height) > 0 ? number(meta.height) : 3];
        const dimensionsKnown = knownImageSize(meta);
        const scale = Math.min(
            Math.max(width / (sw * rw), h / (sh * rh)),
            width / (sw * 0.3),
            dimensionsKnown ? 1 : Infinity,
        );
        // A native image smaller than the slot stays small; filling the slot
        // here would undo the pixel cap and distort its aspect ratio.
        const iw = round(sw * scale), ih = round(sh * scale);
        element.crop = {
            iw,
            ih,
            ox: Math.max(
                0,
                Math.min(iw - width, round((rx + rw / 2) * iw - width / 2)),
            ),
            oy: Math.max(
                0,
                Math.min(ih - h, round((ry + rh / 2) * ih - h / 2)),
            ),
        };
        element.pointItemKind = "wornCrop";
        element.bodyPart = card.pointItem.bodyPart;
    }
    const failure = ctx.failedCuts[card?.id];
    if (!src && failure) {
        Object.assign(element, {
            genFailed: true,
            genFailReason: failure.reason,
            genFailRetryable: failure.retryable,
        });
        if (failure.sourceBlockId != null)
            element.genFailSourceBlockId = failure.sourceBlockId;
    }
    return { elements: [element], h, card };
}

function overlayBoxes(
    bare,
    groupKey,
    x,
    y,
    width,
    height,
    metrics,
    ignoreBlank = false,
) {
    return {
        region: {
            left: (Math.min(...bare.map((el) => el.x)) - x) / width,
            top: (Math.min(...bare.map((el) => el.y)) - y) / height,
            right: (Math.max(...bare.map((el) => el.x + el.w)) - x) / width,
            bottom: (Math.max(...bare.map((el) => el.y + el.h)) - y) / height,
        },
        lines: bare.flatMap((el, elementIndex) =>
            wrapText(
                el.text || el.placeholder,
                el.w,
                el.style.size,
                metrics,
                el.style.tracking,
            ).map((text, lineIndex) => {
                const textW =
                    ignoreBlank && !text.trim()
                        ? 0
                        : measureTextWidth(
                              text,
                              el.w,
                              el.style.size,
                              metrics,
                              el.style.tracking,
                          );
                const left =
                    el.x +
                    (el.style.align === "center"
                        ? (el.w - textW) / 2
                        : el.style.align === "right"
                          ? el.w - textW
                          : 0);
                return {
                    key: `${groupKey}:${elementIndex}:${lineIndex}`,
                    text,
                    region: {
                        left: (left - x) / width,
                        top:
                            (el.y + lineIndex * el.style.lineHeight - y) /
                            height,
                        right: (left + textW - x) / width,
                        bottom:
                            (el.y + (lineIndex + 1) * el.style.lineHeight - y) /
                            height,
                    },
                };
            }),
        ),
    };
}

function regionPlan(boxes) {
    return {
        region: boxes.region,
        lines: boxes.lines.map(({ text, ...line }) => line),
    };
}

function renderGroups(groups, x, y, width, height, ctx, photo) {
    return (groups || []).flatMap((group) => {
        const groupKey = `${ctx.key}:${photo.slotKey}:${group.id}`;
        const measured =
            ctx.cuts[photo.sourceBlockId]?.overlayTextColors?.[groupKey];
        const fixedColor = ctx.tokens.colors[group.textColor] || group.textColor;
        const onPhoto = fixedColor || (Object.values(ctx.catalog.overlayColors).includes(
            measured,
        )
            ? measured
            : ctx.catalog.overlayColors.light);
        const result = renderNodes(
            group.nodes,
            x + (group.x || 0),
            0,
            Math.min(group.w || width, width - (group.x || 0)),
            {
                ...ctx,
                onPhoto,
                overlayGroupKey: groupKey,
                fixedOverlayColor: fixedColor,
                opaqueBackground: false,
                surfaceColor: null,
                photoWidth: width,
            },
        );
        const top = Math.min(0, ...result.elements.map((element) => element.y));
        const bottom = Math.max(
            0,
            ...result.elements.map((element) => element.y + element.h),
        );
        const band = group.band ? ctx.catalog[group.band] : null;
        let gy =
            y +
            (group.anchor === "center"
                ? height * (band ? (band.top + band.bottom) / 2 : 0.5) -
                  (top + bottom) / 2
                : group.anchor === "bottom"
                  ? Math.max(0, height - (group.y || 0) - result.h)
                  : group.y || 0);
        const policy =
            ["P01-D09", "P01-D09-G"].includes(ctx.designId) ? group.positionFallback : null;
        let offsets;
        if (policy) {
            const inset = Math.max(
                0,
                Math.min(
                    height * policy.edgeInsetRatio,
                    (height - (bottom - top)) / 2,
                ),
            );
            offsets = {
                center: gy,
                top: y + inset - top,
                bottom: y + height - inset - bottom,
            };
            const position =
                ctx.cuts[photo.sourceBlockId]?.overlayTextPositions?.[groupKey];
            if (policy.anchors.includes(position)) gy = offsets[position];
        }
        const placed = result.elements.map((element) => ({
            ...element,
            y: element.y + gy,
        }));
        const bare = placed.filter(
            (element) => element._overlayGroupKey === groupKey,
        );
        const boxes = bare.length
            ? overlayBoxes(
                  bare,
                  groupKey,
                  x,
                  y,
                  width,
                  height,
                  ctx.catalog.textMetrics,
                  Boolean(policy),
              )
            : null;
        const lineBoxes = boxes?.lines || [];
        if (bare.length && photo.sourceBlockId && ctx.overlayRegions) {
            const plan = {
                sourceBlockId: photo.sourceBlockId,
                key: groupKey,
                frame: {
                    w: photo.w,
                    h: photo.h,
                    ...(photo.crop ? { crop: photo.crop } : {}),
                },
                ...regionPlan(boxes),
            };
            if (policy) {
                const rawBare = result.elements.filter(
                    (el) => el._overlayGroupKey === groupKey,
                );
                plan.positionCandidates = policy.anchors.map((position) => ({
                    position,
                    ...regionPlan(
                        overlayBoxes(
                            rawBare.map((el) => ({
                                ...el,
                                y: el.y + offsets[position],
                            })),
                            groupKey,
                            x,
                            y,
                            width,
                            height,
                            ctx.catalog.textMetrics,
                            true,
                        ),
                    ),
                }));
            }
            ctx.overlayRegions.push(plan);
        }
        const measuredLines =
            ctx.cuts[photo.sourceBlockId]?.overlayTextLines || {};
        bare.forEach((el, elementIndex) => {
            const lines = lineBoxes.filter((line) =>
                line.key.startsWith(`${groupKey}:${elementIndex}:`),
            );
            if (
                !lines.length ||
                !lines.every((line) =>
                    Object.values(ctx.catalog.overlayColors).includes(
                        measuredLines[line.key]?.color,
                    ),
                )
            )
                return;
            el.contrastLines = lines.map((line) => ({
                text: line.text,
                color: policy ? onPhoto : measuredLines[line.key].color,
                shade: measuredLines[line.key].shade === true,
            }));
            el.contrastFor = {
                text: el.text,
                x: el.x,
                y: el.y,
                w: el.w,
                style: { ...el.style },
            };
        });
        return placed.map(({ _overlayGroupKey, ...element }) => element);
    });
}

function renderNode(node, x, y, width, ctx) {
    if (node.whenTemplate && node.whenTemplate !== ctx.tokens.template.id)
        return { elements: [], h: 0 };
    if (node.firstItemOnly && ctx.paragraphIndex > 0)
        return { elements: [], h: 0 };
    x += node.x || 0;
    const availableWidth = width - (node.x || 0);
    width = Math.min(
        node.type === "list"
            ? width
            : node.w === "text"
              ? ctx.lastTextWidth || 0
              : node.w || width,
        width - (node.x || 0),
    );
    if (node.type === "factTable" && node.layout && node.layout !== "inline")
        return renderFactTable(node, x, y, width, ctx);
    if (node.type === "text" || node.type === "factTable")
        return renderText(node, x, y, width, ctx);
    if (node.type === "photo") return renderPhoto(node, x, y, width, ctx);
    if (node.type === "rule") {
        if (!width) return { elements: [], h: 0 };
        const align = node.align === "template" ? ctx.align : node.align;
        if (align === "center") x += (availableWidth - width) / 2;
        return {
            elements: [
                {
                    type: "shape",
                    shape: "rect",
                    x,
                    y,
                    w: width,
                    h: node.h || 2,
                    fill:
                        node.color === "onPhoto"
                            ? ctx.onPhoto
                            : ctx.tokens.colors[node.color || "accentLine"],
                    radius: 0,
                },
            ],
            h: node.h || 2,
        };
    }
    if (node.type === "panel") {
        if (node.splitParagraphs)
            return renderParagraphs(node, x, y, availableWidth, ctx);
        const [padY, requestedPadX] = paddingOf(node);
        const fill = ctx.tokens.colors[node.fill];
        const sameSurface = samePanelSurface(node, fill, ctx.surfaceColor);
        const padX = sameSurface ? 0 : requestedPadX;
        const childCtx = {
            ...ctx,
            opaqueBackground: ctx.opaqueBackground || Boolean(node.fill),
            surfaceColor: fill || ctx.surfaceColor,
        };
        const children = (boxX, boxWidth) => {
            if (node.columns) {
                const columns = node.columns.map((col) =>
                    renderNodes(
                        col.nodes,
                        boxX + padX + col.x,
                        y + padY,
                        Math.max(
                            1,
                            Math.min(col.w, boxWidth - 2 * padX - col.x),
                        ),
                        childCtx,
                    ),
                );
                return {
                    elements: columns.flatMap((col) => col.elements),
                    h: Math.max(0, ...columns.map((col) => col.h)),
                };
            }
            return renderNodes(
                node.nodes,
                boxX + padX,
                y + padY,
                Math.max(1, boxWidth - 2 * padX),
                childCtx,
            );
        };
        width = Math.min(width, node.maxW || width);
        let result = children(x, width);
        if (node.hug) {
            width = Math.min(
                width,
                Math.max(
                    node.minW || 0,
                    contentWidth(result.elements, x + padX) + padX * 2,
                ),
            );
            const align =
                node.align === "template" ? ctx.align : node.align || "left";
            x +=
                align === "right"
                    ? availableWidth - width
                    : align === "center"
                      ? (availableWidth - width) / 2
                      : 0;
            result = children(x, width);
        }
        const h = result.h + padY * 2;
        return {
            elements: [
                ...(node.fill && !sameSurface
                    ? [
                          {
                              type: "shape",
                              shape: "rect",
                              x,
                              y,
                              w: width,
                              h,
                              fill: ctx.tokens.colors[node.fill],
                              radius: node.radius || 0,
                              ...shapePaint(node),
                          },
                      ]
                    : []),
                ...result.elements,
            ],
            h,
        };
    }
    if (node.type === "overlay") {
        const result = renderPhoto(node, x, y, width, ctx);
        return {
            ...result,
            elements: [
                ...result.elements,
                ...renderScrim(node.scrim, x, y, width, result.h),
                ...renderGroups(
                    node.groups,
                    x,
                    y,
                    width,
                    result.h,
                    ctx,
                    result.elements[0],
                ),
            ],
        };
    }
    if (node.type === "list") {
        const pattern = string(node.countRole || node.copyRole);
        const indexPattern = new RegExp(
            `^${pattern.replaceAll(".", "\\.").replace("\\.n", "\\.(\\d+)")}$`,
        );
        const indexes = Object.keys(ctx.copy)
            .map((key) => key.match(indexPattern)?.[1])
            .filter((value) => value != null)
            .map(Number);
        const count = Math.min(
            node.maxCount || node.count,
            Math.max(node.count, ...indexes.map((index) => index + 1)),
        );
        let cursor = y;
        const elements = [];
        const maxWidth = Math.min(node.maxW || node.w || width, width);
        const items = node.marker
            ? node.items.map((child) => ({ ...child, x: (child.x || 0) + 22 }))
            : node.items;
        const itemNode = {
            type: "panel",
            padding: node.padding || 0,
            fill: node.fill,
            fillOpacity: node.fillOpacity,
            shadow: node.shadow,
            radius: node.radius,
            nodes: items,
            hug: node.hug,
            minW: node.minW,
            maxW: maxWidth,
        };
        let sharedWidth = 0;
        if (node.hug && node.hugMode === "max") {
            for (let i = 0; i < count; i += 1) {
                const result = renderNode(itemNode, 0, 0, maxWidth, {
                    ...ctx,
                    index: i,
                });
                const fill = ctx.tokens.colors[node.fill];
                const sameSurface = samePanelSurface(node, fill, ctx.surfaceColor);
                const padX = sameSurface ? 0 : paddingOf(node)[1];
                const boxWidth =
                    fill && !sameSurface
                        ? result.elements[0]?.w || 0
                        : Math.max(
                              node.minW || 0,
                              ...result.elements.map(
                                  (el) => el.x + el.w + padX,
                              ),
                          );
                sharedWidth = Math.max(sharedWidth, boxWidth);
            }
        }
        for (let i = 0; i < count; i += 1) {
            const align = node.alternate
                ? i % 2
                    ? "right"
                    : "left"
                : node.align || "left";
            const result = renderNode(
                {
                    ...itemNode,
                    nodes: items,
                    align,
                    minW: sharedWidth || node.minW,
                    maxW: sharedWidth || maxWidth,
                },
                x,
                cursor,
                width,
                { ...ctx, index: i },
            );
            if (node.marker && result.h) {
                const first = result.elements.find((el) => el.type === "text");
                if (first) {
                    const firstLine = wrapText(
                        first.text || first.placeholder,
                        first.w,
                        first.style.size,
                        ctx.catalog.textMetrics,
                        first.style.tracking,
                    )[0];
                    const textW = measureTextWidth(
                        firstLine,
                        first.w,
                        first.style.size,
                        ctx.catalog.textMetrics,
                        first.style.tracking,
                    );
                    const textStart =
                        first.x +
                        (first.style.align === "center"
                            ? (first.w - textW) / 2
                            : first.style.align === "right"
                              ? first.w - textW
                              : 0);
                    result.elements.unshift({
                        type: "shape",
                        shape: node.marker === "dot" ? "circle" : "rect",
                        x: textStart - 22,
                        y: first.y + (first.style.lineHeight - 6) / 2,
                        w: 6,
                        h: 6,
                        fill: ctx.tokens.colors.ink,
                        radius: node.marker === "dot" ? 999 : 0,
                    });
                }
            }
            elements.push(...result.elements);
            cursor += result.h + (i < count - 1 ? node.itemGap ?? node.gap ?? 0 : 0);
        }
        return { elements, h: cursor - y };
    }
    throw new Error(`Unsupported nested layout ${node.type}`);
}

function renderNodes(nodes, x, y, width, ctx) {
    let cursor = y;
    const elements = [];
    const matchingRules = [];
    let lastTextWidth = ctx.lastTextWidth;
    for (const node of nodes || []) {
        const result = renderNode(node, x, cursor, width, {
            ...ctx,
            lastTextWidth,
        });
        elements.push(...result.elements);
        if (node.type === "rule" && node.matchText && result.elements.length)
            matchingRules.push({ node, element: result.elements[0] });
        const last = result.elements.findLast((el) => el.type === "text");
        if (last)
            lastTextWidth = measureTextWidth(
                last.text,
                last.w,
                last.style.size,
                ctx.catalog.textMetrics,
                last.style.tracking,
            );
        if (!node.absolute && result.h) cursor += result.h + (node.gap || 0);
    }
    // Resolve forward references too: the upper rule precedes the name, while
    // the lower rule follows colors. Both must use the name's longest line.
    for (const { node, element } of matchingRules) {
        const text = elements.find((el) => el.copyRole === node.matchText);
        if (!text) continue;
        const ruleWidth = Math.min(
            width * node.maxWidthRatio,
            Math.max(
                node.minW,
                measureTextWidth(
                    text.text || text.placeholder,
                    text.w,
                    text.style.size,
                    ctx.catalog.textMetrics,
                    text.style.tracking,
                ),
            ),
        );
        element.x += (element.w - ruleWidth) / 2;
        element.w = ruleWidth;
    }
    return {
        elements,
        h: Math.max(cursor - y, ...elements.map((el) => el.y + el.h - y), 0),
    };
}

function contentWidth(elements, x) {
    return Math.max(
        0,
        ...elements.map(
            (el) =>
                el.x -
                x +
                (el.type === "text"
                    ? measureTextWidth(
                          el.text || el.placeholder,
                          el.w,
                          el.style.size,
                          pageTemplatesCatalog.textMetrics,
                          el.style.tracking,
                      )
                    : el.w),
        ),
    );
}

function renderParagraphs(node, x, y, width, ctx) {
    const role = node.splitParagraphs;
    const source = node.nodes.find((child) => child.copyRole === role);
    const paragraphs = contentFor(source, ctx)
        .text.split(/\n+/)
        .map((text) => text.trim())
        .filter(Boolean);
    // Keep all copy: excess paragraphs share the third bubble.
    const parts = paragraphs.length
        ? [
              ...paragraphs.slice(0, 2),
              ...(paragraphs.length > 2
                  ? [paragraphs.slice(2).join("\n")]
                  : []),
          ]
        : [""];
    let cursor = y;
    const elements = [];
    parts.forEach((text, i) => {
        const result = renderNode(
            { ...node, x: 0, splitParagraphs: null },
            x,
            cursor,
            width,
            {
                ...ctx,
                paragraphRole: role,
                paragraphText: text,
                paragraphIndex: i,
                paragraphCount: parts.length,
            },
        );
        elements.push(...result.elements);
        cursor += result.h + (i < parts.length - 1 ? 12 : 0);
    });
    return { elements, h: cursor - y };
}

function renderFactTable(node, x, y, width, ctx) {
    const rows = factText("facts", ctx)
        .split("\n")
        .filter(Boolean)
        .map((row) => {
            const split = row.indexOf("  ");
            return [row.slice(0, split), row.slice(split + 2)];
        });
    let cursor = y;
    const elements = [];
    rows.forEach(([label, value], index) => {
        const stacked = node.layout === "stacked";
        const align = stacked
            ? node.align === "template"
                ? ctx.align
                : node.align || ctx.align
            : "left";
        const lab = renderText(
            {
                literal: label.toUpperCase(),
                style: "body",
                size: round((node.size || 25) * 0.72),
                color: "muted",
                align,
            },
            x,
            cursor,
            stacked ? width : 150,
            ctx,
        );
        const val = renderText(
            {
                literal: value,
                style: "body",
                size: node.size || 25,
                align,
            },
            x + (stacked ? 0 : 150),
            cursor + (stacked ? lab.h + 4 : 0),
            stacked ? width : width - 150,
            ctx,
        );
        for (const [part, result] of [
            ["label", lab],
            ["value", val],
        ])
            elements.push(
                ...result.elements.map((el) => ({
                    ...el,
                    sourceBlockId: ctx.sourceId,
                    copyRole: "facts",
                    textSlotVersion: 1,
                    factLabel: label,
                    factPart: part,
                })),
            );
        cursor +=
            (stacked ? lab.h + 4 + val.h : Math.max(lab.h, val.h)) +
            (index < rows.length - 1 ? (stacked ? 24 : 20) : 0);
    });
    if (rows.length && node.layout === "columns") {
        elements.push({
            type: "shape",
            shape: "rect",
            x,
            y: cursor,
            w: width,
            h: 1,
            fill: ctx.tokens.colors.accentLine,
            radius: 0,
        });
        cursor += 1;
    }
    return { elements, h: cursor - y };
}

const photoGroupKey = (card) => card?.spaceGroupId ? `space:${card.spaceGroupId}` : `color:${card?.colorId ?? ""}`;

function contiguousPhotoGroups(cards) {
    const groups = [];
    for (const card of cards) {
        const last = groups.at(-1);
        if (last && photoGroupKey(last[0]) === photoGroupKey(card)) last.push(card);
        else groups.push([card]);
    }
    return groups;
}

/** Existing photos only. Preserve contiguous runs even after an entire run is framed. */
export function selectFramePhotos(cards, count) {
    const groups = contiguousPhotoGroups(cards);
    const medium = (group) => group.filter(card => card.shot === 'medium');
    const winner = groups.find(group => medium(group).length >= count)
        || groups.reduce((best, group) => medium(group).length > medium(best).length ? group : best, groups[0] || []);
    const seen = new Set(), frame = [];
    for (const card of [...medium(winner), ...winner.filter(card => card.shot === 'full'), ...cards]) {
        if (frame.length >= count) break;
        if (seen.has(card.id)) continue;
        seen.add(card.id); frame.push(card);
    }
    const unused = winner.filter(card => !seen.has(card.id));
    const remainingGroups = groups.map(group => group.filter(card => !seen.has(card.id))).filter(group => group.length);
    return { frame, remaining: remainingGroups.flat(), remainingGroups,
        background: unused.find(card => card.shot === 'full') || unused[0] || frame[0] || null };
}

function renderFrameFact(text, role, x, y, width, ctx) {
    if (!text) return { elements: [], h: 0 };
    const frame = ctx.catalog.frameStyle;
    const style = { font: ctx.tokens.fonts.body.font, size: frame.textSize, weight: frame.textWeight, italic: false,
        tracking: round(frame.textTrackingEm * frame.textSize * 100) / 100, lineHeight: frame.textLineHeight,
        align: 'center', color: ctx.tokens.colors[frame.ink] };
    const h = estimateTextHeight(text, width, style.size, style.lineHeight, ctx.catalog.textMetrics, style.tracking);
    return { elements: [{ type: 'text', x, y, w: width, h, text, style,
        sourceBlockId: ctx.sourceId, copyRole: role, textSlotVersion: 1 }], h };
}

function renderFrameBand(selection, y, ctx) {
    if (!selection.frame.length) return { elements: [], h: 0 };
    const frame = ctx.catalog.frameStyle, cardX = (ctx.catalog.canvasWidth - frame.cardWidth) / 2,
        cardY = y + frame.bandPadding, photoX = cardX + frame.paddingX, photoWidth = frame.cardWidth - frame.paddingX * 2;
    let cursor = cardY + frame.paddingTop;
    const head = renderFrameFact(string(ctx.product.name), 'name', photoX, cursor, photoWidth, ctx);
    const content = [...head.elements];
    cursor += head.h + (head.h ? frame.photoTop : 0);
    selection.frame.forEach((card, i) => {
        const result = renderPhoto({ type: 'photo', slot: '$current', fit: 'contain', aspect: 'source', fallbackAspect: frame.fallbackAspect },
            photoX, cursor, photoWidth, { ...ctx, card, index: ctx.photos.indexOf(card) });
        content.push(...result.elements); cursor += result.h + (i + 1 < selection.frame.length ? frame.photoGap : 0);
    });
    const shopName = ctx.shopName;
    if (shopName) {
        cursor += frame.footerGap;
        const foot = renderFrameFact(shopName, 'who', photoX, cursor, photoWidth, ctx);
        content.push(...foot.elements); cursor += foot.h;
    }
    const cardHeight = cursor + frame.paddingBottom - cardY, height = cardHeight + frame.bandPadding * 2;
    // A background reuse must not consume a remaining original photo.
    const backdrop = renderPhoto({ type: 'photo', slot: '$current', aspect: '4/5' }, 0, y, ctx.catalog.canvasWidth,
        { ...ctx, card: selection.background, index: ctx.photos.indexOf(selection.background), used: new Set() }).elements[0];
    return { elements: [{ ...backdrop, h: height, blur: frame.blur },
        { type: 'shape', shape: 'rect', x: cardX, y: cardY, w: frame.cardWidth, h: cardHeight, fill: ctx.tokens.colors[frame.paper], radius: 0 },
        ...content], h: height };
}

function photoFlowRows(node, cards) {
    const groupKey = (card) => node.groupBy === "none" ? "all" : photoGroupKey(card);
    const rows = [];
    for (let start = 0; start < cards.length;) {
        let end = start + 1;
        while (end < cards.length && groupKey(cards[end]) === groupKey(cards[start])) end += 1;
        for (let index = start, row = 0; index < end; row += 1) {
            const remaining = end - index;
            const count = Math.min(remaining, node.pattern === "twoColumn" || remaining <= 2 ? 2 : row % 2 ? 2 : 1);
            rows.push({ index, cards: cards.slice(index, index + count) });
            index += count;
        }
        start = end;
    }
    return rows;
}

function renderRepeated(node, cards, ctx, x, y, width) {
    if (node.type === "photoGrid" || node.type === "photoFlow") {
        const columns = node.type === "photoFlow" ? cards.length : Math.min(node.columns, ctx.gridCardCount);
        const gap = node.gap || 0,
            cellW = Math.floor((width - gap * (columns - 1)) / columns);
        const startX =
            x +
            (cards.length < columns
                ? (width - (cards.length * cellW + gap * (cards.length - 1))) /
                  2
                : 0);
        const cells = cards.map((card, i) => {
            const colorIndex = (ctx.product.colors || []).findIndex(
                (color) => string(color.id) === string(card?.colorId),
            );
            const item = {
                ...ctx,
                card,
                index:
                    node.type === "photoGrid" && ctx.kind.kind === "color_product" && colorIndex >= 0
                        ? colorIndex
                        : ctx.photoIndexes?.get(card?.id) ?? ctx.index + i,
                targetSlot: node.slots?.[ctx.index + i],
            };
            const photo = renderPhoto(
                { type: "photo", slot: "$current", aspect: node.aspect },
                startX + i * (cellW + gap),
                y,
                cellW,
                item,
            );
            const captions = renderNodes(
                node.captions,
                startX + i * (cellW + gap),
                y + photo.h + 16,
                cellW,
                item,
            );
            return {
                elements: [
                    ...photo.elements,
                    ...renderGroups(
                        node.overlayGroups,
                        startX + i * (cellW + gap),
                        y,
                        cellW,
                        photo.h,
                        item,
                        photo.elements[0],
                    ),
                    ...captions.elements,
                ],
                h: photo.h + (captions.h ? 16 + captions.h : 0),
            };
        });
        return {
            elements: cells.flatMap((cell) => cell.elements),
            h: Math.max(0, ...cells.map((cell) => cell.h)),
        };
    }
    const card = cards[0];
    if (node.type === "photoStack") {
        const item = { ...ctx, card, index: ctx.photoIndexes?.get(card?.id) ?? ctx.index };
        if (ctx.kind.kind === "color_product" && ctx.catalog.designs[ctx.designId]?.choiceAxes) {
            const colorIndex = (ctx.product.colors || []).findIndex((color) => string(color.id) === string(card?.colorId));
            if (colorIndex >= 0) item.index = colorIndex;
        }
        return renderNodes(node.items, x, y, width, item);
    }
    const pad = node.padding || 0,
        photoW = Math.min(node.photoWidth, width - 2 * pad),
        gap = node.gap || 0;
    const reverse = node.alternate && ctx.index % 2;
    const photoX = x + pad + (reverse ? width - 2 * pad - photoW : 0);
    const textX = x + pad + (reverse ? 0 : photoW + gap);
    const photo = renderPhoto(
        {
            type: "photo",
            slot: "$current",
            aspect: "1/1",
            radius: node.photoRadius,
        },
        photoX,
        y + pad,
        photoW,
        { ...ctx, card },
    );
    const copy = renderNodes(
        node.items,
        textX,
        y + pad,
        width - 2 * pad - photoW - gap,
        { ...ctx, card },
    );
    const offset =
        node.valign === "center" ? Math.max(0, (photo.h - copy.h) / 2) : 0;
    const h = Math.max(photo.h, copy.h) + pad * 2;
    return {
        elements: [
            ...(node.fill
                ? [
                      {
                          type: "shape",
                          shape: "rect",
                          x,
                          y,
                          w: width,
                          h,
                          fill: ctx.tokens.colors[node.fill],
                          radius: 0,
                      },
                  ]
                : []),
            ...photo.elements,
            ...copy.elements.map((el) => ({ ...el, y: el.y + offset })),
        ],
        h,
    };
}

/** Taxonomy v4 only. Legacy assembly remains in mock/db.js until WO2. */
export function pointPhotosForRun(run, storyboard) {
    const photos = run.filter((card) => card.source !== "text");
    const lead = run.find(
        (card) => card.source === "text" && Array.isArray(card.pointItems),
    );
    if (!lead) return photos;
    const byId = new Map(storyboard.map((card) => [card.id, card]));
    const selected = lead.pointItems.flatMap((item, index) => {
        const source = byId.get(item.sourceBlockId);
        if (!source) return [];
        if (item.kind === "detail")
            return source.source === "ai" && source.shot === "detail" && source.pageSectionKey === lead.pageSectionKey
                ? [source]
                : [];
        if (
            item.kind !== "wornCrop" ||
            !isWornCropSource(source)
        )
            return [];
        return [
            {
                ...source,
                pageSectionKey: lead.pageSectionKey,
                pointItem: item,
                slotKey: `${lead.pageSectionKey.split(".").slice(1).join(".")}.detail${index + 1}`,
            },
        ];
    });
    const selectedIds = new Set(selected.map((card) => card.id));
    return [...selected, ...photos.filter((card) => !selectedIds.has(card.id))];
}

export function assemblePage({
    storyboard = [],
    cutResults = [],
    copyResults = [],
    product = {},
    analysis = {},
    pageStyle,
    baseSwatchId,
    copywriting = true,
    failedCuts = {},
    licenseNotice = null,
    profile = {},
    shopName,
    overlayRegions,
} = {}) {
    const catalog = pageTemplatesCatalog;
    if (
        !storyboard.length ||
        storyboard.some((card) => card.taxonomyVersion !== 4)
    )
        throw new Error("Expected a taxonomy v4 storyboard");
    const templateId = string(storyboard[0].pageSectionKey).split(".")[0];
    if (!getTemplate(templateId)) throw new Error("Unknown page template");
    const style = pageStyle ||
        analysis.pageStyle || { ...catalog.defaults, templateId };
    const tokens = resolvePageTokens(
        style,
        baseSwatchId ?? baseColorOf(product).swatchId,
    );
    if (tokens.template.id !== templateId)
        throw new Error("Mixed page templates");
    const runs = [],
        seen = new Set();
    for (const card of storyboard) {
        if (!string(card.pageSectionKey).startsWith(`${templateId}.`))
            throw new Error("Mixed page templates");
        const last = runs.at(-1);
        if (last?.[0].pageSectionKey === card.pageSectionKey) last.push(card);
        else {
            if (seen.has(card.pageSectionKey))
                throw new Error("Non-contiguous page section");
            seen.add(card.pageSectionKey);
            runs.push([card]);
        }
    }
    const cuts = Object.fromEntries(
        cutResults.map((cut) => [cut.blockId, cut]),
    );
    const copies = Object.fromEntries(
        copyResults.map((copy) => [
            copy.blockId,
            Object.fromEntries(
                (copy.texts || []).map((text) => [text.role, text.text]),
            ),
        ]),
    );
    const blocks = [];
    for (const run of runs) {
        const first = run[0],
            key = first.pageSectionKey,
            kind = catalog.kinds[first.pageSectionKind];
        const section = sectionDefinition(
            tokens.template,
            key,
            first.pageSectionKind,
            first.pageSectionUsage,
            first.pageDesignId,
        );
        let designId = first.pageDesignId || section.designId;
        const photos = pointPhotosForRun(run, storyboard);
        if (!photos.length && section.fallbackDesignId)
            designId = section.fallbackDesignId;
        const design = catalog.designs[designId];
        if (
            !kind ||
            design?.kind !== first.pageSectionKind ||
            run.some(
                (card) =>
                    card.pageSectionKind !== first.pageSectionKind ||
                    card.pageDesignId !== first.pageDesignId,
            )
        )
            throw new Error("Invalid page section design");
        const renderSection = design.invert == null ? section
            : { ...section, block: { ...section.block, invert: design.invert } };
        const sectionStyle = sectionTokens(tokens, renderSection);
        const ctx = {
            catalog,
            storyboard,
            product,
            analysis,
            profile,
            shopName: typeof shopName === "string" ? shopName.trim() : "",
            copywriting,
            key,
            section,
            kind,
            cuts,
            copy: copies[first.id] || {},
            sourceId: first.id,
            baseColor: baseColorOf(product),
            tokens: sectionStyle,
            photos,
            used: new Set(),
            failedCuts,
            align: design.align || tokens.template.align,
            onPhoto: catalog.overlayColors.light,
            index: 0,
            overlayRegions,
            designId,
            surfaceColor: renderSection.block?.invert
                ? tokens.colors.invertBg
                : sectionStyle.colors[design.surface || "canvas"],
        };
        const pad = design.padding || 0,
            width = catalog.canvasWidth - pad * 2;
        let y = design.top || 0,
            elements = [],
            part = 0,
            lastTextWidth;
        const flush = (bottom = design.bottom || 0) => {
            if (!elements.length) return;
            const id = `b${blocks.length}`;
            const gap = photos.length
                ? tokens.template.spacing.sectionGap
                : tokens.template.spacing.textSectionGap;
            const h =
                Math.max(y, ...elements.map((el) => el.y + el.h)) +
                bottom;
            blocks.push({
                id,
                name: templateSectionName(first),
                kind: first.sectionRole || "info",
                pageSectionKey: key,
                pageSectionKind: first.pageSectionKind,
                pageDesignId: designId,
                bg: renderSection.block?.invert
                    ? tokens.colors.invertBg
                    : sectionStyle.colors[design.surface || "canvas"],
                h,
                elements: elements.map((el, j) => ({
                    id: `${id}e${j}`,
                    ...el,
                })),
            });
            elements = [];
            y = 0;
            part += 1;
            // Final section spacing is applied once below, rather than between every photo.
            return gap;
        };
        for (const node of design.layout) {
            if (node.whenTemplate && node.whenTemplate !== tokens.template.id)
                continue;
            if (node.type === "photoFrame") {
                const selection = selectFramePhotos(selectCards(node, ctx), node.count);
                if (elements.some(el => el.type === 'image')) flush();
                const frame = renderFrameBand(selection, y, ctx);
                elements.push(...frame.elements); y += frame.h;
                const rows = selection.remainingGroups.flatMap(group => node.pattern === 'column'
                    ? group.map(card => ({ cards: [card] })) : photoFlowRows({ ...node, groupBy: 'none' }, group));
                const remainderNode = node.pattern === 'column'
                    ? { type: 'photoStack', items: [{ type: 'photo', slot: '$current', aspect: node.aspect, radius: 16 }, ...node.captions] }
                    : { ...node, type: 'photoFlow' };
                const photoIndexes = new Map(ctx.photos.map((card, index) => [card.id, index]));
                if (rows.length) { y += catalog.frameStyle.remainderGap; flush(); }
                rows.forEach((row, index) => {
                    if (index) flush();
                    const result = renderRepeated(remainderNode, row.cards, { ...ctx, photoIndexes, index: photoIndexes.get(row.cards[0].id) }, pad, y, width);
                    elements.push(...result.elements); y += result.h;
                    if (index + 1 < rows.length) y += tokens.template.spacing.photoGap;
                });
                if (rows.length && node.pattern !== 'column') y += node.gap || 0;
            } else if (node.type === "photoFlow") {
                const rows = photoFlowRows(node, selectCards(node, ctx));
                if (elements.some((el) => el.type === "image")) flush();
                rows.forEach((row, index) => {
                    if (index) flush();
                    const result = renderRepeated(node, row.cards, { ...ctx, index: row.index }, pad, y, width);
                    elements.push(...result.elements);
                    y += result.h;
                    if (index + 1 < rows.length) y += tokens.template.spacing.photoGap;
                });
                if (rows.length) y += node.gap || 0;
            } else if (["photoStack", "pointRows", "photoGrid"].includes(node.type)) {
                const selected = selectCards(node, ctx);
                const step = node.type === "photoGrid" ? node.columns : 1;
                if (elements.some((el) => el.type === "image")) flush();
                for (let i = 0; i < selected.length; i += step) {
                    if (i) flush(node.itemGap != null ? 0 : undefined);
                    const result = renderRepeated(
                        node,
                        selected.slice(i, i + step),
                        { ...ctx, index: i, gridCardCount: selected.length },
                        pad,
                        y,
                        width,
                    );
                    elements.push(...result.elements);
                    y += result.h;
                    if (i + step < selected.length)
                        y += node.itemGap ?? tokens.template.spacing.photoGap;
                }
                if (selected.length) y += node.gap || 0;
            } else {
                const inset = ["text", "factTable", "rule"].includes(node.type)
                    ? design.contentInset || 0
                    : 0;
                const result = renderNode(
                    node,
                    pad + inset,
                    y,
                    width - 2 * inset,
                    { ...ctx, lastTextWidth },
                );
                elements.push(...result.elements);
                const last = result.elements.findLast(
                    (el) => el.type === "text",
                );
                if (last)
                    lastTextWidth = measureTextWidth(
                        last.text,
                        last.w,
                        last.style.size,
                        catalog.textMetrics,
                        last.style.tracking,
                    );
                if (result.h) y += result.h + (node.gap || 0);
            }
        }
        for (const card of photos.filter((card) => !ctx.used.has(card.id))) {
            flush();
            const result = renderPhoto(
                {
                    type: "photo",
                    slot: "$current",
                    aspect: design.overflow.aspect,
                },
                pad,
                0,
                width,
                { ...ctx, card, index: part },
            );
            elements.push(...result.elements);
            y = result.h + (design.overflow.gap || 0);
        }
        flush();
        if (blocks.at(-1)?.pageSectionKey === key) {
            const block = blocks.at(-1);
            const gap = photos.length
                ? tokens.template.spacing.sectionGap
                : tokens.template.spacing.textSectionGap;
            if (block.bg.toLowerCase() !== catalog.canvasColor.toLowerCase())
                block.elements.push({
                    id: `${block.id}e${block.elements.length}`,
                    type: "shape",
                    shape: "rect",
                    x: 0,
                    y: block.h,
                    w: catalog.canvasWidth,
                    h: gap,
                    fill: catalog.canvasColor,
                    radius: 0,
                    locked: true,
                    pageSectionGap: true,
                });
            block.h += gap;
        }
    }
    const auto = buildAutoBlocks(product, {
        startIndex: blocks.length,
        licenseNotice,
    });
    const id = `b${blocks.length + auto.length}`;
    return [
        ...blocks,
        ...auto,
        {
            id,
            name: "공통 주의",
            kind: "common-notice",
            auto: true,
            bg: catalog.canvasColor,
            h: 120,
            elements: [
                {
                    id: `${id}e0`,
                    type: "text",
                    x: 60,
                    y: 40,
                    w: 880,
                    h: 40,
                    text: catalog.commonNotice,
                    style: {
                        font: "Pretendard",
                        size: 20,
                        color: tokens.colors.muted,
                        align: "center",
                    },
                },
            ],
        },
    ];
}

/** Exact bare-text regions, using the same layout and copy as final assembly. */
export function collectOverlayRegions(input) {
    const overlayRegions = [];
    assemblePage({ ...input, overlayRegions });
    return overlayRegions;
}
