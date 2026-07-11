package kr.wearless.fmholder.protocol;

import kr.wearless.fmholder.wallet.HolderWalletService;
import org.omnione.did.wallet.key.WalletManagerInterface;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;

/**
 * 홀더 DID 온체인 앵커 — chicken-egg 우회.
 *
 * <p>request-ecdh 등 프로토콜 스텝은 클라 DID 를 온체인에서 조회해 검증하므로 신규 홀더 DID 는
 * 먼저 온체인에 있어야 한다. TAS 관리자 부트스트랩 경로로 직접 앵커한다:
 * <ol>
 *   <li>홀더 DID doc 자기서명(HolderDidDoc) → multibase 인코딩</li>
 *   <li>register-did/public — 엔티티로 저장</li>
 *   <li>entities/list — entityId 조회</li>
 *   <li>approve-did — 승인 = storageService.registerDidDoc 온체인 앵커</li>
 * </ol>
 * (dev 스택 TAS 는 permitAll 이라 관리자 인증 불필요. 앵커된 DID 는 이후 issue-vc 의 ecdh/didAuth 에서 해소된다.)
 */
@Service
public class DidAnchorService {

    private static final Logger log = LoggerFactory.getLogger(DidAnchorService.class);

    private final TasAdminClient admin;
    private final HolderWalletService wallets;

    public DidAnchorService(TasAdminClient admin, HolderWalletService wallets) {
        this.admin = admin;
        this.wallets = wallets;
    }

    public record AnchorResult(String did, Long entityId, String status) {}

    public AnchorResult anchor(String modelId) throws Exception {
        WalletManagerInterface wallet = wallets.connect(modelId);
        try {
            String did = wallets.readDid(modelId);
            // 1. 자기서명 DID doc → multibase
            String signedDidDocJson = HolderDidDoc.selfSign(wallet, wallets.didDocJson(modelId));
            String didDocMultibase = HolderCrypto.encode(signedDidDocJson.getBytes(StandardCharsets.UTF_8));

            // 2. 이미 앵커됐으면 재사용
            TasAdminClient.EntityInfo existing = admin.findEntityByDid(did);
            if (existing != null) {
                log.info("DID already registered as entity {} (status={})", existing.id(), existing.status());
                if (!"COMPLETED".equals(existing.status()) && !"CERTIFICATE_VC_REQUIRED".equals(existing.status())) {
                    admin.approveDid(existing.id());
                }
                return new AnchorResult(did, existing.id(), "anchored");
            }

            // 3. register-did/public (엔티티 저장) — 이름은 모델별 고유
            String name = "fm-holder-" + wallets.walletId(modelId);
            admin.registerDidPublic(new TasAdminClient.RegisterDidReq(
                    didDocMultibase, name, "WALLET_PROVIDER",
                    "http://localhost:8100/holder", "http://localhost:8100/holder/cert-vc"));

            // 4. entityId 조회 → 5. approve-did (온체인 앵커)
            TasAdminClient.EntityInfo entity = admin.findEntityByDid(did);
            if (entity == null) throw new IllegalStateException("entity not found after register-did for " + did);
            admin.approveDid(entity.id());

            return new AnchorResult(did, entity.id(), "anchored");
        } finally {
            wallet.disConnect();
        }
    }
}
