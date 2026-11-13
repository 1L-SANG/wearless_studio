import { uid } from "../ids.js";
import { MEASUREMENT_LABELS } from "../measurementSchema.js";
import { pageTemplatesCatalog } from "./catalog.js";

/** Existing automatic footer geometry, also used by the legacy mock builder. */
export function buildAutoBlocks(
    product = {},
    { startIndex, licenseNotice } = {},
) {
    const data = pageTemplatesCatalog.autoBlocks;
    const blocks = [data.size, data.care, data.notice].map((block) => ({
        ...block,
        elements: block.elements.map((element) => ({
            ...element,
            style: { ...element.style },
        })),
    }));
    (product.measurements || []).slice(0, 4).forEach((measurement, index) => {
        const x = 60 + index * 232;
        blocks[0].elements.push(
            {
                type: "text",
                x,
                y: 168,
                w: 200,
                h: 24,
                text:
                    (Number.isInteger(startIndex)
                        ? data.measurementLabels
                        : MEASUREMENT_LABELS)[measurement.key] ||
                    measurement.key,
                style: { ...data.measurementLabelStyle },
            },
            {
                type: "text",
                x,
                y: 194,
                w: 200,
                h: 48,
                text:
                    measurement.value != null ? `${measurement.value} cm` : "—",
                style: { ...data.measurementValueStyle },
            },
        );
    });
    if (licenseNotice) {
        const notice = blocks[2],
            element = notice.elements[0];
        const all =
            licenseNotice.totalCuts > 0 &&
            licenseNotice.faceCuts >= licenseNotice.totalCuts;
        element.text = (all ? data.licensedAll : data.licensedPartial)
            .replace("{model_name}", licenseNotice.modelName || "익명")
            .replace("{license_id}", licenseNotice.licenseId || "");
        element.h = all ? 80 : 100;
        if (licenseNotice.licenseId) {
            const badgeY = 48 + element.h + 28;
            notice.elements.push(
                {
                    type: "text",
                    x: 60,
                    y: badgeY,
                    w: 880,
                    h: 30,
                    text: "✅ 검증된 실제 모델",
                    style: { ...data.badgeStyle },
                },
                {
                    type: "license-verify",
                    x: 380,
                    y: badgeY + 48,
                    w: 240,
                    h: 200,
                    licenseId: licenseNotice.licenseId,
                },
            );
            notice.licenseId = licenseNotice.licenseId;
        }
    }
    return blocks.map((block, index) => {
        const deterministic = Number.isInteger(startIndex);
        const id = deterministic ? `b${startIndex + index}` : uid("b");
        return {
            id,
            ...block,
            elements: block.elements.map((element, j) => ({
                id: deterministic ? `${id}e${j}` : uid("el"),
                ...element,
            })),
        };
    });
}
