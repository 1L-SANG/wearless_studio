package kr.wearless.fmholder.protocol;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import kr.wearless.fmholder.wallet.HolderWalletService;
import org.omnione.did.base.datamodel.data.AccE2e;
import org.omnione.did.base.datamodel.data.AccEcdh;
import org.omnione.did.base.datamodel.data.AttestedAppInfo;
import org.omnione.did.base.datamodel.data.DidAuth;
import org.omnione.did.base.datamodel.data.E2e;
import org.omnione.did.base.datamodel.data.EcdhReqData;
import org.omnione.did.base.datamodel.data.Proof;
import org.omnione.did.base.datamodel.data.Provider;
import org.omnione.did.base.datamodel.data.ServerTokenSeed;
import org.omnione.did.base.datamodel.data.SignedWalletInfo;
import org.omnione.did.base.datamodel.data.Wallet;
import org.omnione.did.base.datamodel.enums.EccCurveType;
import org.omnione.did.base.datamodel.enums.ProofPurpose;
import org.omnione.did.base.datamodel.enums.ProofType;
import org.omnione.did.base.datamodel.enums.ServerTokenPurpose;
import org.omnione.did.base.datamodel.enums.SymmetricCipherType;
import org.omnione.did.base.datamodel.enums.SymmetricPaddingType;
import org.omnione.did.wallet.key.WalletManagerInterface;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClientResponseException;

import java.nio.charset.StandardCharsets;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Flow B(issue-vc) 오케스트레이션 — 실 VC 발급(선택과제1). Flow A 완주(wallet ASSIGNED + TAS User row)가 전제.
 * offer → propose → request-ecdh → request-create-token(purpose=ISSUE_VC) → request-issue-profile
 * → request-issue-vc → confirm-issue-vc. 응답 e2e.encVc 를 홀더 1회성 E2E 키로 복호해 실 VC 를 얻는다.
 *
 * <p><b>두 DID 매핑</b>(Flow A 산출):
 * <ul>
 *   <li><b>USER DID</b>(did:omn:fmu...) = ecdh.client(B3, User 조회키·앵커됨) + didAuth.did(B6). USER 지갑 keyagree/auth 서명.</li>
 *   <li><b>WALLET DID</b>(did:omn:fm...) = SignedWalletInfo.wallet.did(B4, ASSIGNED 지갑 행). WALLET 지갑 #assert 서명.</li>
 * </ul>
 * caAppInfo 는 Flow A 와 동일하게 did:omn:cas #assert 실서명({@link CasKeyService}).
 *
 * <p>E2E 채널은 <b>발급자</b> profile.process.reqE2e(AES-256-CBC/PKCS5) 기준이며 TAS ECDH 세션과 무관하다.
 * 홀더 1회성 EcKeyPair 를 만들어 encReqVc 암호화 + 응답 encVc 복호화에 재사용한다(IssueVCTests.decode 미러).
 */
@Service
public class IssueVcService {

    private static final Logger log = LoggerFactory.getLogger(IssueVcService.class);
    private static final DateTimeFormatter TS = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'");

    // MDL(기존, non-ZKP)
    private static final String MDL_PLAN = "vcplanid000000000001";
    private static final String MDL_SCHEMA = "mdl";
    private static final String MDL_NS = "org.iso.18013.5.1";
    // FaceLicense(선택과제1 커스텀 VC) — scripts/issuer-provision-facelicense.sh 로 프로비저닝.
    // vc_plan_id 컬럼 = varchar(20) → plan 은 정확히 20자.
    private static final String FL_PLAN = "vcplanface0000000001";
    private static final String FL_SCHEMA = "facelicense";
    private static final String FL_NS = "kr.wearless.facelicense";

    private static final String ISSUER_DID = "did:omn:issuer";
    private static final String CAS_DID = "did:omn:cas";

    private final TasClient tas;
    private final HolderWalletService wallets;
    private final CasKeyService casKey;
    private final RegisterUserService registerUser;
    private final IssuerAdminClient issuerAdmin;
    private final KycSetupService kycSetup;
    private final ObjectMapper om = new ObjectMapper();

    public IssueVcService(TasClient tas, HolderWalletService wallets, CasKeyService casKey,
                          RegisterUserService registerUser, IssuerAdminClient issuerAdmin,
                          KycSetupService kycSetup) {
        this.tas = tas;
        this.wallets = wallets;
        this.casKey = casKey;
        this.registerUser = registerUser;
        this.issuerAdmin = issuerAdmin;
        this.kycSetup = kycSetup;
    }

    public record IssueResult(String vcId, String issuer, String txId, JsonNode vc, String status,
                              String note, String userDid) {}

    /** 발급 플랜 해소 결과: vcPlanId(B1/B2/B4) + vcSchemaId(user upsert) + userInfo(user.data). */
    private record ResolvedPlan(String vcPlanId, String vcSchemaId, Map<String, String> userInfo, String label) {}

    /** 본문 없이 호출(백워드 호환) = MDL. */
    public IssueResult issue(String modelId) throws Exception {
        return issue(modelId, null);
    }

    public IssueResult issue(String modelId, IssueVcDtos.IssueRequest request) throws Exception {
        ResolvedPlan plan = resolvePlan(request);
        if (!wallets.exists(modelId)) {
            throw new IllegalStateException("no wallet for model " + modelId + " (run POST /holder/models/{id}/wallet first)");
        }
        // 전제: Flow A 완주(wallet ASSIGNED + TAS User row). 미완주면 register-did 를 먼저 실행한다.
        if (!wallets.isFlowAComplete(modelId)) {
            log.info("Flow A not complete for model {} — running register-did before issue-vc", modelId);
            RegisterUserService.RegisterResult reg = registerUser.register(modelId);
            if (!reg.flowAComplete()) {
                return new IssueResult(null, ISSUER_DID, reg.txId(), null, "flow_a_incomplete",
                        "Flow B(issue-vc) requires Flow A complete (wallet ASSIGNED + TAS User row). "
                      + "register-did returned status=" + reg.status() + " note=" + reg.note(),
                        wallets.readUserDid(modelId));
            }
        }

        String walletDid = wallets.readDid(modelId);
        String userDid = wallets.readUserDid(modelId);
        String walletId = wallets.walletId(modelId);

        // 발급 전 모델별 Issuer user upsert(멱등). ISSUER_INIT 플랜은 발급 시 (pii, vcSchemaId)로 user 를
        // 조회해 credentialSubject 를 채운다 → 임의 모델도 수동 프로비저닝 없이 동작. pii = TAS user.pii.
        upsertIssuerUser(modelId, userDid, plan);

        WalletManagerInterface userWallet = wallets.connectUser(modelId);
        WalletManagerInterface walletWallet = wallets.connect(modelId);
        try {
            // B1 offer-issue-vc/qr — vcPlanId 로 offerId 획득.
            String offerId = tas.offerIssueVcQr(new IssueVcDtos.OfferReq(TasClient.newId(), plan.vcPlanId(), ISSUER_DID)).offerId();
            log.info("B1 offer-issue-vc OK model={} plan={}({}) offerId={}", modelId, plan.label(), plan.vcPlanId(), offerId);

            // B2 propose-issue-vc — txId + refId(encReqVc 검증키).
            IssueVcDtos.ProposeRes prop =
                    tas.proposeIssueVc(new IssueVcDtos.ProposeReq(TasClient.newId(), plan.vcPlanId(), ISSUER_DID, offerId));
            String txId = prop.txId();
            String refId = prop.refId();
            log.info("B2 propose-issue-vc OK model={} txId={} refId={}", modelId, txId, refId);

            // B3 request-ecdh — client=USER DID(User 조회키), USER 지갑 keyagree 서명. 세션키 유도.
            byte[] clientNonce = HolderCrypto.nonce16();
            EcdhReqData reqEcdh = buildEcdhReq(userWallet, userDid, clientNonce);
            AccEcdh acc = tas.requestEcdh(new RegisterUserDtos.EcdhReq(TasClient.newId(), txId, reqEcdh)).accEcdh();
            byte[] sessionKey = HolderCrypto.deriveSessionKey(
                    userWallet, "keyagree", acc.getPublicKey(),
                    clientNonce, HolderCrypto.decode(acc.getServerNonce()), acc.getCipher());
            log.info("B3 request-ecdh OK model={} (USER DID {})", modelId, userDid);

            // B4 request-create-token(purpose=ISSUE_VC) — walletInfo=WALLET DID #assert(ASSIGNED 행), caAppInfo=CAS #assert.
            ServerTokenSeed seed = buildTokenSeed(walletWallet, walletDid, walletId);
            RegisterUserDtos.CreateTokenRes tok =
                    tas.requestCreateToken(new RegisterUserDtos.CreateTokenReq(TasClient.newId(), txId, seed));
            String serverToken = HolderCrypto.serverTokenFromEncStd(
                    tok.encStd(), tok.iv(), sessionKey, acc.getCipher(), acc.getPadding());
            log.info("B4 request-create-token(ISSUE_VC) OK model={} serverToken derived", modelId);

            // B5 request-issue-profile — 원문 JSON 파싱: authNonce, profile.id, process.{issuerNonce,reqE2e}.
            String profileJson = tas.requestIssueProfile(new IssueVcDtos.ProfileReq(TasClient.newId(), txId, serverToken));
            JsonNode root = om.readTree(profileJson);
            String authNonce = text(root, "authNonce");
            JsonNode profile = root.get("profile");
            String profileId = text(profile, "id");
            JsonNode process = profile.get("profile").get("process");
            String issuerNonce = text(process, "issuerNonce");
            JsonNode reqE2e = process.get("reqE2e");
            EccCurveType curve = curveOf(text(reqE2e, "curve"));
            SymmetricCipherType cipher = SymmetricCipherType.fromDisplayName(text(reqE2e, "cipher"));
            SymmetricPaddingType padding = SymmetricPaddingType.fromDisplayName(text(reqE2e, "padding"));
            byte[] issuerPub = HolderCrypto.decode(text(reqE2e, "publicKey"));
            byte[] e2eNonce = HolderCrypto.decode(text(reqE2e, "nonce"));
            log.info("B5 request-issue-profile OK model={} profileId={} cipher={}/{} curve={}",
                    modelId, profileId, cipher, padding, curve);

            // B6 request-issue-vc — 1회성 E2E 키로 encReqVc 암호화 + didAuth(USER #auth, authNonce echo).
            HolderCrypto.E2eEphemeral eph = HolderCrypto.generateE2eEphemeral(curve);
            byte[] iv = HolderCrypto.initialVector();
            byte[] e2eKey = HolderCrypto.e2eSessionKey(issuerPub, eph.privateKeyPkcs8(), e2eNonce, curve, cipher);

            IssueVcDtos.ReqVc reqVc = new IssueVcDtos.ReqVc(refId, new IssueVcDtos.ProfileInfo(profileId, issuerNonce));
            String reqVcJson = om.writeValueAsString(reqVc);
            String encReqVc = HolderCrypto.encode(HolderCrypto.aesEncrypt(reqVcJson, e2eKey, iv, cipher, padding));

            AccE2e accE2e = AccE2e.builder()
                    .publicKey(HolderCrypto.encode(eph.compressedPublicKey()))
                    .iv(HolderCrypto.encode(iv))
                    .build();                                   // proof 생략(발급자 검증 optional, 홀더 1회성키 미앵커)

            DidAuth didAuth = buildDidAuth(userWallet, userDid, authNonce);

            IssueVcDtos.IssueVcRes vcRes = tas.requestIssueVc(
                    new IssueVcDtos.IssueVcReq(TasClient.newId(), txId, serverToken, didAuth, accE2e, encReqVc));
            E2e e2e = vcRes.e2e();
            log.info("B6 request-issue-vc OK model={} — e2e returned (iv+encVc)", modelId);

            // 복호 — 같은 E2E 키, iv=응답 e2e.iv. 페이로드 = CredentialInfo{vc, credential} 래퍼.
            byte[] vcPlain = HolderCrypto.aesDecrypt(
                    HolderCrypto.decode(e2e.getEncVc()), e2eKey, HolderCrypto.decode(e2e.getIv()), cipher, padding);
            String decrypted = new String(vcPlain, StandardCharsets.UTF_8);
            JsonNode decoded = om.readTree(decrypted);          // 복호 성공 = 유효 JSON (garbage 아님)
            JsonNode vcNode = decoded.has("vc") ? decoded.get("vc") : decoded;
            String vcId = text(vcNode, "id");
            log.info("VC decrypted model={} vcId={} issuer={}", modelId, vcId, issuerText(vcNode));

            // B7 confirm-issue-vc — vcId = 복호 VC 의 root id.
            tas.confirmIssueVc(new IssueVcDtos.ConfirmReq(TasClient.newId(), txId, serverToken, vcId));
            log.info("B7 confirm-issue-vc OK model={} txId={} — Flow B COMPLETE", modelId, txId);

            String note = "Flow B complete. Real " + plan.label() + " VC (plan=" + plan.vcPlanId()
                    + ", schema=" + plan.vcSchemaId() + ") issued by " + ISSUER_DID + " to USER DID " + userDid
                    + " (WALLET DID " + walletDid + " signed create-token). E2E decrypt succeeded (AES-256-CBC/PKCS5).";
            return new IssueResult(vcId, issuerText(vcNode), txId, vcNode, "issued", note, userDid);
        } catch (RestClientResponseException e) {
            log.error("issue-vc FAILED model={} status={} body={}", modelId, e.getStatusCode(), e.getResponseBodyAsString());
            throw e;
        } finally {
            userWallet.disConnect();
            walletWallet.disConnect();
        }
    }

    // ── plan/claims 해소 + Issuer user upsert ──────────────────────────────

    /**
     * 요청 본문 → 발급 플랜 해소. 본문 없음/plan 생략/"mdl" → MDL(데모 기본 claim). "facelicense" →
     * FaceLicense(요청 claims 를 namespace claim id 로 매핑). userInfo 키 = "namespaceId.claimId".
     */
    private ResolvedPlan resolvePlan(IssueVcDtos.IssueRequest request) {
        String plan = (request == null || request.plan() == null) ? "mdl"
                : request.plan().trim().toLowerCase();

        if ("facelicense".equals(plan)) {
            IssueVcDtos.Claims c = request.claims();
            if (c == null) {
                throw new IllegalArgumentException("plan=facelicense requires a claims object");
            }
            Map<String, String> ui = new LinkedHashMap<>();
            putClaim(ui, FL_NS + ".allowed_use", c.allowedUse());
            putClaim(ui, FL_NS + ".forbidden_use", c.forbiddenUse());
            putClaim(ui, FL_NS + ".unit_price", c.unitPrice() == null ? null : String.valueOf(c.unitPrice()));
            putClaim(ui, FL_NS + ".license_valid_until", c.licenseValidUntil());
            putClaim(ui, FL_NS + ".face_image_digest", c.faceImageDigest());
            putClaim(ui, FL_NS + ".model_name", c.modelName());
            return new ResolvedPlan(FL_PLAN, FL_SCHEMA, ui, "FaceLicense");
        }
        if (!"mdl".equals(plan)) {
            throw new IllegalArgumentException("unknown plan '" + plan + "' (expected 'mdl' or 'facelicense')");
        }
        // MDL — 기존 동작(데모 placeholder claim). Issuer user 는 이 값으로 멱등 upsert.
        Map<String, String> ui = new LinkedHashMap<>();
        ui.put(MDL_NS + ".family_name", "WEARLESS");
        ui.put(MDL_NS + ".given_name", "Model");
        ui.put(MDL_NS + ".birth_date", "2000-01-01");
        return new ResolvedPlan(MDL_PLAN, MDL_SCHEMA, ui, "MDL");
    }

    private static void putClaim(Map<String, String> map, String key, String value) {
        if (value != null) map.put(key, value);
    }

    /**
     * 발급 전 Issuer user upsert(POST /users/demo, 멱등). did=USER DID, pii=TAS user.pii(SHA256(
     * "fm-holder-&lt;modelId&gt;")), vcSchemaId=플랜 스키마, userInfo=claim JSON. ISSUER_INIT 발급이
     * (pii, vcSchemaId)로 조회하므로 pii 일치 필수.
     */
    private void upsertIssuerUser(String modelId, String userDid, ResolvedPlan plan) throws Exception {
        String pii = kycSetup.modelPii(modelId);
        String userInfoJson = om.writeValueAsString(plan.userInfo());
        issuerAdmin.upsertDemoUser(new IssuerAdminClient.DemoUserReq(userDid, pii, plan.vcSchemaId(), userInfoJson));
        log.info("Issuer user upsert OK model={} plan={} userDid={} vcSchema={} claims={}",
                modelId, plan.label(), userDid, plan.vcSchemaId(), plan.userInfo().keySet());
    }

    // ── helpers (Flow A RegisterUserService 미러) ──────────────────────────

    private static String now() {
        return TS.format(ZonedDateTime.now(ZoneOffset.UTC));
    }

    private static Proof proof(String did, String keyId, ProofPurpose purpose) {
        Proof p = new Proof();
        p.setType(ProofType.SECP_256R1_SIGNATURE_2018);
        p.setCreated(now());
        p.setVerificationMethod(did + "?versionId=1#" + keyId);
        p.setProofPurpose(purpose);
        return p;
    }

    /** B3 reqEcdh — client=USER DID, USER 지갑 keyagree 서명. */
    private static EcdhReqData buildEcdhReq(WalletManagerInterface wallet, String did, byte[] clientNonce)
            throws Exception {
        Proof p = proof(did, "keyagree", ProofPurpose.KEY_AGREEMENT);
        EcdhReqData req = new EcdhReqData();
        req.setClient(did);
        req.setClientNonce(HolderCrypto.encode(clientNonce));
        req.setCurve(EccCurveType.SECP_256_R1);
        req.setPublicKey(wallet.getPublicKey("keyagree"));
        req.setProof(p);
        HolderCrypto.sign(req, p, wallet, "keyagree");
        return req;
    }

    /** B6 didAuth — did=USER DID, USER 지갑 #auth 서명, authNonce echo(B5). */
    private static DidAuth buildDidAuth(WalletManagerInterface userWallet, String userDid, String authNonce) {
        Proof p = proof(userDid, "auth", ProofPurpose.AUTHENTICATION);
        DidAuth da = DidAuth.builder().did(userDid).authNonce(authNonce).proof(p).build();
        HolderCrypto.sign(da, p, userWallet, "auth");
        return da;
    }

    /**
     * B4 create-token seed — purpose=ISSUE_VC. walletInfo=WALLET DID #assert(ASSIGNED 행 조회키),
     * caAppInfo=did:omn:cas #assert 실서명(Flow A 와 동일 레시피).
     */
    private ServerTokenSeed buildTokenSeed(WalletManagerInterface walletWallet, String walletDid, String walletId) {
        Wallet w = new Wallet();
        w.setId(walletId);
        w.setDid(walletDid);
        Proof wp = proof(walletDid, "assert", ProofPurpose.ASSERTION_METHOD);
        SignedWalletInfo swi = new SignedWalletInfo();
        swi.setWallet(w);
        swi.setNonce(HolderCrypto.encode(HolderCrypto.nonce16()));
        swi.setProof(wp);
        HolderCrypto.sign(swi, wp, walletWallet, "assert");

        Provider prov = new Provider();
        prov.setDid(CAS_DID);
        prov.setCertVcRef("http://localhost:8094/cas/api/v1/certificate-vc");
        Proof ap = proof(CAS_DID, "assert", ProofPurpose.ASSERTION_METHOD);
        AttestedAppInfo cai = new AttestedAppInfo();
        cai.setAppId("fm-holder-app");
        cai.setProvider(prov);
        cai.setNonce(HolderCrypto.encode(HolderCrypto.nonce16()));
        cai.setProof(ap);
        casKey.sign(cai, ap, "assert");

        ServerTokenSeed seed = new ServerTokenSeed();
        seed.setPurpose(ServerTokenPurpose.ISSUE_VC);
        seed.setWalletInfo(swi);
        seed.setCaAppInfo(cai);
        return seed;
    }

    private static EccCurveType curveOf(String displayName) {
        return "Secp256k1".equals(displayName) ? EccCurveType.SECP_256_K1 : EccCurveType.SECP_256_R1;
    }

    private static String text(JsonNode node, String field) {
        JsonNode v = node == null ? null : node.get(field);
        if (v == null || v.isNull()) {
            throw new IllegalStateException("missing field '" + field + "' in issue-vc response");
        }
        return v.asText();
    }

    /** VC issuer 는 문자열 또는 {id,...} 객체일 수 있다 — 진단/리포트용 표시값. */
    private static String issuerText(JsonNode vcNode) {
        JsonNode iss = vcNode == null ? null : vcNode.get("issuer");
        if (iss == null || iss.isNull()) return null;
        return iss.isObject() ? (iss.has("id") ? iss.get("id").asText() : iss.toString()) : iss.asText();
    }
}
