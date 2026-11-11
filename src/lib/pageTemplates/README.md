# Page templates (WO1)

The shared catalog is `server/app/data/page_templates_v1.json`. JavaScript imports
it directly; Python reads and validates it in `app.agents.page_templates`. API
startup validates the file before opening connections. Invalid data is an error,
not a request to fall back to an unrelated layout.

## Entry points

- `catalog.js`: `validateCatalog`, `getTemplate`, `sectionDefinition`, and
  `textSlotForRole`. Python exposes equivalent snake_case functions.
- `tokens.js`: `resolvePageTokens(pageStyle, baseSwatchId)` and `baseColorOf`.
  This resolves fonts and palette values, without sampling garment pixels.
- `assemble.js`: `assemblePage({ storyboard, cutResults, copyResults, product,
  analysis, pageStyle, baseSwatchId, copywriting, failedCuts, licenseNotice,
  profile })`. Optional `profile` has `display_name` and `handle`; retrieving the
  seller profile in the worker belongs to WO3. Without it, the avatar and seller
  name use `MD`, and the account slot stays empty with a placeholder. With a
  display name, the avatar uses its first Unicode code point. The emotional
  intro avatar has a 98px circle and a 50px-wide text box at 24px, leaving room
  for the bundled font's `MD` advance without disabling text wrapping.
- `collectOverlayRegions(input)` evaluates the same layout and returns each
  source image's bare-text group and line rectangles, including reused SNS appearances.
- Python: `page_assembler.assemble(..., analysis=..., page_style=...,
  base_swatch_id=..., failed_cuts=...)` dispatches taxonomy 4 to
  `page_template_assembler.assemble_v4`. Other versions keep the existing path.
- The mock `buildEditorBlocksFromStoryboard` dispatches in the same way. The
  editor passes the analysis object when building its generation skeleton.

## Storyboard presentation (WOC8 R2, 2026-11-11)

Analysis offers only the two templates, `emo` and `show`. Color selection lives
in the storyboard header; previously saved pageStyle.colorMode values survive
analysis restoration and template selection. The existing serialized style-save
queue, restore/promotion and mannequin template lock remain unchanged. After a mannequin exists, only a change
to templateId is locked; fontStyle/colorMode/updatedAt edits remain valid in both
the mock API and the server. Removed `guide` is not a
selectable template or mock query; its five layout definitions remain in the
shared 32-design catalog. Long preview assets are
`public/templates/previews/preview-{emo,show}-v1.webp`; preview hover/focus,
full-view modal, missing-image fallback and reduced-motion rules are unchanged.

The header has two aligned label/control rows: 글자 스타일 and 페이지 색감.
Typography uses one centered two-line specimen, previous/next arrows and
noninteractive dots. Order comes from Object.keys(fontStyles), currently template,
serif, boldGothic. Arrows wrap and ArrowLeft/Right work only inside the focused
group. Every specimen reads the actual resolvePageTokens labelEn/titleKo fonts,
including family, weight, italic, case and tracking. Current names remain
다정 (emo template), 기본 (show template), 매거진 and 볼드. There is no specimen
border or vertical bar. Each arrow optimistically updates pageStyle.fontStyle
through the same serialized save/rollback queue and re-entry wait.

Both validators derive supported style IDs/defaults from the catalog. Retiring
an old saved font falls back only that font, preserving its template/tone; explicit
template selection uses the catalog's current default font. No actual font option
or design definition was added/removed in WOC8.

Page tone has two equal pill cells in the shared modal-actions group, 기본 (`mono`) and 의류 색감
(`point`). A check and 2px ink border mark the selected cell; neither unselected
cell is dimmed. SVG paint attributes read the actual pointPalette accentBg and
accentInk, rather than maintaining a duplicate CSS color table. The row is absent
for white/gray/black/null, unknown swatches and an empty product color list.
baseColorOf retains its existing explicit-base/first-color fallback. Both engines
resolve saved point to effective mono for these neutral cases without rewriting
the saved analysis on load or hover. Point mode maps both accentBg and the existing
panel alias to its pale palette; ink/invertBg and inverted section panels remain
neutral. No design definitions or catalogVersion changed.

useStoryboardTone owns only ephemeral pointer/focus preview and a bounded color
wave. Preview never enters the save queue. Leaving/blurring restores the current
selection; selecting uses the same queue as font selection, with a latest-style
ref preserving rapid interleaved choices. The queue separately retains its last
successful server value. A latest-request failure restores that value and clears
hover/wave state while showing the error; stale responses cannot replace a newer
choice. Earlier successful requests still become the fallback for a later failure. The stage band changes first, outline
rows reveal their new assembled fills between 100 and 500ms, and 280ms fill
transitions finish within 780ms. Reduced motion skips both delay and transition;
context changes/unmount clear timers. Mono and point assembled pages are memoized
for the same board/catalog state, so hover and reveal steps reuse them. The open
section picker receives the same preview mode; its wireframes tint accent surfaces
while keeping existing gray photo/dark-shape simplification. Both rows share a compact 256px control column: two 124px tone pills plus 8px gap.
Tone labels, accessible names and tooltips are 기본/의류 색감. The header
control group remains right aligned; the open-drawer one-column layout remains.
Title and subtitle occupy a top-aligned left copy block, with word wrapping
instead of expanding the control column. Pointer-compatible mouse events and
focus both reach the actual stage/outline/open-picker preview path. Analysis no longer renders color cards or their
explanatory copy.

The v5 board has an outline and one four-column stage. A right overlay drawer
never changes photo widths. Text-only `intro`/`for_you` runs and previously saved
zero-photo `fabric` fallback runs remain saved and assembled for generation, but
are absent from the outline and add list. New seeds omit fabric entirely when no
eligible analysis recommendation with kind fabric exists; Detail/BackDetail
uploads alone never qualify (D221). They never create a
new T03-TEXT-01 fallback run. The fallback design remains for old saved data.
Actual photo-bearing fabric remains visible. Overview, active-section fallback
and crop-source Section numbering use the same run visibility predicate. There is
no text-section management mode. All storyboard previews render text positions
as faint lines; design candidates additionally replace photos and colored shapes
with a generic grayscale rendering of actual assembler geometry. Editor/final
copy is unaffected. No separate handwritten layout scheme catalog is maintained.

The outline strip uses 184px thumbnails (172px below 1360px), with its column
44px wider. Default and narrow widths are declared together on the theme in
tokens.css, so repeated body/page theme classes cannot reset the narrow value.
Full section heights retain their actual proportions. Titles above
thumbnails contain Playfair italic `Section n`, catalog name, and visible photo
count, keeping the owner-verified 3px gaps and zero extra count margin.
The nonwrapping header contains only 페이지 구성 and the outlined ＋ 섹션 추가 pill.
The overview text action follows the final section inside the outline scroller.
Selected borders use primary ink; the existing pastel rotating border
continues on the floating action. When at least 40px exists to the panel's left,
a vertical 28px grip/trash toolbar sits in that outside gutter and scrolls with its
row. The scroll box extends into that gutter so the buttons are not clipped. At
widths below 1110px, or while the drawer moves the page to the 24px viewport edge,
tools use the inside-thumbnail fallback. Up/down arrows expand on hover/focus
and remain keyboard reachable. The opening has no tools. Boundary arrows use aria-disabled without losing
focus, and a completed move restores the same arrow/grip. Titles also drag.
Drop gaps appear during dragging; there are no between-section add buttons or
notches. Only outline text bars use emphasizeText to increase visible thickness;
design previews and generated text geometry are unchanged. The stage's white
pencil button reads 섹션 디자인 변경. The design panel has names only, default
first, and a header removal action except on the opening; it has no option toggles.

`sectionRuns.js` groups the saved blocks list by contiguous `pageSectionKey`.
That list order is page order in both assemblers, including hidden text runs.
Move/delete/design actions affect whole runs; the opening stays fixed. A design
change updates every run block's existing `pageDesignId`. The pure
`automaticSectionInsertionKey` chooses the last matching physical wear usage,
otherwise the next later default template kind (kinds absent from the default append). Hidden runs are never moved to
make room. List clicks use this automatic boundary; explicit drops still insert
before their lower run, or at the end. The low-level add API retains its null
boundary meaning page end and never uses `addPlacements.after` for placement.

The single outline-header add action opens a filtered list of absent singleton
photo kinds plus repeatable daily/studio wear. A click immediately adds its
default design and keeps the panel open. Its generic 64×84 default-layout preview
flies for 520ms, then the inserted row is briefly marked; reduced motion inserts
immediately. A DOM snapshot is captured before a singleton option disappears.
This transient Web Animations transform never changes saved geometry or uses
React style props. A panel-local message exposes standalone undo while the bottom
action is hidden. Default color layouts are the existing dynamic grids; no deferred
D201 vertical color design has been invented for the representative preview.

`designChoices.js` uses the whole catalog, independent of per-template alternatives.
Candidates match kind, physical wear usage and declared layout photo-position
count, including reuse. The opening alone compares generated positions: an
available borrowed product photo in P01-D01 costs zero additional cuts. Without
that source its extra position still counts, so a one-photo opening cannot switch
to a design that would silently generate a new ghost cut. Color designs must expose a front photo for every actual
sale color; fixed layouts repeating only some colors are excluded even if their
position count happens to match. Current three color grids all expand dynamically.
Worn-crop-only points reject layouts whose metadata excludes cropped sources.
Exactly the template instance's default gets `기본` and is placed first among
eligible designs; the remaining candidates retain their catalog order. The
current selection check is independent of that order. Presentation samples use the
first four photos for variable daily/continuous albums, repeating when needed;
fixed rotation stays three, and colors use the actual product color count.
Samples never enter persistence, undo or billing. Preview heights/coordinates are
validated before SVG transforms, and crop origins follow the editor's -ox/-oy
rendering contract. Stage crops use that exact assembled viewport inside an
overflow-clipped nested SVG, then contain it in the portrait card with bg-2
letterboxing; the outline continues rendering the actual assembled geometry.
Both engines cap worn-crop zoom at a 30% visible source width and, for measured
native dimensions, at 1x. A smaller native image stays smaller than its slot;
slot-fill max operations must not undo this limit. Pixel rounding remains shared.
Approximate storyboard preview dimensions carry transient `dimensionsKnown:false`
and therefore use only the fractional limit. That metadata is never a saved
block field. The 12 D205 regions in seed.js apply to newly added crops; valid
stored cropRegion values are preserved until the source ID changes. Rebinding
selects the current region by bodyPart. Positive bounded ROIs such as the new
28% neck region are valid even though rendered zoom stops at 30%.

For source-bound AI product/detail cards, `detailTargetPreview` separately resolves
the candidate's bounded region and exact `selectPublicAnalysisPhotos` sourceIndex
on the base color, also checking sourceSlot. `detailTargetPresentation` keeps its
overview-first src/label/target contract for editing and retry. Only storyboard
presentation uses `assembleStoryboardPreview`: it decorates transient image
elements with region-centered crop geometry and `previewDetailCrop`, shared by
the stage, outline and overview. These are never saved block fields. Missing
candidates, invalid regions or missing originals keep the existing whole-photo
fallback. Real generated cutResults take priority and remain ordinary assembled
results; the raw JS/Python assemblers and worn-crop rendering are unchanged.

`useDetailPreviewDimensions` reads intrinsic sizes of the distinct bound originals
while the board is open, without canvas/pixel access, API calls or Product changes.
The transient URL-keyed map preserves square/landscape source proportions and
applies the same 30%-width/native-1x cap. Source changes and unmount release image
callbacks. Before measurement, or if a source cannot load, the explicitly unknown
1024×1365 preview estimate uses only the 30% rule. Existing source metadata with
valid native dimensions needs no additional measurement. An undersized original
stays smaller than its slot rather than being enlarged past 1x.
SVG originals use their intrinsic aspect but have no bitmap pixel cap; the mock
SVG sources therefore retain distinct enlarged region previews after loading.

Catalog/recipe metadata determines `일상 착용컷`, `스튜디오 착용컷`, `디테일 요소`
and `원단 설명`; saved titles are not rewritten. Showroom continuous horizon wear
is studio wear even on older styling-named instances. Both templates add daily
wear with P02-D01 and two same-place full/medium photographs; studio additions
use P04-D01 with front/side/back. The showroom's default continuous layout remains
P02-D02. Existing default budgets and extra-color cards remain unchanged.
Only physical daily wear exposes cut/set addition and the twelve-cut hint.

`storyboardPositions.js` derives stable view IDs from section/slot/source and
labels real assembled image positions. Single-photo sections have no badge;
mixed areas use 큰/작은 자리. Hover links the stage to a 2.4px ink thumbnail
outline plus a faint ink veil. Clicking an active thumbnail position selects that
photo; an inactive thumbnail first opens its section. Same-section/same-cut-type
wear or true detail photos can exchange positions; source settings and similar
followers travel together. True-detail swaps move whole pointItems bindings,
including sourceBlockId and detailTargetId, rather than exchanging IDs alone. Opening/review/color/fabric/SNS, rotation directions,
borrowed crops and similar children are fixed with a short reason. Cross-place
moves rekey separated set runs without losing membership. No view ID is saved.

`sectionRemovalImpact` computes the exact next blocks before showing a worn-run
confirmation. It explains crop retarget/removal and materialized SNS costs.
Confirm uses that same snapshot, so fallback IDs do not change; cancellation
leaves blocks untouched. Other kinds remove immediately. Add/remove/move/design
changes are individual undo entries; ordinary edits retain grouped undo. Focus
returns to the original trigger or a surviving active outline control.

Initial points require at least two distinct clear structural detail targets,
excluding fabric: two/three targets create two/three true detail photos, never
mixed crop/detail rows. With fewer targets the initial run is omitted. Manual
addition uses three crops from one existing wear source and creates no billed
photo. Source priority is rotation front, continuous studio front, daily front,
then another remaining wear photo (including uploaded wear). First/review/SNS
are not crop sources. When no wear photo remains the derived run is removed.
The stage reports the source's current visible Section number after moves.
`detailBudget.js` reads the catalog `photoBudgets.detail` value (4): at most three
clear structural photos plus one fabric photo. Concrete server subject kinds and
legacy `structure` are accepted. Fabric always uses a clear standalone fabric
candidate from the ready analysis recommendations, including candidates found
in uploaded closeups. No unbound fabric fallback is inferred from upload wells.
The manual twelve-cut daily-wear gate, three
unbilled crop additions and saved-board validation limits remain unchanged.

`sectionDependencies.js` reconciles reused positions from the selected design.
Moving sources preserves links. Removing a source materializes only used slots
as separate generated cards: I09-D14 has sns1 only, never a hidden sns2.
Reinsertion or undo restores reuse and removes fallback cards. Worn crops retarget
as above. All mutations preserve the existing blocks schema.

D204 applies to every published set member. Selecting another set's member or
using one outside its group creates a fresh one-member instance, with variation,
member order and background settings; styling uses pose scope, studio its normal
complete-example rules. Same-set choices preserve their instance. Split runs
are rekeyed; similar followers inherit their source. Legacy ungrouped ss_ choices
are repaired before autofill. Ordinary independent examples remain ungrouped.
Used examples stay selectable; storyboard photo cards no longer display used
badges or names. Automatic selection still prefers unused members. Stage group
names continue using `spaceSetDisplayName`; picker cards contain photos/checks
and, for studio replacements that increase the count, a cut-delta badge. Singleton groups outside wear have no group
heading. A row reserves header cells only when at least one group actually has
a heading; the add tile uses the same conditional offset. Color-product sections
have no group headers. Photo captions show the ordinal and, only with multiple
sale colors, a color dot followed by its visible color name. The dot is decorative
for assistive technology because the name already supplies the label. Place and front/back caption
words are absent. Extra-color wear group heads retain the actual color name.
The common stage subtitle is 예시 이미지를 클릭해서 변경할 수 있어요; derived-source
and text-only guidance remain separate in the stage body.
Whole-set replacement/background changes retain atomic retry/save.

Showroom first.hero uses the restored independent signature pool, filtered by
gender, garment, bottom category and dress rules, at horizon/medium/front/all.
Analysis owns subCategory/customCategory, so entry/autofill, the photo panel,
repair and reroll pass Analysis explicitly instead of reading Product fields.
Women skirts select the skirt pool; unknown bottoms retain pants and women
dresses retain their dedicated pool. An Analysis object is authoritative even
when its category fields are null. The optional Analysis argument omitted or
entirely null retains compatibility with older direct flat category callers.
Both templates' first.hero can select the single pinned signature card. New
showroom openings use that project/slot-stable pick; emotional openings keep the
ordinary-place default. A saved approved signature remains the card's picture as
well as the stage's, and ordinary showroom choices survive re-entry. First-slot
recipes allow styling/horizon and full/medium. The active pool has 39 entries;
the two retired local/server entries remain readable. New asset metadata uses
the existing signature generation contract and requires the owner's CDN upload.
Entry assignment preserves valid seller choices. Both category fields now participate
in seedProductKey and analysis-save prefetch invalidation. An untouched v4 board
with the previous key format may rebuild once, including unknown categories and
either template; the next resolve is stable. Edited boards are not reseeded.
sig_ stays out of the ordinary example catalog and follows the existing
server signature worker input route. New showroom seeds default to P01-SHOW-01,
a full-width 3:4 photo followed by a 28px product name with 70px side insets.
The generic singleLine text policy normalizes whitespace and shrinks a long name
to its measured one-line width. P01-D01 retains its old title/ghost layout as a
candidate under the zero-extra-generation rule above. Switching and undo keep
the same source cards and signature recipe; existing saved designs are not migrated.

`StoryboardPhotoGallery` is the storyboard-only picker. Place tabs retain
cafe-first order, have no all/more option, and wrap between whole labels. Only
places with photos eligible for the current garment, gender, framing and light
are shown. Opening uses the photo's place when it is available, otherwise the
first visible place. Framing/light changes that empty the active place move to
and remember the first visible place; no available places keeps the empty message.
The signature stays pinned regardless. The light dropdown reads 빛: 전체 and
filters the existing soft/hard lightQuality tags. Photo framing opens at the
current example's full/medium value. Signatures, missing photos and unknown
framing use medium (하반신 for bottoms); the wear gallery's all-framing option is
still manually selectable. First/review/faq use the place/framing gallery
instead of the old cut-family pending transaction. Normal wear-set instances
still show their same-place photos and the folded alternate-gallery control;
one-member instances open alternates immediately.

Matching set photos form one representative card even if only one member matches
the framing. A click expands matching photos inline after that grid row. Cards
have three columns and no names, descriptions, count or used badges. Keyboard
paging moves focus to a persistent, visibly focused gallery region before old
buttons disappear. Group replacement/add panels use the same place/light/tone
rules and choose the whole set. The editor's separate MoodGuide keeps its existing
single-image controls; these new storyboard rules do not change editor behavior.

`exampleToneOrder.json` contains the owner's supplied tone order. Numbered cover example IDs
sort ascending, then rotate as one ring with a project/filter-stable hash; unknown
IDs stay in their existing order at the end. There is no 48-entry random sample in
the new storyboard picker. Six-card pages preserve the existing paging controls,
and the opening signature remains the first card on every page/tab. The order
and filter state never enter saved blocks or automatic initial filling.

`productPresentation.js` applies one existing productPresentation value to AI
product/ghost cards of exactly one contiguous color_product run. Detail, mine,
first-page and other runs are excluded. Atomic photo persistence saves/undoes the
whole change and rolls it all back on failure. Old missing/mixed runs are shown
without migration and require selection before their photo settings are edited
or generation proceeds. Added/permitted moved full photos inherit their target
run's selected mode; the new-section default remains ghost. The picker has only
three mode cards; display-only local examples follow the clicked face, fall back
to the same type/mode Front with a note, then a gray name-only card. Neither image
fallback nor thumbnail failure changes the real direction/color/source recipe.
First ghost and fabric product attachments use only paired pageDesignId values:
P01-D11 ↔ P01-D01, P01-D09 ↔ P01-D09-G, P01-SHOW-01 ↔ P01-SHOW-01-G,
T03-D01 ↔ T03-D01-P, T03-D06 ↔ T03-D06-P. These pairs have pickerHidden=true;
the picker applies its existing kind/usage/photo-count rules to the visible base,
and changing that base retains an enabled attachment. Saved hidden layouts still
assemble. The catalogVersion stays compatible with existing saved page styles.

`stageAdditions.js` computes ephemeral pale positions and rechecks eligibility
on insertion. No option flags or preview fields enter blocks. First ghost reuses
only the original color_product run’s base/front AI full photo when every product
full photo in that run explicitly uses ghost. Otherwise it creates one clean
first.ghost AI recipe; undecided/mixed modes must first be selected. Flat/natural
transitions retain its ID, ghost reuse removes it, and base changes update its
color. The clean recipe has ghost/front, angle same, empty match/ref arrays and no
set/example/mood/similar state. Failure of a reused source never creates another
billable photo. The displayed first ghost has readonly base/front settings.

Daily wear has one pale next-unused-member position per styling group, omitted
when exhausted or at 12 actual photos. Insertion preserves that set instance and
pose reference. Studio wear and color_product have no pale positions. Points
may add one eligible fabric target if no fabric run exists, that target is unused,
and fewer than four actual AI detail photos exist. Crops consume no detail budget;
the additional fabric uses the same measured ROI preview and a separate row from
worn crops. If all worn sources disappear, unavailable crops are removed while
an independent detail remains. Fabric’s optional product position reuses the
original base/front source for zero generations; removing that source downgrades
to the base fabric layout instead of creating a fallback. Pair X actions remove
only the attachment, never its source. Each insertion/removal is one undo/save.

Pale positions show only their preview and label; count/credit changes appear in
the existing floating action (구성 n컷). Other generated-photo counts use 컷;
user-upload progress retains 장. The photo panel places 의류 색상 선택 after its generated-example gallery
(or product-presentation examples), separated by a top rule and spacing. It uses
one dot/name button per color and is omitted entirely for single-color products. The pinned
signature card has a small 시그니처 chip.

Drawers keep their 48px header, 12px viewport inset, focus return, Escape/blank
click behavior and independent scrolling. Place replacement marks the whole
current group with the existing 2px selected border and dims only other photos.

At desktop widths (1024px+), only /create/storyboard uses a viewport-bound shell.
The compact 44px footer contains the terms/privacy links and a business-info
popover built from the existing CompanyInfoRows. Other routes retain SiteFooter.
The outline and stage body are separate flex/grid scrollers; the document does
not grow. The stage header is a non-scrolling sibling of the body, so its tinted
band uses the whole card width even when a persistent scrollbar gutter is present.
Only the body carries data-storyboard-scroll. Its key is the section key: changing
sections creates a fresh body at scroll top, while same-section tone/settings
updates preserve the body and its scroll position. The stage fills the remaining grid column instead of capping at 742px, so
its right edge matches the full-width header and portalled action. The action
uses the same 1030px inner-page right-edge formula. An open drawer reflows the
header into the safe remaining width, without resizing the stage photos.

Completion used to remove the progress ribbon's height, moving a hovered tone
button away from the stationary pointer. The storyboard reserves a 36px strip;
a second existing ribbon can use the page's 44px top padding. Notification context
rerenders alone do not clear preview state, and pointer/focus previews are tested
through the actual ToastProvider. Toasts sit above the 44px footer, 16px gap, 60px
action and 12px clearance. The business popover raises its footer above these
portals only while open and handles Escape before a background drawer. With a drawer
open, the legal links, business popup and notification move into the visible
left area so the drawer cannot cover them.

The v5 body theme hides the seller aurora/orb only during this screen and mounts
two centered, softly rotating orbs. White outline/stage panels use named scoped
border/shadow/background tokens; panel headers use bg-2.
Pastel is not used as text; drop lines use ring-flow-5. The seller ADR-0021 ring
values are inherited. Layout/panel CSS modules and scoped tokens isolate changes
from other screens; cleanup restores their usual background. The floating action
is portalled outside page-enter transforms, stays above the footer, hides only
with a drawer, and retains counts, credit calculation, undo and next-step flush.
V4 copywriting remains always on through the existing serialized confirmation.
Prefetch, thumbnail reveal, autosave/conflict handling, errors and PC-only handoff
remain on their existing service paths. No DB field or paid generation call was added.

For mock-only QA append `mockDetail=full` to either
`/create/storyboard?mockTemplate=emo` or `mockTemplate=show`. The mock draft then
uses three structural subjects from its Front image and one fabric subject from
its Detail image. detailRecommendationsFixture.json contains public candidates
generated through the server contract builder; tests reconstruct their contract
from the actual mock original bytes and require exact normalized equality.
Missing/unknown mockDetail leaves the default unavailable/empty recommendations
unchanged. HTTP adapter and production analysis paths do not read this flag.

## Layout interpretation

Coordinates use a 1000px canvas. The catalog contains the ten layout node types
from the contract. `x` and `w` are local to their containing panel; ordinary nodes
flow vertically. `gap` is trailing spacing. Panels can contain local columns;
overlays contain named groups anchored at the top, center or bottom. Text heights use the
shared integer metrics, count Unicode code points, preserve multiple spaces,
and wrap at word boundaries without splitting Korean words. Long unbroken words
reduce their font size to fit their allotted width.
Uppercase Latin letters use `textMetrics.uppercaseAscii` (700 units versus 560
for other ASCII). Capitalization precedes sizing. Width, wrapping, height, hug
panels, rules, swatch groups and overlay sample regions all include the emitted
pixel tracking for every code point, including the final character. Integer
hundredths keep positive/negative tracking rounding identical in both engines.

Panels and lists accept scalar `padding` or `[padY, padX]`, `hug`, `minW`, and
`maxW`. Lists use `hugMode: each | max`; the latter shares the longest box width.
Box measurements use the same wrapped-line integer units in both engines.
`rule.w: text` follows the preceding text's measured width and hides for empty
copy. A panel matching its actual solid background omits its shape and horizontal
padding. A photo background never triggers that omission.

`factTable.layout` supports inline, columns (150px label column, 0.72-size muted
labels, 20px row gaps and a 1px bottom rule), and stacked (24px row gaps).
Stacked labels/values follow the node/template alignment; columns stay left
aligned. Lists support geometric 6px dot/square markers with 22px indentation
from the first rendered line's start, including centered showroom summaries.
Dots use the editor's existing `circle` shape. Point rows accept centered
vertical alignment and photo radii (emotional detail 16px; shared T05-D07 detail 0px). Color captions
can show 10px swatch dots from the shared `swatchHex` map, grouped with the
measured name width. Light dots (weighted RGB brightness above 225) have a 1px
`accentLine` token border. English color names use an actual English product color
name or remain empty; swatch families are never translated into product names.

Top-level `photoStack` and `pointRows` produce one editor block per photo/row.
`photoGrid` produces one block per row. Cell width uses the first row's column
count for the whole grid; an incomplete last row keeps that width and centers
the remaining cells. A grid containing just one photo still uses one full-width
column. Section headers occur only on the first
block. Extra cards, including cards with `slotKey: null`, follow in new blocks
inside the same section. Section spacing is added once, on the last block.
If that block differs from the canvas color, a locked canvas-colored rectangle
(`pageSectionGap: true`) covers the appended gap; no spacer block is introduced.
`textMetrics.maxBlockHeight` is a layout verification budget, not a clipping
operation; the editor itself does not enforce a block height ceiling.

A photo slot matches `g`, `g1`, `g2`, or `g.1` after stripping an optional
`<instanceKey>.` prefix. For example, `wear_styling.g1` fills the `g` slot.
`$current` refers to the card currently being laid out by a repeating node.
Generated photo elements retain the card's slot key. Reused photos retain the
target slot key and the original card's `sourceBlockId`. An unreconciled assembler input whose source has been removed still produces an
empty target without `sourceBlockId`; v5 mutations materialize a fallback before
saving, as described above.

`photoBudgets.detail` is the shared initial budget of four source-bound detail
photos. Points use at most three distinct clear structural targets and fabric
uses at most one; lack of a fabric source omits that initial run. Existing saved
zero-photo fabric is compatible but is never a newly seeded text replacement.

Copy is keyed by the first storyboard card of the section run, including a
leading text card. Repeated copy roles are zero based (`items.0.desc`, etc.).
Static and fact slots ignore copy results. AI slots use the provided result or
an empty string and placeholder. When copywriting is disabled, seller MD text
is preserved verbatim and the other AI slots remain empty. `featureCopy` takes
priority for matching product-point descriptions when copywriting is enabled.
The intro `facts` slot projects confirmed color, material and fit values. Notice
slots have `unavailable: true` and no fact path because the current analysis
contract contains no product notice; these slots are hidden. SIZE remains absent
because the fact contract has no confirmed size-name path. The first kind adds
an optional `intro` role for showroom openings, hidden when empty.
WO3 will supply that copy. MD body paragraphs become at most three separately
hugged bubbles, 12px apart, with a heading on the first. Excess paragraphs stay
in the third bubble; body elements retain the `body` role plus `paragraphIndex`
and `paragraphCount`.

`P01-D01` opening ghost images and SNS photos use reuse slots. P01-SHOW-01 is the
showroom default; P01-D01 can also be chosen when its product source exists. Review and FAQ
backgrounds are independent generated cards. I09-D06 and T08-D07 use a 4:5 photo,
full black 30% scrim, fixed white heading and smaller alternating white 96%
bubbles at the bottom (7% outer insets). I09-SCRIM-01 and T08-SCRIM-01 use a
bottom-58% transparent-to-black-72% gradient and fixed white copy. Catalog-driven
scrim, fillOpacity, shadow and itemGap attributes are shared by both assemblers;
saved rectangle paint/opacity/shadow is also rendered by editor/export and SVG
previews. Wireframes draw the band using the storyboard-only
--sb-wire-scrim-end token (#777773), retaining its 0-to-.72 alpha and lower-58%
geometry. Over the photo placeholder the endpoint contrast is 2.153:1; white
copy lines over that endpoint are 3.140:1. Text above a preceding gradient uses
previewTextOnScrim; default bubble designs retain their existing paints. Actual
assembled black/white output and adaptive contrast rules are unaffected. D216 removes the
old bottomClear preference from these section recipes. Inversion is limited to showroom
blocks. English labels are stored in uppercase when their font style requires
it; factual English color names keep their initial-capital form.

Every adaptive bare-text overlay group reads `cutResults[].overlayTextColors[groupKey]`,
accepting only the catalog's white or black. The key is
`<pageSectionKey>:<targetSlotKey>:<group.id>`. The worker evaluates the actual
layout with its copy, maps each rectangle through cover or the editor's saved
crop to source pixels, and measures each line after generation. New
`overlayTextLines[groupKey:elementIndex:lineIndex]` entries contain `{color, shade}`.
The choice maximizes the lower-decile per-pixel contrast. Shade is enabled only
when neither color reaches 3:1 for 90% of a line's pixels. Pixels are not
resized or averaged. This CPU work runs off the generation event loop. Reused
photos also receive colors for their SNS appearances. An error or missing
measurement uses default white without shade. Opaque panels/bubbles are excluded.
Saved text elements carry `contrastLines` and a `contrastFor` text/geometry/style
snapshot. The editor and exported DOM render the same per-line colors/shades;
editing that snapshot's fields invalidates the old measurement. Review/FAQ's
explicit textColor groups do not request sampling or apply old overlayTextColors:
their fixed design controls the ink and scrim. Bubbles keep their own dark ink.
Fixed white over a 30% black scrim cannot guarantee a contrast threshold for
every possible source photo; this follows the owner's fixed design rather than
claiming that a thin scrim provides automatic contrast assurance.

P01-D09 alone adds centered 1.5px rules above the English label and below
the colors, each 18px from the copy and using the group's selected ink. Both
rules use `matchText: "name"`: the longest measured name line (rounded up to a
pixel by the existing text metrics), clamped to 48px–60% of the 888px group
(532.8px). Forward/backward references resolve after layout, so neither the
English label nor the color list sets a rule's width.

The name's catalog `titleWrap` policy limits its centered box to 70% of the
photo width (700px). Try 51, 44, then 38px, keeping one line when possible,
otherwise balancing at most two lines at each size. Only at 38px may it use
three lines. Candidate breaks use whitespace, preserve explicit newlines and
the whitespace within each line, and minimize the longest/shortest line-width
difference (then the longest width; ties use the earlier break). An individual
word that still exceeds the budget at 38px can additionally break after `]`
or before `(`. No other internal word boundary or ellipsis is introduced.
The chosen breaks are saved as newlines in the text element; `product.name`
is unchanged. `keepWords` applies `white-space: pre`, `word-break: keep-all`
and `overflow-wrap: normal`
in the editor and its cloned export DOM, including the unmeasured mock/pending
path. Stored newlines are the only rendered breaks, including around hyphens
and other punctuation. Heights, contrast-line keys and placement candidates use
these same lines.
Infeasible input (over three lines at 38px, or an oversized indivisible word)
keeps all words at 38px, permitting excess rows/width rather than truncation
or arbitrary internal breaks. This exception preserves the font floor and copy.

Its `positionFallback` policy lists
`center`, `top`, `bottom` in that order with `edgeInsetRatio: 0.08`. Edge insets
apply to the entire group including both rules; exceptionally tall groups
reduce the inset to keep the group within the photo when it fits.
`collectOverlayRegions` exposes exact `positionCandidates` line rectangles,
using the same layout and cover/crop mapping as final assembly. For each
candidate the server scores each ink by its worst line's lower-decile ratio.
The first candidate reaching 3:1 with one ink on every line wins; if none
does, the highest score wins (ties retain the earlier position). Only lines
below 3:1 in that chosen ink receive the existing shade.
Empty or whitespace-only first-title lines retain their line indices but have
no measured ink footprint and never cause relocation or shade.
The worker includes `overlayTextPositions[groupKey]` alongside the selected
group color and line metadata in the generated cut result. Both assemblers
apply that position to the copy and both rules; first-title lines use the
shared group ink. Missing/invalid positions, mock output and pending skeletons
remain centered. Other photo overlays ignore position metadata and retain
their existing independent line colors and geometry.

Template pages default to stitched editor view. Explicit choices are kept per
project in browser storage; unavailable storage leaves the template default.
Separated view groups contiguous section blocks in one visual surface with 8px
between sections. Individual saved/exported blocks and photo replacement remain
unchanged. Untouched generation sections adopt the completed server geometry,
including new bubbles, hidden/visible intro copy and contrast metadata. Seller
edits keep their existing merge protections. The temporary `genTemplateBaseline`
is removed at final merge; paragraph and fact-cell copy identities stay distinct.

D86 centers the emotional first-page text as a complete group, using its actual
text bounds. `firstTitleBand` now marks the central 40%-60% band; longer copy
remains centered and its actual bounds are measured. The former empty-top
example preference is rejected. The existing v3 middle-band measurement is
unchanged.

The fit album saves a native editor `crop: {ox, oy, iw, ih}` that excludes the
upper 25% of a medium source, centers the retained area horizontally and avoids
exposing a gap. The summary rule follows template alignment: center in showroom,
left in the retained shared column layout.

## Verification and follow-up boundaries

Both test suites read `tests/fixtures/page_templates_cases.json`, which contains
132 complete input/output cases, including independent colors for two groups on
one fabric photo, optional opening copy, MD paragraphs, per-line contrast,
white/ivory/dark swatches in both templates, and a four-color shared grid.
Exact measurement-region parity is included. Separate semantic assertions check card counts,
geometry, source links, missing/failing reuse, copy switches, point surfaces and
inversion. `page_templates_quality.json` adds 18 fixed text/box width cases,
11 uppercase/tracking measurement cases and 10 grid geometry cases. Editor JSX
and export clone/capture tests cover circles and 1px borders at the SVG/raster
boundaries without starting a browser; they do not compare rendered PNG pixels.
`page_templates_title_wrap.json` fixes name breaks, font sizes, measured widths
and rule lengths, including line-budget boundaries, balanced two/three lines,
uppercase/negative tracking, bracket/parenthesis boundaries and astral text.
Both languages test each name at center/top/bottom. Synthetic pixel tests also
exercise relocation using the newly wrapped line bounds and final contrast DOM.
The crop cases include custom saved regions and tiny, large, unknown-size and
approximate-preview sources. WOC3 cases cover absent fabric, four initial details,
seller uploads without fabric eligibility, old saved text fallback, both gradient alternatives and
new/retained showroom openings with reuse-source removal. Mock full/default
subjects in both templates add four cases: the default has neither points nor
fabric, while full has three structural details plus one fabric candidate.
Fixtures are never regenerated by a test run. Run
`node scripts/regenerate-page-template-fixtures.mjs` explicitly: it checks exact
JS/Python agreement before writing the new expected results.

WO2 owns seed construction, storyboard validation/storage, template selection
and removal of v3 code. WO3 owns new copy generation, editor draft badges and the
font bundles/weight table. Serif is defined in the catalog but is not exercised
as an active style in WO1 tests. No browser, paid model or network call is needed
for the WO1 tests.

For WO3, reserve or reconcile layout when late copy changes text height or the
recommendation item count (four to six). The existing late-copy merger preserves
seller geometry, so adding more copy roles must also handle the additional
panels/slots. WO1 tests verify the completed assembly and bypass the legacy
skeleton height correction; they do not claim to implement that WO3 merge.
