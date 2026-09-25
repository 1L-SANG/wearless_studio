# Detail recommendation probe — 2026-09-25

Four AG-08 text/vision calls used existing uploaded originals; no image generation.
Requested model: `gemini-3.7-flash`; provider reported by the existing client: Gemini.
The client does not expose the returned model ID, so this is not a claim about an
independently verified returned model. The normal auxiliary timeout is 12 seconds.
Only named Gemini/model/budget environment keys were loaded in the child process.

| Prompt revision | Existing product | Elapsed | Result |
| --- | --- | ---: | --- |
| Initial | Pink lace-trim top | 8.250 s | Narrow chest lace/ribbon incorrectly remained a standalone candidate. |
| Initial | Green pointelle knit | 7.547 s | Closure, material, neckline candidates; no padded quota. |
| Usefulness gate | Pink lace-trim top | 7.922 s | One connected upper-bodice candidate contains neckline + chest trim/ribbon. No standalone narrow strip; hem remains context. |
| Usefulness gate | Crochet cardigan | 7.656 s | Material, functional closure, readable label remain standalone; decorative pocket appearance remains context. |

The revised prompt asks what additional buyer information a separate photograph
provides. A new closed enum `standaloneValue` supports deterministic demotion of
`appearance_only` to context, while material and structural evidence remain eligible.
No pixel-area threshold or lace blacklist is used. Existing highlighting output
remains independent of the recommended photograph subjects.

The pink upper-bodice candidate is still labeled construction by the model; the
observable improvement is its connected framing and removal of the isolated-strip
candidate. These two revision samples do not establish broad recommendation accuracy,
latency tails, or manufacturing-workmanship interpretation accuracy. No image was
generated from the new recommendations in this probe.

`recommendation-probe.json` preserves the initial results.
`recommendation-probe-v2.json` preserves the revised results. Both record original
paths and source hashes, exact normalized contracts, requested model/provider,
elapsed time, and prompt hashes. The full raw provider envelope was not captured;
the stored candidates are the validated server contract, not raw model output.
