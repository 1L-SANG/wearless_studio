# Mannequin Preservation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax.

**Goal:** Preserve good first cuts, use source-backed Sunburst corrections only for confirmed defects, validate preservation and update PR286.

**Architecture:** Reuse existing AG01/photo-structure and real mannequin worker. Add opt-in paired edit assessment to existing image_qc rescore rather than a new analysis agent. Keep shared scene/best-of QC contracts unchanged. No newmaskUI.

**Tech Stack:** Python FastAPI, pytest, existing OpenAI/Gemini clients, Copilot manifest.

**Spec:** documents/mannequin_preservation_v2_2026-09-14.md

## Global Constraints

- First Sunburst2K, existingvalidatedAG01reuse, noAstra/newanalysis, specialistQCoff.
- Preserve this thread's current dirty changes on base9709feef. Other rootcheckout work is unrelated.
- Newpaidprovider/imagecalls0, noDB/R2writes in tests, no migration/deploy/merge.
- Userauthorizedcommit/push/updateexistingPR286. Worker doesnotcommit; controller commits reviewedcombinedchanges.
- No requiredusercorrection/maskstep. Relative garmentfeatures plus sourcephotos are sufficient, noinventedcmcoordinates.
- Existing 12-imagequalityexperiment remainsincomplete and cannot be claimed solved by tests.

### Task 1: Coherent source-backed preservation implementation

**Files:** worker owns server/app/workers/mannequin_job.py; server/app/agents/image_qc.py, edit_gate.py, mannequin_bust.py, mannequin_untuck.py and relatedsmallhelpers ifnecessary; server/app/agents/vision_llm.py onlyopt-incomplete-envelope support ifneeded; server/app/config.py; copilot/api/manifest.yml; server/.env.example; related mannequin/image_qc promptfiles and focusedtests. Do notchangeunrelated sharedimage_high routing or matching preprocessing. Controllerownsdocuments and PRpackaging.

**Interfaces:** Extend existing image_qc.verdict onlywith opt-in before_image and edit_goal parameters (or equivalentlynamed). Existing no-argumentbehavior/schema ofscene,best_ofandunscored callers unchanged. Paired assessment outputs strictbooleans target_resolved and protected_regions_unchanged plus boundedregressionreasons; malformed/missing/incompletecannotapproveedit. Helper edit_accepted(result) isfalseunlessbothpositiveandno regressions, andexisting critical/source/matchinggatesalso pass. Add comparisoninputs onlytothe existingpost-editQCcall, not toallfirstgenerationcalls.

- [x] RED policy tests using recorded values:
```python
assert mannequin_untuck.gate_skips({"verdict":"untucked","confidence":0.7}) is True
assert mannequin_bust.gate_skips({"verdict":"adequate","confidence":0.8}) is True
assert mannequin_untuck.gate_skips({"verdict":"unclear","confidence":0.99}) is True
assert mannequin_untuck.gate_skips({"verdict":"tucked","confidence":0.9}) is False
```
- [x] GREEN conservativegate: onlyrecognizednegativeverdict withvalidconfidence>=existing0.85ispermissiontoedit. Gateoff/missing/error cannotcauseautonomousedit. Keepcompatibilityofpublicflagparserandtestexplicitoff behavior.
- [x] Set default/deployed maxfirstgenerationattempts1, bustpassoff andfabricpassoff; keepuntuckonwithrequiredgate. Currentconfigalreadydefaultsoffforbust/fabric, alignmanifestandcomments. First/adjust/repair use scopedimage_mannequin(Sunburst); neverchange globalimage_high usedbyotherfeatures.
- [x] Replace harmful bustprompt/templateconstantinstructions withneutralbase-preservinglanguage. RemovefixedB/Ccup,1.3multiplier,waistnipping,gapsallowed,SAMEORMOREbuttons,duplicateduntuckfrombust. Exactclosedbuttonsandoriginalnumber, fine ribs/color preserved. Dormantlegacyfunctions mustnotretainGemini-onlymannequineditroute; ensureavailableoriginalevidenceorabortunverifiededit. Do notbuildnewbodygenerator.
- [x] Reuse _specialist_repair_request orsmallcommonbuilderfor currentimagefirst, originalproductrefs plus sourcecrops, matchingref, explicitfitprofile and narrowlyscopededitgoal. Coverproduct_refsNone byrole-neutraloriginalphotos withoutinventingslots. Apply toconfirmeduntuck andfinalconfirmedqualityrepair, ratherthanoriginal-lesssingleimageedits. Do nothardcodepinkfeatures. Preserveoriginalsourceview labels andcustommatchinggrid/mirrorcontracts.
- [x] Convert defaultfinalqualityrepair tocurrent-cutedit withsourceevidence insteadofsource-regeneration. Defaultmax1firstgeneration plusoneconfirmedrepair preventsrandomfull rerolls. A filedecode/canvasfailure remainsinvalid, neverserveit. Existingboundedattempt/cancellation/billableunknown protections remain.
- [x] Pairedrescoreprompt compares originalsource, BEFORE,andAFTER withunambiguousindices. Existingoriginal+matchingindicesstayvalid. Require exact target success and no unauthorizedcolor/rib, seam/button/fastening, silhouette/body,pose/matchingchanges. Explicitfitrequestedaxisexemptfromsame-axisfreeze. Invisibleback/innerlabels arenotmissingdesign. RulesareintendedtoimproveQC,notpixelguarantees.
- [x] Wire pairedacceptance into checkeduntuck, activeposteditrescore, finaltargetedqualityrepair andexplicitadjustment path where an existingparent is available. Failure keepspre-edit goodimage anditsmetadata together. A known-badfirstcut mustnotbemarkedpassjustbecause repairfailed; preserveexistingfailure/reviewsemanticsandprevioususercut. Do notsilentlyweakenhardgates.
- [x] RED/GREEN tests: goodfirstcalls1withnofurtheredit, adequate/untucked/unclearerror=0edit; confirmedtuckroutesSunburstwithsource+beforematching; changedcolor/rib/placket orunresolvededit rejecteddespitehigheroverallscore; incompleteQC rejects; acceptededit retainscorrecthash/scores; explicitlengthchangeallowedwithoutwaist/bustrestyling; sharedscene/defaultQCschemaunchanged; missingstaleAG01doesnotreanalyze; noAstrapath.
- [x] Run focusedtests and fullserver suite once aftertargetedgreen, excluding tests/test_personalization.py (knownDB-writing test). Use /Users/daily/Documents/wearless_studio/server/.venv/bin/python -m pytest. No providers.
- [x] Report actualtests/redgreen and exactdiff in this plan's SDDworkspace, including any open concerns. No commit/push byworker.

### Task 2: Controller verification, independent review and PR

- [x] Inspectfullcombineddiff againstbase9709feef, includingpreviousuncommittedAG01/Flashchanges andnewtask1changes.
- [x] Runrequiredtests; recordnewcodecoverage, notvisualsuccessclaims. Evaluate recordedgatevalues fromoldexperimentwithoutcallingmodels.
- [x] Independentreview aftertask1, fixconcretefindings withimplementer andscopedre-review; noduplicatebroadreviewloops.
- [ ] Commit onlythismannequinwork, no images/outputdatasets/secrets. UpdateexistingPR286title/body withnewflow, gates, tests, unresolvedpaidvalidation. Pushnormalbranchwithoutforce. KeepPRdraftuntilimagequalityvalidationcanbeapproved; no merge/deploy.
- [ ] Verifyremoteheadmatchescommit, inspectCIfornewhead, reportlinkandlimitations. User-facingfinalincludesanswersaboveandwhatactuallychanged.
