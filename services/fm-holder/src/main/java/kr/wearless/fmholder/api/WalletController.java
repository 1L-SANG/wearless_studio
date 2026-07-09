package kr.wearless.fmholder.api;

import kr.wearless.fmholder.wallet.HolderWalletService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * 커스터디얼 홀더 월렛/DID API. Python 백엔드(FastAPI)가 호출한다.
 *
 * <ul>
 *   <li>POST /holder/models/{modelId}/wallet — 월렛 + DID 생성(멱등: 이미 있으면 409)</li>
 *   <li>GET  /holder/models/{modelId} — 저장된 DID 조회</li>
 * </ul>
 */
@RestController
@RequestMapping("/holder/models")
public class WalletController {

    private final HolderWalletService walletService;

    public WalletController(HolderWalletService walletService) {
        this.walletService = walletService;
    }

    @PostMapping("/{modelId}/wallet")
    public ResponseEntity<?> createWallet(@PathVariable String modelId) throws Exception {
        if (walletService.exists(modelId)) {
            return ResponseEntity.status(HttpStatus.CONFLICT)
                    .body(Map.of("error", "wallet_exists", "modelId", modelId,
                            "did", String.valueOf(walletService.readDid(modelId))));
        }
        HolderWalletService.WalletResult r = walletService.createWallet(modelId);
        return ResponseEntity.status(HttpStatus.CREATED).body(r);
    }

    @GetMapping("/{modelId}")
    public ResponseEntity<?> getModel(@PathVariable String modelId) throws Exception {
        if (!walletService.exists(modelId)) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                    .body(Map.of("error", "not_found", "modelId", modelId));
        }
        return ResponseEntity.ok(Map.of(
                "modelId", modelId,
                "did", String.valueOf(walletService.readDid(modelId)),
                "hasWallet", true));
    }
}
