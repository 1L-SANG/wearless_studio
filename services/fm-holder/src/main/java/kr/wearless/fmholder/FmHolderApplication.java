package kr.wearless.fmholder;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.openfeign.EnableFeignClients;

/**
 * FaceMarket 커스터디얼 홀더 서비스 (MSA).
 *
 * OpenDID 서버 SDK(did-wallet-sdk-server·did-core-sdk-server·did-crypto-sdk-server)로
 * 모델별 월렛·DID를 서버가 보유(커스터디얼)하고, Issuer(P210) 발급의 홀더 4단계를 대행한다.
 * Python 백엔드(FastAPI)가 REST로 이 서비스를 호출한다.
 */
@SpringBootApplication
@EnableFeignClients
public class FmHolderApplication {
    public static void main(String[] args) {
        SpringApplication.run(FmHolderApplication.class, args);
    }
}
