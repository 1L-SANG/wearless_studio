import { Fragment } from "react";
import { pageTemplatesCatalog } from "../../lib/pageTemplates/catalog.js";

export function hasTemplateContrastLines(el) {
    const source = el.contrastFor;
    return Boolean(
        source?.style &&
        el.contrastLines?.length &&
        source.text === el.text &&
        ["x", "y", "w"].every((key) => source[key] === el[key]) &&
        Object.keys(source.style).every(
            (key) => source.style[key] === el.style?.[key],
        ) &&
        Object.keys(el.style || {}).every(
            (key) => source.style[key] === el.style[key],
        ),
    );
}

// The export path clones this exact DOM, including per-line colors and shade classes.
export function TemplateContrastText({ element }) {
    return element.contrastLines.map((line, index) => (
        <Fragment key={index}>
            {index ? <br /> : null}
            <span
                className={`template-contrast-line ${line.color === pageTemplatesCatalog.overlayColors.dark ? "is-dark" : "is-light"}${line.shade ? " has-shade" : ""}`}
            >
                {line.text || "\u00a0"}
            </span>
        </Fragment>
    ));
}
