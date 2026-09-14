import test from "node:test";
import assert from "node:assert/strict";

import {
    eventually,
    modelComponentHarness,
} from "./helpers/facemarketHarness.mjs";

function navLabels(tree) {
    const nav = tree.props.children.find((node) => node?.type === "nav");
    return nav.props.children.flat().map((node) => node.props.children);
}

async function headerHarness(listMyModels) {
    const harness = await modelComponentHarness({
        entry: "/src/features/facemarket-landing/LandingHeader.jsx",
        exportName: "LandingHeader",
        initialStates: [],
        honorHookDependencies: true,
        api: { listMyModels },
    });
    harness.runtime.session = { user: { id: "u1" } };
    harness.runtime.location = { pathname: "/models", search: "" };
    return harness;
}

test("로그인 사용자는 모델 상태가 확인되기 전에 모델 지원을 보지 않아요", async () => {
    let finishLookup;
    const models = new Promise((resolve) => {
        finishLookup = resolve;
    });
    const harness = await headerHarness(() => models);
    try {
        const pending = harness.render();
        harness.runtime.effects.forEach((effect) => effect());
        assert.deepEqual(navLabels(pending), ["모델 리스트", "마이페이지"]);

        finishLookup([{ id: "m1", status: "verified" }]);
        await new Promise((resolve) => setImmediate(resolve));
        assert.deepEqual(navLabels(harness.render()), [
            "모델 리스트",
            "마이페이지",
        ]);
    } finally {
        await harness.close();
    }
});

test("인증 상태가 확인되기 전에는 모델 지원을 보이지 않아요", async () => {
    const harness = await headerHarness(async () => []);
    try {
        harness.runtime.session = null;
        harness.runtime.loading = true;
        assert.deepEqual(navLabels(harness.render()), [
            "모델 리스트",
            "마이페이지",
        ]);
    } finally {
        await harness.close();
    }
});

test("모델 조회가 실패해도 로그인 사용자에게 모델 지원을 보이지 않아요", async () => {
    const harness = await headerHarness(async () => {
        throw new Error("offline");
    });
    try {
        harness.render();
        harness.runtime.effects.forEach((effect) => effect());
        await new Promise((resolve) => setImmediate(resolve));
        assert.deepEqual(navLabels(harness.render()), [
            "모델 리스트",
            "마이페이지",
        ]);
    } finally {
        await harness.close();
    }
});

test("로그인 사용자가 미등록으로 확인되면 모델 지원을 보여요", async () => {
    const harness = await headerHarness(async () => []);
    try {
        harness.render();
        harness.runtime.effects.forEach((effect) => effect());
        let rendered;
        await eventually(() => {
            rendered = harness.render();
            return navLabels(rendered)[0] === "모델 지원";
        }, "model application nav must appear after confirming no registered model");
        assert.deepEqual(navLabels(rendered), [
            "모델 지원",
            "모델 리스트",
            "마이페이지",
        ]);
    } finally {
        await harness.close();
    }
});
