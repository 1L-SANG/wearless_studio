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
  profile, shopName })`. Optional `profile` has `display_name` and `handle`; retrieving the
  seller profile in the worker belongs to WO3. Without it, the avatar and seller
  name use `MD`, and the account slot stays empty with a placeholder. With a
  display name, the avatar uses its first Unicode code point. The emotional
  intro avatar has a 98px circle and a 50px-wide text box at 24px, leaving room
  for the bundled font's `MD` advance without disabling text wrapping.
- `shopName` is a separate optional, trimmed string for frame footers only.
  It comes from a connected mall's `mall_connections.shop_name`, never a member's
  profile or account name. Python accepts the corresponding `shop_name` argument.
- `collectOverlayRegions(input)` evaluates the same layout and returns each
  source image's bare-text group and line rectangles, including reused SNS appearances.
- Python: `page_assembler.assemble(..., analysis=..., page_style=...,
  base_swatch_id=..., failed_cuts=...)` dispatches taxonomy 4 to
  `page_template_assembler.assemble_v4`. Other versions keep the existing path.
- The mock `buildEditorBlocksFromStoryboard` dispatches in the same way. The
  editor passes the analysis object when building its generation skeleton.

## Storyboard presentation (WOC10, 2026-11-12)

Analysis offers only the two templates, `emo` and `show`. Color selection lives
in the storyboard header; previously saved pageStyle.colorMode values survive
analysis restoration and template selection. The existing serialized style-save
queue, restore/promotion and mannequin template lock remain unchanged. After a mannequin exists, only a change
to templateId is locked; fontStyle/colorMode/updatedAt edits remain valid in both
the mock API and the server. Removed `guide` is not a
selectable template or mock query; its five layout definitions remain in the
shared 52-design catalog (24 picker-hidden definitions). Long preview assets are
`public/templates/previews/preview-{emo,show}-v1.webp`; preview hover/focus,
full-view modal, missing-image fallback and reduced-motion rules are unchanged.

The header has two aligned label/control rows: 글자 스타일 and 페이지 색감.
Typography uses one centered two-line specimen, previous/next arrows and
noninteractive dots. Order comes from Object.keys(fontStyles), currently template,
plainGothic, serif, boldGothic. Arrows wrap and ArrowLeft/Right work only inside the focused
group. Every specimen reads the actual resolvePageTokens labelEn/titleKo fonts,
including family, weight, italic, case and tracking. Current names remain
다정 (emo template), 기본 (show template), 담백 고딕, 매거진 and 볼드. Plain Gothic
uses Pretendard 500, uppercase and 0.12em tracking for labelEn; titleKo uses
Pretendard 600 and -0.02em tracking. Existing sizes and the other three styles
are unchanged. There is no specimen
border or vertical bar. Each arrow optimistically updates pageStyle.fontStyle
through the same serialized save/rollback queue and re-entry wait.

Both validators derive supported style IDs/defaults from the catalog. Retiring
an old saved font falls back only that font, preserving its template/tone; explicit
template selection uses the catalog's current default font. No saved style field
was added for the fourth font.

Page tone has two equal pill cells in the shared modal-actions group, 기본 (`mono`) and 의류 색감
(`point`). A check and 2px ink border mark the selected cell; neither unselected
cell is dimmed. SVG paint attributes read the actual pointPalette accentBg and
accentInk, rather than maintaining a duplicate CSS color table. The row is absent
for white/gray/black/null, unknown swatches and an empty product color list.
baseColorOf retains its existing explicit-base/first-color fallback. Both engines
resolve saved point to effective mono for these neutral cases without rewriting
the saved analysis on load or hover. Point mode maps both accentBg and the existing
panel alias to its pale palette; ink/invertBg and inverted section panels remain
neutral. The existing catalogVersion remains compatible with saved page styles.

Font style and page tone use the same arrow carousel (StoryboardStyleCarousel).
There is no hover or focus preview: an arrow click applies the next value at once
and sends it through the same save queue as the font style, with a latest-style
ref preserving rapid interleaved choices. The queue retains its last successful
server value; a latest-request failure restores it and shows the error, and stale
responses cannot replace a newer choice. The stage band, outline and open section
picker read only the selected tone. The two carousels sit side by side in equal
256px columns; a neutral base color shows only the font style column.
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
as faint lines. Design/decor candidates and the completed preview use the actual
full section photos and assembler geometry, like the outline. Only the add-menu
representative previews retain grayscale samples. Editor/final copy is unaffected.

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
notches. The outline previews use emphasizeText to make
faint text bars legible; generated text geometry is unchanged. The stage's white
pencil button reads 섹션 디자인 변경. Design mode has one title/close header and no
removal action, default badge or checkmark. Removal remains in the outline.

Only the section-design drawer is 330px wide (`--sb-design-drawer-width`).
Photo and add drawers retain `--sb-drawer-width` (520px capped at40vw). The open
page and design drawer share the active width token: header and stage use at most
`100vw - drawerWidth - 12px - 16px - 24px`, leaving the same16px gap. Opening a
design selects that section on the stage and scrolls its outline row into view;
design changes immediately update the assembled outline. There is no completed
preview column. While the floating continue bar is hidden, the design pane shows
the existing change count and undo action at its bottom, including the same
hover/focus timeout pause and undo/save path.

Layout glyphs show only column/twoColumn/heroPair or color-column/grid structure.
The wear twoColumn glyph has two columns and three rows. Tile names are available
through aria-label/title only. The decoration and flat-design stage both read
디자인 선택. Their fixed silver SVG illustrations contain only photo rectangles,
panels and text bars: two openings, basic/frame styling, two detail designs and
two fabric designs. Product photos, selected layout, frame count and page tone
never change these illustrations. Their five silver roles live in tokens.css.
The actual-assembly design-preview cache was removed; the outline remains the
live assembled result and the separate add-menu preview is unchanged.

Selection uses one2px ink border without checks/default badges. Stages with one
choice disappear. Native buttons support Tab/Enter/Space; Left/Right arrows also
select and focus the adjacent option. Saving disables all options. At1440×900,
a330px drawer leaves288px after borders/padding (276px with a12px scrollbar).
Two illustration tiles are140px/134px wide and121.33px/116.13px high;
the76px layout tiles and both headings fit comfortably above the bottom undo.

`sectionDesignAxes` derives 배치 and 디자인 선택 from catalog `choiceAxes` metadata.
Each complete combination is still one `pageDesignId`: choosing a layout keeps
the current decoration. Frame choices use the automatic photo count below and
retain the current layout when eligible, otherwise resolve to column.
Each axis row disappears when it has at most one eligible option. Non-axis
sections retain the flat design list. `pickerHidden` only hides choices; it does
not invalidate or migrate saved layouts. Existing same-kind/usage/photo-count
and color-coverage rules still apply, followed by explicit `minPhotos` and
`minColors` constraints.

Styling and fit each offer 한 열, 2열 (at least two photos), and 큰 사진 아래 두 칸
(at least three). Fit uses only 기본 decoration; styling offers 기본 and 액자. Their column
defaults are P02-D01 and WEAR-FIT-COLUMN-01. Flow designs restart at every
contiguous place-set group; loose photos use consecutive color groups. Two-column
odd tails fill the row; hero-pair repeats one full-width photo and a pair, with
one remaining photo full width or two remaining photos paired. Photos remain
4:5 and all fit layouts retain direction captions. Curated wildcard repeats
include loose/similar photos at their stored positions. Legacy hidden layouts
without axes retain their previous overflow behavior. The live outline uses real stored groups and the full photo count. The separate
add-menu sample helper retains its representative role.

The frame decoration has nine styling-only definitions: WEAR-STYLING-FRAME-
{COLUMN,2COL,HERO}-{01,02,03}. `framePhotos` is catalog metadata; only the complete
pageDesignId is saved. New explicit choices use one framed photo when the section
has one photo, and two otherwise. There is no photo-count selector. All three
03 designs are pickerHidden, but saved one/three-photo variants still render
unchanged and show 액자 selected. An explicit layout/decoration choice from a
saved variant resolves to the eligible automatic one/two-photo design.

Framed layout minima use total photos minus the automatic count: column supports
0/1 remaining, twoColumn needs2, heroPair needs3. Four total withframe2 offers
column/twoColumn; three total offers only column and hides the layout stage.
When the current layout no longer fits, a frame choice falls back to column.
Reading old IDs never migrates them; an explicit new choice is one existing
undo/save. The backdrop remains a reuse, not another storyboard position or
generated cut. Frame selection and assembly rules below remain unchanged.

`selectFramePhotos` chooses the first contiguous group with enough medium shots.
Otherwise it picks the first group with the most medium shots, fills from that
group's full shots and then other section photos in stored order. The backdrop
uses an unused full shot in the winning group, then any unused shot in that group,
then the first framed photo. Only one frame is emitted. Remaining photos keep
original group boundaries even when an entire intervening group was framed.

The 1000px-wide band has 110px vertical padding around a centered 680px paper
card. Shared frameStyle tokens define padding 52/60/46, 24px photo gaps, 34px after
the product name and 40px before an optional connected shopName. Name/footer
use body500 at22px with .04em tracking. Photos retain their natural ratio, falling
back to3:4 when dimensions are unknown. Frame paper/ink are neutral catalog
tokens, independent of page tone. Unframed photos use the selected4:5 layout.

Color/product defaults to CP-COLUMN-01, with full-width photos and actual color
dots/names. CP-EMO-01 is the two-column choice only with two or more sale colors.
Both retain the existing requiresBack source rule; a missing uploaded base-color
back creates no slot. Only 기본 decoration exists, so there is no decoration row.
Points now have two flat design choices, with no layout/decoration axes.
PT-PHOTO-CARD-01 (사진과 글 칸 카드) is the emotional default; PT-TEXT-PHOTO-01
(제목, 설명, 사진) is the showroom default. Both templates can choose either without
changing photo/generation counts. Old PT-COLUMN-01, P06-D01, PT-COLUMN-CARD-01 and
PT-BELOW-CARD-01 are picker-hidden and retain their saved natural-aspect layouts.
The existing text-only PT-EMO-TEXT fallback remains.

The card design has a page-panel-colored section, only a Detail label in labelEn,
and white cards at x60/w880. The 4:5 photo fills card width; caption padding is50,
part name42 in the resolved title font/weight, and description35 in regular body,
muted color, line-height1.7. Cards are50px apart, without border/shadow. The plain
design has no section heading: one-based numbered actual part label (46px,
tracking-.03em), description (37px, light300, tracking-.05em, line-height1.35), then
photo. Text x43/w914, photo x40/w920; gaps are66/24/230px. Title font/weight follows
page style. Both plain text roles use the existing ink token, not a new theme.
New descriptions have no singleLine policy. Design-mode previews use every real/derived
photo; the separate add-menu representative sample remains limited to two.
Wireframe photo panels use the distinct --sb-wire-panel surface, with a restrained
tone mix during point-color preview. Overlay bubbles retain their existing paint;
frame backdrops become dark gray rectangles in wireframes.

Frame backdrops are ordinary editor image elements with optional `blur` in
1000px-canvas units (finite0–100, default48). Presence including zero is the
only marker; ordinary images do not acquire it. The editor exposes its existing
range/number control under 효과 (the control remains 흐림) and history/autosave path, and replacement preserves the
value. Storyboard and editor share BackdropSvg: original crop/cover geometry,
eight extended edge/corner strips, Gaussian blur and final clipping. Background
reuse is excluded from storyboard slot numbering/hit areas. The footer source
is an active connected store: Cafe24 first, then Zigzag, skipping missing or blank
names. Cafe24 must also be available and have a valid refresh expiry, matching
the connection API's effective connected status. The existing unique active
owner/provider constraint makes this priority independent of query order.
The worker reads only existing owner-bound mall connections when a frame exists
and mall connections are enabled;
missing/failed reads omit the footer and never fall back to profiles.display_name.
Storyboard uses a service/query hook with the existing account-scoped
`['mall-connections', userId]` cache (30s freshness), fetching only for a selected
frame and hiding a stale name on refresh error. Local unauthenticated mock demos
use an isolated local-mock cache key. Actual connected names also drive mock
generation. Account changes cannot apply an earlier response to a new board.
Legacy profile/initial handling in other designs remains unchanged.

Every page export bakes the blur after existing CORS-safe image preflight and
before image probes/toSvg: single block, long PNG, split/ZIP/storefront and both
publication/mall-registration paths. JavaScript triple-box pixel blur uses
premultiplied alpha and clamped edges, never CSS or Canvas filters. Positive
intermediates are bounded to1,048,576pixels/4096side; sigma scales with the work
raster and the baked PNG fills the original box without cropping twice. This
reduces background fine detail at very small positive blur, not page geometry.
Zero blur keeps original source bytes/crop without a full-height backdrop buffer.
Selected backdrop download produces PNG; oversized zero-blur standalone capture
fails before allocation and suggests split page export. GIF replacement uses its
fixed poster for these captures. Ordinary original-photo/GIF download is unchanged.
Server and mock validate only the optional image effect; no DB migration occurs.

First choices are P01-D09/P01-SHOW-01, fabric T03-D01/T03-D06, review I09-D06,
and FAQ T08-D07. Retired choices (including old opening, studio, color, points,
fabric-guide and scrim layouts) remain valid but picker-hidden. First/fabric
attachment pairs still preserve an enabled attachment when the visible base
changes. SNS is absent from new defaults and the add menu; saved SNS runs,
reuse behavior and the low-level compatibility addition helper remain supported.
Intro/for-you visibility is unchanged. Fit and color/product have no frame or
extra decoration. No color-background decoration was introduced.

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
action is hidden. Default color layouts use the current dynamic color column; no deferred
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
Legacy/hidden natural designs retain the 30%-source-width and known-native-1x
worn-crop caps, including a smaller image staying smaller than its slot. New WOC11
fit:cover viewports instead fill4:5 around the selected region's center, permitting
upscaling rather than empty bars. Ordinary2:3 detail photos retain5/6 of source
height. Unknown ordinary dimensions use native renderer center-cover; unknown
worn sources use3:4. No stored ROI is changed during assembly.

`wornCropRegions` now exports4:5 normalized regions for the3:4 source fallback.
New bindings and retargets use wornCropRegion/sourceAspect: preserve the anatomical
center and width, derive height for4:5, shrink only if needed to fit source bounds,
and clamp the origin. Existing exact cutResults dimensions may be supplied as
transient context; mine sources additionally require matching image URLs, or an
exact matching product image's dimensions. AI sources never borrow product Front
sizes. Current Storyboard callers have no final cutResults before generation and
use3:4 unless matching mine metadata exists. Same-source custom saved regions,
reordering/settings, late metadata and undo snapshots are preserved. Same-ID mine
image replacement is a source rebinding unless explicit replacement ROI was supplied.
Labels/bodyPart fields are unchanged; no source-dimension fields were added.

For source-bound AI product/detail cards, `detailTargetPreview` separately resolves
the candidate's bounded region and exact `selectPublicAnalysisPhotos` sourceIndex
on the base color, also checking sourceSlot. `detailTargetPresentation` keeps its
overview-first src/label/target contract for editing and retry. Only storyboard
presentation uses `assembleStoryboardPreview`: it decorates transient image
elements with contain-region crop geometry and `previewDetailCrop`, shared by
the stage, outline and overview. Bound candidates also carry previewDetailLabel. A fixed white gradient (56% through52%, ending84%) marks these planned AI details. Stage tiles show the part name plus 확대컷 and AI가 이 부분을 새로 만들어요; outline thumbnails omit that copy, while overview includes it. The notice is anchored 12px from the stage photo sides and 10px from its bottom. Overview uses wrapped SVG text with explicit coordinates inside the photo, avoiding foreignObject height resolution. Planned-detail cards have no hover translation; the source, mask and notice remain fixed. These are never saved block fields. Missing
candidates, invalid regions or missing originals keep the existing whole-photo
fallback. Real generated cutResults take priority and remain ordinary assembled
results; the raw JS/Python assemblers and worn-crop rendering are unchanged.

`useDetailPreviewDimensions` reads intrinsic sizes of the distinct bound originals
while the board is open, without canvas/pixel access, API calls or Product changes.
The transient URL-keyed map preserves square/landscape source proportions and
keeps the30%-source-width zoom cap, without a native-1x cap. Source changes and unmount release image
callbacks. Before measurement, or if a source cannot load, the explicitly unknown
1024×1365 preview estimate uses only the 30% rule. Existing source metadata with
valid native dimensions needs no additional measurement. Small originals may upscale. detailStageGeometry recomputes the same contain formula for its 3:4 viewport from transient source/region metadata. Scale is min(slotWidth / regionPixelWidth, slotHeight / regionPixelHeight, slotWidth / (sourceWidth × .3)). Surrounding original pixels fill the remaining viewport. If the source itself is too narrow or short to fill it, the contained source stays above a full-viewport copy of that same photo, so no blank gutter is exposed.
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
to its measured one-line width. P01-D01 retains its old title/ghost layout for
saved projects and its hidden P01-D11 attachment pair. Switching and undo keep
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

Coordinates use a 1000px canvas. The catalog contains twelve layout node types,
including the grouped `photoFlow` repeat and `photoFrame`. `x` and `w` are local to their containing panel; ordinary nodes
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
inside the same section for legacy layouts. Curated `choiceAxes` wildcard nodes
include those cards in their original order. `photoFlow` uses the row's actual
cardinality and restarts its twoColumn/heroPair pattern at each contiguous group.
Section spacing is added once, on the last block.
If that block differs from the canvas color, a locked canvas-colored rectangle
(`pageSectionGap: true`) covers the appended gap; no spacer block is introduced.
`textMetrics.maxBlockHeight` is a layout verification budget, not a clipping
operation; the editor itself does not enforce a block height ceiling.

Text nodes may opt into `weight: light` (300), trackingEm and absolute lineHeight.
Omitted weight retains the resolved page-font weight. `numberPrefix: true` adds
one-based N. only to a nonempty fact title, leaving the source label unchanged.
photoStack.includeLoose opts new designs into null-slot photos; itemGap measures
physical inter-item spacing and applies bottom padding only to the final item.
New designs' invert:false overrides the old showroom section inversion without
rewriting saved section data. Pretendard's bundled variable face declares100–900;
editor cssWeight preserves300 and the export embedder includes the same WOFF2.

`photo` nodes may opt into `fit: contain`, `aspect: source` and a fallbackAspect.
Positive actual image dimensions determine natural height; unknown preview
estimates use fallbackAspect instead. The height has a 1px lower bound and no
legacy min/max clamp, and cropTop is not applied. A wornCrop's natural ratio is
its selected region; its saved ROI, 30%-source-width and known-native-pixel caps
remain unchanged. Existing hidden designs keep their prior photo geometry.

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
is preserved verbatim and the other AI slots remain empty. When copywriting is
enabled, legacy items.n.desc first uses analysis.partDescriptionCopy, then
non-worn featureCopy and section-lead role copy. WOC11 nodes opt into
copyPolicy:partOnly instead: only the dedicated matching part description with
two nonempty explicit lines is used, without dictionary/featureCopy/role-copy
fallback. Old one-line stored rows remain stored and still work in old layouts;
new designs show the ordinary placeholder until valid two-line copy is supplied.
Long supplied lines wrap by words rather than singleLine truncation.

`page_part_copy.generate` keeps one existing completion invocation for seller
misses plus all actual part labels. Its response has separate featureCopy and
partDescriptionCopy lists; the same label may have different wording in each.
Seller DETAIL_COPY hits and existing feature_copy.generate/prompt/60-character
one-sentence rules remain unchanged. Parts always use new two-line generation:
first line describes supported visible construction and ends로/라, second gives
a modest visible benefit in해요체; each line is at most22 codepoints including
spaces.22 full Hangul at35px occupies770px inside the780px card text box;23 would
be805px. The plain text box also fits22. Forbidden/hype/material-function claims,
extra labels, markup/control characters and malformed/overlong lines are rejected.
The eight original structure examples live in server/prompts/page_part_copy_v1.txt.
The existing provider fallback chain remains; no correction call was added.
Missing/failed output preserves stored copy, but new partOnly rendering cannot
fall back to an old one-line dictionary sentence. Local analysis is updated before
current-job assembly. Copywriting off sends no part labels and shows no AI part
copy; the legacy seller-feature path remains independent of that toggle.
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

P01-D09 and its ghost pair add centered 1.5px rules when unplated above the English label and below
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
is unchanged. Untouched `keepWords` text applies `white-space: pre`, `word-break: keep-all`
and `overflow-wrap: normal`
in the editor and its cloned export DOM, including the unmeasured mock/pending
path. Stored newlines are the only rendered breaks, including around hyphens
and other punctuation. Heights, contrast-line keys and placement candidates use
these same lines. A seller text/size edit releases keepWords for that element,
using the ordinary fixed-width wrapping and measured-height path instead.
Infeasible input (over three lines at 38px, or an oversized indivisible word)
keeps all words at 38px, permitting excess rows/width rather than truncation
or arbitrary internal breaks. This exception preserves the font floor and copy.

P01-D09 and its ghost pair omit tagline; saved P01-D01/P01-D11 still use it.
Its positionFallback retains center/top/bottom candidates and edgeInsetRatio0.08.
The server measures every line at all three positions. Among positions at least
3:1, choose the highest score; within10% of that score prefer center, bottom, top.
Empty text lines keep their indices without contributing a failing footprint.

When none passes (including unavailable pixels), firstTitlePlate supplies a
60%-opaque neutral black rectangle and fixed white unshaded copy. Rules are
omitted. Plate candidates include padding26+18 above,64 horizontally and20+18
below. Width follows the widest measured text plus padding, capped at888px.
The common garmentBands table estimates vertical garment intervals by type/shot,
using seed anatomical bounds for full shots and an explicit medium-shot crop
approximation. A plate covering50% or more of its height with that interval is
avoided; scan only center/bottom (platePreference), or choose the least overlap when both fail. Ties keep center. Top remains a valid unplated title position only; invalid top+plate input resolves to center.

Generated cut metadata carries overlayTextPositions and overlayTextPlates.
Both assemblers emit the same ordinary rectangle and text with an existing
groupId prefixed title-plate:. This is not an object-library selection group:
the rectangle can be selected/deleted independently. Editor functional text
patches reflow following copy and refit the plate using measured text heights;
direct undo snapshots retain their exact geometry. A present plate disables
WOC13 photo-ink remeasurement. No new storyboard field is stored. Pending/mock
pages without measured metadata remain unplated and centered; other overlays
retain their existing independent line colors and geometry.

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
282 complete input/output cases, including independent colors for two groups on
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
WOC6 adds 38 shared cases for every layout/decoration combination in both templates,
all four fonts and known/unknown natural image dimensions. Separate engine tests
cover place-group tails, null-slot photos, native/ROI sizing and real color-index
binding after photo removal; all 60 focused parity inputs compare complete output.
WOC6B adds36 cases for frame counts/layouts in both templates, medium/full/other
group fallback, absent shop name, no remaining photos, unknown dimensions and
separate real-detail/worn descriptions. Focused frame tests add23 complete-output
parity inputs including source-index and removed-group boundaries.
WOC11 adds60 shared cases for both new designs, templates, fourfonts/twocolormodes, known/unknown images, new/saved wornROIs and copy-off/legacy-copy behavior. Focused engines add72 complete parity inputs and eight saved-render hash checks.
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

WOC14 R2 retains 10 shared cases for P01-D09 and its ghost pair: unplated top/center/bottom and plated center/bottom. The two top+plate cases are removed.
