import data from "../../../server/app/data/page_templates_v1.json" with { type: "json" };

export const SECTION_KINDS = Object.freeze([
    "first",
    "wear",
    "color_product",
    "fabric",
    "intro",
    "points",
    "for_you",
    "review",
    "faq",
    "sns",
]);
const FONTS = new Set([
    "Pretendard",
    "Cormorant",
    "Roboto Mono",
    "Cal Sans",
    "Gowun Dodum",
    "Gowun Batang",
]);
const FACT_PATHS = new Set([
    "product.name",
    "product.colors[].name",
    "product.colors[].swatchId",
    "analysis.materials",
    "analysis.fit",
    "analysis.detailRecommendations.candidates[].label",
    "analysis.featureCopy",
    "analysis.partDescriptionCopy",
    "analysis.mdComment",
    "profiles.display_name",
    "mall_connections.shop_name",
]);
const COLORS = new Set([
    "ink",
    "muted",
    "accentInk",
    "accentBg",
    "accentLine",
    "panel",
    "invertBg",
    "invertInk",
    "canvas",
    "onPhoto",
    "summaryBg",
    "pointBg",
    "framePaper",
    "frameInk",
]);
const STYLES = new Set(["titleKo", "labelEn", "body", "small", "num"]);
const LAYOUT_TYPES = new Set([
    "text",
    "photo",
    "photoStack",
    "photoGrid",
    "photoFlow",
    "photoFrame",
    "overlay",
    "pointRows",
    "list",
    "factTable",
    "panel",
    "rule",
    "swatchRow",
]);
const HEX = /^#[\da-f]{6}$/i;
const positive = (value) => Number.isFinite(value) && value > 0;
const opacity = (value) => Number.isFinite(value) && value >= 0 && value <= 1;
const aspectRatio = (value) => typeof value === 'string'
    && /^\d+(?:\.\d+)?\/\d+(?:\.\d+)?$/.test(value)
    && value.split('/').every(part => Number(part) > 0);
const CHOICE_LAYOUTS = {
    wear: ['column', 'twoColumn', 'heroPair'],
    color_product: ['column', 'grid'],
    points: ['column', 'below'],
};

export function walkLayout(nodes, visit) {
    for (const node of Array.isArray(nodes) ? nodes : []) {
        visit(node);
        for (const key of ["nodes", "items", "captions"])
            walkLayout(node[key], visit);
        for (const group of [
            ...(node.groups || []),
            ...(Array.isArray(node.columns) ? node.columns : []),
            ...(node.overlayGroups || []),
        ])
            { walkLayout(group.nodes, visit); walkLayout(group.faceFallback?.nodes, visit); }
    }
}

const rolePattern = (role) =>
    String(role)
        .replace(/\{direction\}/g, "front")
        .replace(/\.\d+(?=\.|$)/g, ".n");
export function textSlotForRole(kind, role) {
    const pattern = rolePattern(role);
    return kind?.textSlots?.find((slot) => slot.copyRole === pattern);
}

/** Both runtimes reject an invalid catalog before serving any page. */
export function validateCatalog(catalog) {
    const errors = [];
    const check = (condition, message) => {
        if (!condition) errors.push(message);
    };
    check(
        catalog?.schemaVersion === 1 && catalog?.canvasWidth === 1000,
        "schemaVersion/canvasWidth",
    );
    check(HEX.test(catalog?.canvasColor), "canvas color");
    const frameStyle = catalog?.frameStyle || {};
    check(['bandPadding', 'cardWidth', 'paddingTop', 'paddingX', 'paddingBottom', 'photoGap', 'photoTop', 'footerGap', 'remainderGap', 'textSize', 'textLineHeight', 'textWeight']
        .every(key => positive(frameStyle[key]))
        && frameStyle.cardWidth <= catalog.canvasWidth && frameStyle.paddingX * 2 < frameStyle.cardWidth
        && Number.isFinite(frameStyle.textTrackingEm) && Number.isFinite(frameStyle.blur) && frameStyle.blur >= 0 && frameStyle.blur <= 100
        && aspectRatio(frameStyle.fallbackAspect)
        && ['paper', 'ink'].every(key => COLORS.has(frameStyle[key]) && HEX.test(catalog?.neutralColors?.[frameStyle[key]])), 'frame style');
    check(
        Object.keys(catalog?.swatchLabels || {}).every((key) =>
            HEX.test(catalog?.swatchHex?.[key]),
        ),
        "swatch colors",
    );
    check(
        ["emo", "show"].includes(catalog?.defaults?.templateId) &&
            typeof catalog?.defaults?.fontStyle === 'string' &&
            Object.hasOwn(catalog?.fontStyles || {}, catalog.defaults.fontStyle) &&
            ["mono", "point"].includes(catalog?.defaults?.colorMode),
        "default page style",
    );
    const plate = catalog.firstTitlePlate || {};
    const bandValid = band => Array.isArray(band) && band.length === 2 && band.every(Number.isFinite)
        && band[0] >= 0 && band[1] <= 1 && band[0] < band[1];
    check(HEX.test(catalog.neutralColors?.[plate.fillToken] || '') && opacity(plate.opacity)
        && ['paddingX', 'paddingTop', 'paddingBottom', 'threshold'].every(key => positive(plate[key]))
        && opacity(plate.nearBestRatio) && opacity(plate.maxGarmentOverlap)
        && JSON.stringify(plate.preference) === JSON.stringify(['center', 'bottom', 'top'])
        && JSON.stringify(plate.platePreference) === JSON.stringify(['center', 'bottom'])
        && bandValid(plate.fallbackGarmentBand)
        && ['top', 'outer', 'bottom', 'dress'].every(kind => ['full', 'medium'].every(shot => bandValid(plate.garmentBands?.[kind]?.[shot]))), 'first title plate');
    const band = catalog?.firstTitleBand || {};
    check(
        [band.left, band.right, band.top, band.bottom].every(
            (v) => Number.isFinite(v) && v >= 0 && v <= 1,
        ) &&
            band.left < band.right &&
            band.top < band.bottom,
        "first title band",
    );
    const kinds = catalog?.kinds || {};
    check(
        Object.keys(kinds).length === 10 &&
            SECTION_KINDS.every((key) => kinds[key]),
        "ten section kinds",
    );
    for (const [key, kind] of Object.entries(kinds)) {
        check(SECTION_KINDS.includes(key) && kind.kind === key, `kind ${key}`);
        check(
            kind.repeatable === (key === "wear"),
            `repeatable ${key}`,
        );
        check(
            ["photo", "detail", "describe", "social"].includes(kind.menuGroup),
            `menuGroup ${key}`,
        );
        check(
            ["only", "lead", "none"].includes(kind.storyboardTextCard),
            `text card ${key}`,
        );
        check(
            key === "wear"
                ? JSON.stringify(kind.usages) === '["styling","fit"]'
                : kind.usages == null,
            `usage ${key}`,
        );
        const roles = new Set();
        for (const slot of kind.textSlots || []) {
            check(
                !roles.has(slot.copyRole),
                `duplicate copyRole ${key}.${slot.copyRole}`,
            );
            roles.add(slot.copyRole);
            check(
                ["static", "fact", "ai", "aiDraft", "seller+ai"].includes(
                    slot.source,
                ),
                `source ${key}.${slot.copyRole}`,
            );
            check(
                STYLES.has(slot.style) && COLORS.has(slot.color),
                `text token ${key}.${slot.copyRole}`,
            );
            if (slot.source === "fact")
                check(
                    FACT_PATHS.has(slot.factPath) ||
                        (slot.copyRole === "notice" &&
                            slot.factPath === null &&
                            slot.unavailable === true),
                    `factPath ${key}.${slot.copyRole}`,
                );
            if (["ai", "aiDraft", "seller+ai"].includes(slot.source))
                check(
                    positive(slot.maxChars),
                    `maxChars ${key}.${slot.copyRole}`,
                );
            if (slot.itemCount)
                check(
                    positive(slot.itemCount.default) &&
                        slot.itemCount.max >= slot.itemCount.default,
                    `itemCount ${key}.${slot.copyRole}`,
                );
        }
    }
    const designs = catalog?.designs || {};
    for (const [id, design] of Object.entries(designs)) {
        const kind = kinds[design.kind];
        check(
            design.id === id && kind && Array.isArray(design.layout),
            `design ${id}`,
        );
        check(typeof design.displayName === 'string' && design.displayName.trim().length > 0
            && design.displayName.length <= 24, `design displayName ${id}`);
        check(design.kind === 'wear' ? ['styling', 'fit'].includes(design.usage) : design.usage == null, `design usage ${id}`);
        if (Object.hasOwn(design, 'pickerHidden'))
            check(typeof design.pickerHidden === 'boolean', `pickerHidden ${id}`);
        if (Object.hasOwn(design, 'invert'))
            check(typeof design.invert === 'boolean', `design invert ${id}`);
        if (Object.hasOwn(design, 'choiceAxes')) {
            const axes = design.choiceAxes;
            check(CHOICE_LAYOUTS[design.kind]?.includes(axes?.layout?.id)
                && ['basic', 'card', 'frame'].includes(axes?.decoration?.id)
                && [axes?.layout?.name, axes?.decoration?.name].every(name => typeof name === 'string' && name.trim()),
            `choice axes ${id}`);
        }
        const frameNodes = design.layout?.filter(node => node.type === 'photoFrame') || [];
        if (design.choiceAxes?.decoration?.id === 'frame' || Object.hasOwn(design, 'framePhotos') || frameNodes.length)
            check(design.kind === 'wear' && design.usage === 'styling' && design.choiceAxes?.decoration?.id === 'frame'
                && [1, 2, 3].includes(design.framePhotos) && frameNodes.length === 1
                && frameNodes[0].count === design.framePhotos && frameNodes[0].pattern === design.choiceAxes?.layout?.id
                && design.minPhotos >= Math.max(design.framePhotos, { column: 1, twoColumn: 2, heroPair: 3 }[design.choiceAxes?.layout?.id]), `frame design ${id}`);
        for (const key of ['minPhotos', 'minColors'])
            if (Object.hasOwn(design, key))
                check(Number.isInteger(design[key]) && design[key] > 0, `design minimum ${id}.${key}`);
        const roles = new Set(),
            photos = new Set(),
            groupIds = new Set();
        walkLayout(design.layout, (node) => {
            for (const group of [
                ...(node.groups || []),
                ...(node.overlayGroups || []),
            ]) {
                check(
                    typeof group.id === "string" &&
                        group.id.length > 0 &&
                        !groupIds.has(group.id),
                    `overlay group ${id}.${group.id}`,
                );
                groupIds.add(group.id);
                check(
                    group.anchor == null ||
                        ["top", "bottom", "center"].includes(group.anchor),
                    `overlay anchor ${id}.${group.id}`,
                );
                check(
                    group.band == null || group.band === "firstTitleBand",
                    `overlay band ${id}.${group.id}`,
                );
                if (group.textColor != null)
                    check(
                        typeof group.textColor === "string" &&
                            (HEX.test(group.textColor) ||
                                COLORS.has(group.textColor) && group.textColor !== "onPhoto"),
                        `overlay text color ${id}.${group.id}`,
                    );
                if (group.positionFallback != null) {
                    const policy = group.positionFallback;
                    check(
                        ["P01-D09", "P01-D09-G"].includes(id) &&
                            Array.isArray(policy.anchors) &&
                            policy.anchors.length === 3 &&
                            policy.anchors.every(
                                (anchor, index) =>
                                    anchor ===
                                    ["center", "top", "bottom"][index],
                            ) &&
                            Number.isFinite(policy.edgeInsetRatio) &&
                            policy.edgeInsetRatio >= 0 &&
                            policy.edgeInsetRatio < 0.5,
                        `overlay position ${id}.${group.id}`,
                    );
                }
            }
            for (const group of node.groups || []) if (group.faceFallback != null) {
                const fallback = group.faceFallback;
                check(id === 'T08-D07' && group.id === 'heading'
                    && ['top', 'padding', 'gap'].every(key => positive(fallback[key]))
                    && Array.isArray(fallback.nodes), `face fallback ${id}`);
            }
            if (Object.hasOwn(node, 'prefix'))
                check(node.type === 'text' && ['Q.', 'A.'].includes(node.prefix) && positive(node.prefixWidth), `text prefix ${id}`);
            if (Object.hasOwn(node, 'liningNumbers'))
                check(node.type === 'text' && typeof node.liningNumbers === 'boolean', `lining numbers ${id}`);
            if (node.type === 'swatchRow')
                check(design.kind === 'color_product' && positive(node.size) && Number.isFinite(node.itemGap) && node.itemGap >= 0, `swatch row ${id}`);
            check(LAYOUT_TYPES.has(node.type), `layout ${id}.${node.type}`);
            if (node.type === 'photoFrame')
                check(design.layout.includes(node) && design.kind === 'wear' && design.usage === 'styling'
                    && design.choiceAxes?.decoration?.id === 'frame' && design.framePhotos === node.count
                    && node.slot === '*' && [1, 2, 3].includes(node.count)
                    && ['column', 'twoColumn', 'heroPair'].includes(node.pattern)
                    && node.groupBy === 'spaceGroup' && node.aspect === '4/5'
                    && Number.isFinite(node.gap) && node.gap >= 0 && Array.isArray(node.captions), `photoFrame ${id}`);
            if (node.type === 'photoFlow')
                check(typeof node.slot === 'string' && node.slot.length > 0
                    && ['twoColumn', 'heroPair'].includes(node.pattern)
                    && ['spaceGroup', 'none'].includes(node.groupBy)
                    && aspectRatio(node.aspect)
                    && Number.isFinite(node.gap) && node.gap >= 0
                    && Array.isArray(node.captions), `photoFlow ${id}`);
            if (Object.hasOwn(node, 'fit'))
                check(node.type === 'photo' && ['contain', 'cover'].includes(node.fit), `photo fit ${id}`);
            if (Object.hasOwn(node, 'fallbackAspect'))
                check(node.type === 'photo' && node.aspect === 'source' && aspectRatio(node.fallbackAspect), `fallback aspect ${id}`);
            if (node.fit === 'contain')
                check(!Object.hasOwn(node, 'cropTop'), `contain crop ${id}`);
            if (node.scrim != null) {
                const paint = node.scrim;
                check(
                    node.type === "overlay" && paint && typeof paint === "object" &&
                        (HEX.test(paint.color) || HEX.test(catalog.neutralColors[paint.color])) && opacity(paint.opacity) &&
                        (paint.heightRatio == null || positive(paint.heightRatio) && paint.heightRatio <= 1) &&
                        (paint.gradient == null || typeof paint.gradient === "boolean"),
                    `overlay scrim ${id}`,
                );
            }
            if (node.fillOpacity != null)
                check(["panel", "list"].includes(node.type) && opacity(node.fillOpacity), `fill opacity ${id}`);
            if (node.itemGap != null)
                check(["list", "photoStack", "swatchRow"].includes(node.type) && Number.isFinite(node.itemGap) && node.itemGap >= 0, `list item gap ${id}`);
            if (Object.hasOwn(node, 'includeLoose'))
                check(node.type === 'photoStack' && node.slot === '*' && typeof node.includeLoose === 'boolean', `include loose ${id}`);
            if (node.shadow != null) {
                const shadow = node.shadow;
                check(
                    ["panel", "list"].includes(node.type) && shadow && typeof shadow === "object" &&
                        [shadow.x, shadow.y, shadow.blur].every(Number.isFinite) && shadow.blur >= 0 &&
                        (HEX.test(shadow.color) || HEX.test(catalog.neutralColors[shadow.color])) && opacity(shadow.opacity),
                    `shape shadow ${id}`,
                );
            }
            if (node.singleLine != null)
                check(node.type === "text" && typeof node.singleLine === "boolean", `single line ${id}`);
            if (Object.hasOwn(node, 'weight'))
                check(node.type === 'text' && node.weight === 'light', `text weight ${id}`);
            if (Object.hasOwn(node, 'numberPrefix'))
                check(node.type === 'text' && typeof node.numberPrefix === 'boolean', `number prefix ${id}`);
            if (Object.hasOwn(node, 'trackingEm'))
                check(node.type === 'text' && Number.isFinite(node.trackingEm) && node.trackingEm > -1 && node.trackingEm <= 1, `text tracking ${id}`);
            if (Object.hasOwn(node, 'lineHeight'))
                check(node.type === 'text' && positive(node.lineHeight), `text line height ${id}`);
            if (Object.hasOwn(node, 'copyPolicy'))
                check(node.type === 'text' && node.copyRole === 'items.n.desc' && node.copyPolicy === 'partOnly', `copy policy ${id}`);
            if (node.titleWrap != null) {
                const policy = node.titleWrap;
                check(
                    ["P01-D09", "P01-D09-G"].includes(id) &&
                        node.type === "text" &&
                        node.copyRole === "name" &&
                        Number.isFinite(policy.photoWidthRatio) &&
                        policy.photoWidthRatio > 0 &&
                        policy.photoWidthRatio <= 1 &&
                        Array.isArray(policy.sizes) &&
                        policy.sizes.length > 0 &&
                        policy.sizes[0] === node.size &&
                        policy.sizes.every(
                            (size, index) =>
                                Number.isInteger(size) &&
                                size > 0 &&
                                (!index || size < policy.sizes[index - 1]),
                        ) &&
                        Number.isInteger(policy.maxLines) &&
                        policy.maxLines > 0 &&
                        Number.isInteger(policy.minSizeMaxLines) &&
                        policy.minSizeMaxLines >= policy.maxLines,
                    `title wrap ${id}.${node.copyRole}`,
                );
            }
            if (node.matchText != null)
                check(
                    ["P01-D09", "P01-D09-G"].includes(id) &&
                        node.type === "rule" &&
                        node.matchText === "name" &&
                        node.align === "center" &&
                        positive(node.minW) &&
                        Number.isFinite(node.maxWidthRatio) &&
                        node.maxWidthRatio > 0 &&
                        node.maxWidthRatio <= 1,
                    `rule text width ${id}`,
                );
            if (node.copyRole) {
                check(
                    Boolean(textSlotForRole(kind, node.copyRole)),
                    `copyRole ${id}.${node.copyRole}`,
                );
                roles.add(rolePattern(node.copyRole));
            }
            if (node.slot && node.slot !== "$current") photos.add(node.slot);
            for (const slot of node.slots || []) photos.add(slot);
            for (const prop of ["color", "fill"])
                if (node[prop] != null)
                    check(
                        COLORS.has(node[prop]),
                        `color token ${id}.${node[prop]}`,
                    );
            if (node.style)
                check(STYLES.has(node.style), `font token ${id}.${node.style}`);
            if (node.columns != null && !Array.isArray(node.columns))
                check(positive(node.columns), `columns ${id}`);
            for (const key of ["w", "h", "size", "photoWidth", "minW", "maxW"])
                if (node[key] != null)
                    check(
                        positive(node[key]) ||
                            (key === "w" &&
                                node.type === "rule" &&
                                node.w === "text"),
                        `geometry ${id}.${key}`,
                    );
            if (node.padding != null) {
                const pads = Array.isArray(node.padding)
                    ? node.padding
                    : [node.padding];
                check(
                    (!Array.isArray(node.padding) ||
                        (pads.length === 2 &&
                            ["panel", "list"].includes(node.type))) &&
                        pads.every((v) => Number.isFinite(v) && v >= 0),
                    `geometry ${id}.padding`,
                );
            }
            if (node.hug != null)
                check(
                    typeof node.hug === "boolean" &&
                        ["panel", "list"].includes(node.type),
                    `hug ${id}`,
                );
            if (node.minW != null && node.maxW != null)
                check(node.minW <= node.maxW, `hug bounds ${id}`);
            if (node.hugMode != null)
                check(
                    node.type === "list" &&
                        ["each", "max"].includes(node.hugMode),
                    `hugMode ${id}`,
                );
            if (node.layout != null)
                check(
                    node.type === "factTable" &&
                        ["inline", "columns", "stacked"].includes(node.layout),
                    `table layout ${id}`,
                );
            if (node.marker != null)
                check(
                    node.type === "list" &&
                        ["dot", "square"].includes(node.marker),
                    `marker ${id}`,
                );
            if (node.valign != null)
                check(
                    node.type === "pointRows" && node.valign === "center",
                    `valign ${id}`,
                );
            if (node.swatchDot != null)
                check(
                    node.type === "text" && typeof node.swatchDot === "boolean",
                    `swatchDot ${id}`,
                );
            if (node.splitParagraphs != null)
                check(
                    node.type === "panel" &&
                        node.nodes?.some(
                            (child) => child.copyRole === node.splitParagraphs,
                        ),
                    `splitParagraphs ${id}`,
                );
            for (const key of ["x", "y", "gap", "radius", "photoRadius"])
                if (node[key] != null)
                    check(
                        Number.isFinite(node[key]) && node[key] >= 0,
                        `geometry ${id}.${key}`,
                    );
            if (node.aspect != null && node.aspect !== "source")
                check(
                    aspectRatio(node.aspect),
                    `aspect ${id}`,
                );
            if (node.cropTop != null)
                check(
                    Number.isFinite(node.cropTop) &&
                        node.cropTop >= 0 &&
                        node.cropTop < 1,
                    `cropTop ${id}`,
                );
        });
        for (const role of kind?.requiredCopyRoles || [])
            check(roles.has(role), `required copyRole ${id}.${role}`);
        for (const slot of kind?.requiredPhotoSlots || [])
            check(photos.has(slot), `required photo ${id}.${slot}`);
        check(design.overflow?.type === "photoStack", `overflow ${id}`);
    }
    const ids = new Set();
    for (const template of catalog?.templates || []) {
        check(
            ["emo", "show"].includes(template.id) &&
                !ids.has(template.id),
            `template ${template.id}`,
        );
        ids.add(template.id);
        check(
            ["titleKo", "labelEn", "body"].every(
                (key) => template.fonts?.[key],
            ),
            `required fonts ${template.id}`,
        );
        for (const ref of Object.values(template.fonts || {}))
            check(FONTS.has(ref.font), `font ${template.id}`);
        check(
            ["ink", "muted", "accentInk", "accentLine", "panel"].every((key) =>
                HEX.test(template.colors?.[key]),
            ),
            `required colors ${template.id}`,
        );
        for (const value of Object.values(template.colors || {}))
            check(value === null || HEX.test(value), `color ${template.id}`);
        check(
            template.id === "show" || !template.colors?.invertBg,
            `invert skin ${template.id}`,
        );
        const keys = new Set(),
            counts = {};
        for (const [index, section] of (template.sections || []).entries()) {
            const kind = kinds[section.kind];
            check(
                Boolean(kind),
                `section kind ${template.id}.${section.instanceKey}`,
            );
            check(
                section.instanceKey && !keys.has(section.instanceKey),
                `instanceKey ${template.id}.${section.instanceKey}`,
            );
            keys.add(section.instanceKey);
            counts[section.kind] = (counts[section.kind] || 0) + 1;
            check(
                kind?.repeatable || counts[section.kind] <= 1,
                `repeated kind ${template.id}.${section.kind}`,
            );
            if (section.kind === "first")
                check(index === 0, `first position ${template.id}`);
            check(
                section.kind === "wear"
                    ? kind?.usages.includes(section.usage)
                    : section.usage == null,
                `section usage ${template.id}.${section.instanceKey}`,
            );
            for (const designId of [
                section.designId,
                section.fallbackDesignId,
                ...(section.alternatives || []),
            ].filter(Boolean))
                check(
                    designs[designId]?.kind === section.kind
                        && (section.kind !== 'wear' || designs[designId]?.usage === section.usage),
                    `design kind ${template.id}.${designId}`,
                );
            check(
                !section.block?.invert || template.id === "show",
                `invert block ${template.id}`,
            );
            validatePhotoSlots(template.id, section, check);
        }
        check(
            counts.first === 1 && template.sections?.[0]?.kind === "first",
            `one first ${template.id}`,
        );
        for (const key of SECTION_KINDS) {
            const placement = template.addPlacements?.[key];
            check(
                designs[placement?.designId]?.kind === key,
                `add placement ${template.id}.${key}`,
            );
            for (const id of [
                placement?.fallbackDesignId,
                ...(placement?.designIds || []),
            ].filter(Boolean))
                check(
                    designs[id]?.kind === key,
                    `add design kind ${template.id}.${id}`,
                );
            check(
                placement?.after == null ||
                    keys.has(placement.after) ||
                    ["lastSameKind", "lastSameUsage"].includes(placement.after),
                `placement target ${template.id}.${key}`,
            );
            if (placement)
                validatePhotoSlots(
                    template.id,
                    { ...placement, kind: key },
                    check,
                );
        }
    }
    check(ids.size === 2, "two templates");
    check(
        Object.keys(catalog?.fontStyles || {}).length > 0,
        "font styles",
    );
    for (const refs of Object.values(catalog?.fontStyles || {}))
        for (const ref of Object.values(refs))
            if (ref) check(FONTS.has(ref.font), `font style ${ref.font}`);
    for (const swatch of [
        "white",
        "gray",
        "black",
        "ivory",
        "beige",
        "brown",
        "purple",
        "red",
        "yellow",
        "green",
        "blue",
        "navy",
        "pink",
        "null",
    ]) {
        const palette = catalog?.pointPalette?.[swatch];
        check(
            palette &&
                ["accentInk", "accentBg", "accentLine"].every((key) =>
                    HEX.test(palette[key]),
                ),
            `palette ${swatch}`,
        );
    }
    for (const key of [
        "unitsPerEm",
        "ascii",
        "uppercaseAscii",
        "space",
        "wide",
        "lineHeight",
        "titleLineHeight",
        "labelLineHeight",
        "minImageHeight",
        "maxImageHeight",
        "maxBlockHeight",
    ])
        check(positive(catalog?.textMetrics?.[key]), `text metric ${key}`);
    if (errors.length)
        throw new Error(`Invalid page template catalog: ${errors.join("; ")}`);
    return catalog;
}

function validatePhotoSlots(templateId, section, check) {
    const keys = new Set();
    check(
        !["intro", "for_you"].includes(section.kind) ||
            !section.photoSlots?.length,
        `text-only photos ${templateId}.${section.kind}`,
    );
    for (const slot of section.photoSlots || []) {
        check(
            slot.slotKey && !keys.has(slot.slotKey),
            `photo slot ${templateId}.${slot.slotKey}`,
        );
        keys.add(slot.slotKey);
        check(
            ["generate", "reuse"].includes(slot.fill),
            `fill ${templateId}.${slot.slotKey}`,
        );
        check(
            ["base", "extra", "each", "cycle"].includes(slot.color),
            `photo color ${templateId}.${slot.slotKey}`,
        );
        if (typeof slot.count === "number")
            check(
                positive(slot.count),
                `photo count ${templateId}.${slot.slotKey}`,
            );
        else
            check(
                slot.count &&
                    typeof slot.count === "object" &&
                    !Array.isArray(slot.count) &&
                    positive(slot.count.max) &&
                    Object.entries(slot.count).every(
                        ([key, value]) =>
                            key === "detailPool" || positive(value),
                    ),
                `photo count ${templateId}.${slot.slotKey}`,
            );
        if (slot.fill === "reuse") {
            const ref = slot.reuseFrom || {};
            check(
                ((section.kind === "first" && slot.slotKey === "ghost" ||
                    section.kind === "fabric" && slot.slotKey === "product") &&
                    slot.contentRole === "productOverview" &&
                    ref.kind === "color_product" &&
                    ref.slot === "front" &&
                    ref.color === "base") ||
                    (section.kind === "sns" &&
                        ref.kind === "wear" &&
                        ["styling", "fit"].includes(ref.usage) &&
                        ref.slot === "g" &&
                        [0, 1].includes(ref.index)),
                `reuse ${templateId}.${slot.slotKey}`,
            );
            check(
                slot.recipe == null,
                `reuse recipe ${templateId}.${slot.slotKey}`,
            );
            continue;
        }
        const recipe = slot.recipe || {};
        check(
            section.kind !== "sns",
            `SNS must reuse ${templateId}.${slot.slotKey}`,
        );
        const worn = ["styling", "horizon"].includes(recipe.cutType);
        const allowed = ["hero", "benefit", "coordination", "fit"].includes(
            slot.contentRole,
        )
            ? ["styling", "horizon"]
            : ["productOverview", "detail"].includes(slot.contentRole)
              ? ["product"]
              : [];
        check(
            allowed.includes(recipe.cutType) &&
                recipe.allowedCutTypes?.length > 0 &&
                recipe.allowedCutTypes.every((value) =>
                    allowed.includes(value),
                ) &&
                recipe.allowedCutTypes.includes(recipe.cutType),
            `cutType ${templateId}.${slot.slotKey}`,
        );
        const shots = worn
            ? ["full", "medium"]
            : slot.contentRole === "detail"
              ? ["detail"]
              : ["ghost"];
        check(
            shots.includes(recipe.shot) &&
                recipe.allowedShots?.length > 0 &&
                recipe.allowedShots.every((value) => shots.includes(value)) &&
                recipe.allowedShots.includes(recipe.shot),
            `shot ${templateId}.${slot.slotKey}`,
        );
        check(
            (worn ? ["front", "side", "back"] : ["front", "back"]).includes(
                recipe.direction,
            ),
            `direction ${templateId}.${slot.slotKey}`,
        );
        check(
            recipe.set == null ||
                (worn &&
                    ["styling", "rotation", "sequence"].includes(recipe.set)),
            `set ${templateId}.${slot.slotKey}`,
        );
        check(
            recipe.detailPool == null ||
                (slot.contentRole === "detail" &&
                    ["fabric", "points"].includes(recipe.detailPool)),
            `detailPool ${templateId}.${slot.slotKey}`,
        );
        check(
            recipe.examplePrefer == null ||
                recipe.examplePrefer === "bottomClear",
            `examplePrefer ${templateId}.${slot.slotKey}`,
        );
    }
}

export const pageTemplatesCatalog = validateCatalog(data);
export function getTemplate(id, catalog = pageTemplatesCatalog) {
    return catalog.templates.find((template) => template.id === id);
}

/** Added wear runs are independent of a template's initial continuous studio run. */
export function wearAdditionDefinition(templateOrId, usage) {
    const template = typeof templateOrId === 'string' ? getTemplate(templateOrId) : templateOrId;
    const resolved = template || getTemplate(pageTemplatesCatalog.defaults.templateId);
    const source = usage === 'fit'
        ? resolved.sections.find(section => section.kind === 'wear'
            && section.photoSlots?.some(slot => slot.recipe?.set === 'rotation'))
        : resolved.addPlacements.wear;
    return { ...structuredClone(source), kind: 'wear', usage: usage === 'fit' ? 'fit' : 'styling' };
}

export function sectionDefinition(template, key, kind, usage, designId) {
    template ||= getTemplate(pageTemplatesCatalog.defaults.templateId);
    const instanceKey = String(key || "").split('.').slice(1).join('.');
    const section = (
        template.sections.find(
            (section) => section.instanceKey === instanceKey,
        ) || {
            ...(kind === 'wear' ? wearAdditionDefinition(template, usage) : template.sections.find(
                (section) =>
                    section.kind === kind &&
                    (kind !== "wear" || section.usage === usage),
            ) ||
                template.addPlacements[kind] ||
                {}),
            instanceKey,
            kind,
            ...(usage ? { usage } : {}),
        }
    );
    if (!designId || designId === section.designId) return section;
    const required = new Set();
    walkLayout(pageTemplatesCatalog.designs[designId]?.layout, node => {
        if (node.slot) required.add(node.slot);
        for (const slot of node.slots || []) required.add(slot);
    });
    const slots = [...(section.photoSlots || [])];
    for (const candidate of pageTemplatesCatalog.templates.flatMap(item => item.sections).filter(item => item.kind === kind)) {
        for (const slot of candidate.photoSlots || []) {
            if (slot.fill === 'reuse' && required.has(slot.slotKey) && !slots.some(item => item.slotKey === slot.slotKey)) slots.push(slot);
        }
    }
    return { ...section, photoSlots: slots };
}
